"""
Operaciones del módulo de citas.

Todas las pantallas (estudiante, personal y administrador) usan estas
funciones, para que las reglas de negocio estén en un solo lugar.
Cada operación se ejecuta en una transacción y bloquea las filas que
modifica (select_for_update) para evitar reservas simultáneas.
"""
from datetime import datetime, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from estudiantes.models import Estudiante
from usuarios.grupos import GRUPO_ADMINISTRADOR
from usuarios.models import Personal, Usuario
from usuarios.permisos import tiene_perfil

from .models import (
    HORAS_MINIMAS_CANCELACION, Ausencia, Cita, EstadoCita, Feriado, Horario,
    ausencia_del_turno, feriado_del_turno,
)

# Reglas de reserva para el estudiante
MINUTOS_ANTICIPACION_RESERVA = 60   # no se reserva un turno que empieza en menos de 1 hora
DIAS_MAXIMOS_RESERVA = 30           # no se reserva más allá de 30 días
# =====================================================================
# Ayudantes de permisos
# =====================================================================
def es_administrador(usuario):
    return tiene_perfil(usuario, GRUPO_ADMINISTRADOR)


def area_del_personal(usuario):
    """Id del área del usuario si es personal de Bienestar; si no, None."""
    if usuario.rol != Usuario.Rol.PERSONAL or not usuario.persona_id:
        return None
    personal = getattr(usuario.persona, "personal", None)
    return personal.area_id if personal else None


def puede_gestionar_cita(usuario, cita):
    """Administrador: todas las citas. Personal: las citas de su área."""
    if es_administrador(usuario):
        return True
    area = area_del_personal(usuario)
    return area is not None and area == cita.horario.personal.area_id


# =====================================================================
# Consultas
# =====================================================================
def turnos_disponibles(area=None):
    """Turnos futuros, de profesionales activos, sin cita activa, sin ausencia y que no son feriado."""
    ahora = timezone.localtime()
    cita_activa = Cita.objects.filter(horario=OuterRef("pk"), estado__es_activo=True)
    turnos = (
        Horario.objects
        .filter(personal__fecha_salida__isnull=True, personal__persona__usuario__is_active=True)
        .filter(Q(fecha__gt=ahora.date()) | Q(fecha=ahora.date(), hora_inicio__gt=ahora.time()))
        .exclude(Exists(cita_activa))
        .exclude(Exists(ausencia_del_turno()))
        .exclude(Exists(feriado_del_turno()))
        .select_related("personal__persona", "personal__area")
    )
    if area is not None:
        turnos = turnos.filter(personal__area=area)
    return turnos

def _corte_reserva():
    return timezone.localtime() + timedelta(minutes=MINUTOS_ANTICIPACION_RESERVA)


def limite_reserva():
    """Última fecha en la que un estudiante puede reservar."""
    return timezone.localdate() + timedelta(days=DIAS_MAXIMOS_RESERVA)


def turnos_reservables(area=None):
    """Turnos disponibles que además cumplen la anticipación mínima y el máximo de días."""
    corte = _corte_reserva()
    return (
        turnos_disponibles(area)
        .filter(Q(fecha__gt=corte.date()) | Q(fecha=corte.date(), hora_inicio__gte=corte.time()))
        .filter(fecha__lte=limite_reserva())
        .order_by("fecha", "hora_inicio")
    )


def _validar_ventana_reserva(horario):
    if horario.inicio < _corte_reserva():
        raise ValidationError(
            {"horario": f"Los turnos se reservan con al menos {MINUTOS_ANTICIPACION_RESERVA} minutos de anticipación."}
        )
    if horario.fecha > limite_reserva():
        raise ValidationError(
            {"horario": f"Solo se puede reservar con un máximo de {DIAS_MAXIMOS_RESERVA} días de anticipación."}
        )

def citas_activas_futuras(estudiante):
    ahora = timezone.localtime()
    return (
        Cita.objects
        .filter(estudiante=estudiante, estado__es_activo=True)
        .filter(Q(horario__fecha__gt=ahora.date())
                | Q(horario__fecha=ahora.date(), horario__hora_inicio__gt=ahora.time()))
        .select_related("estado", "horario__personal__persona", "horario__personal__area")
        .order_by("horario__fecha", "horario__hora_inicio")
    )


def proxima_cita(estudiante):
    return citas_activas_futuras(estudiante).first()


def citas_afectadas_por_ausencia(personal, fecha_desde, fecha_hasta, hora_desde=None, hora_hasta=None):
    """Citas activas del profesional que caen dentro de la ausencia (para mostrarlas antes de confirmar)."""
    citas = Cita.objects.filter(
        estado__es_activo=True,
        horario__personal=personal,
        horario__fecha__range=(fecha_desde, fecha_hasta),
    )
    if hora_desde is not None and hora_hasta is not None:
        citas = citas.filter(horario__hora_inicio__lt=hora_hasta, horario__hora_fin__gt=hora_desde)
    return citas.select_related("estudiante__persona", "horario").order_by("horario__fecha", "horario__hora_inicio")


def citas_afectadas_por_feriado(fecha):
    """Citas activas de cualquier profesional en ese día."""
    return (
        Cita.objects
        .filter(estado__es_activo=True, horario__fecha=fecha)
        .select_related("estudiante__persona", "horario__personal__persona", "horario__personal__area")
        .order_by("horario__hora_inicio", "horario__personal__area__nombre")
    )


# =====================================================================
# Ayudantes internos
# =====================================================================
def _bloquear_cita(cita_id):
    try:
        return Cita.objects.select_for_update().get(pk=cita_id)
    except Cita.DoesNotExist:
        raise ValidationError("La cita no existe.")


def _bloquear_horario(horario_id):
    try:
        return Horario.objects.select_for_update().get(pk=horario_id)
    except Horario.DoesNotExist:
        raise ValidationError("El turno seleccionado ya no existe.")


def _dar_de_baja(cita, nombre_estado, usuario, motivo, ausencia=None, feriado=None):
    """Pasa la cita a Cancelada o Reprogramada dejando constancia de quién, cuándo y por qué."""
    cita.estado = EstadoCita.obtener(nombre_estado)
    cita.motivo_cancelacion = (motivo or "").strip()[:255]
    cita.cancelada_por = usuario
    cita.fecha_cancelacion = timezone.now()
    cita.ausencia = ausencia
    cita.feriado = feriado
    cita.save()


# =====================================================================
# Operaciones sobre citas
# =====================================================================
def agendar_cita(estudiante, horario_id, motivo):
    """El estudiante reserva un turno libre. La cita queda Pendiente."""
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "Indique el motivo de la cita."})

    with transaction.atomic():
        # Primero los bloqueos; después las validaciones (que hace cita.save()).
        Estudiante.objects.select_for_update().get(pk=estudiante.pk)
        horario = _bloquear_horario(horario_id)
        _validar_ventana_reserva(horario)
        cita = Cita(
            estudiante=estudiante,
            horario=horario,
            estado=EstadoCita.obtener(EstadoCita.PENDIENTE),
            motivo=motivo,
        )
        cita.save()  # full_clean: futuro, profesional activo, sin feriado ni ausencia, libre, una por área
    return cita

def cancelar_cita(cita_id, usuario, motivo):
    """
    Estudiante: solo sus citas y hasta 24 h antes.
    Personal (de su área) y administrador: en cualquier momento.
    """
    with transaction.atomic():
        cita = _bloquear_cita(cita_id)

        if usuario.rol == Usuario.Rol.ESTUDIANTE:
            if cita.estudiante.persona_id != usuario.persona_id:
                raise PermissionDenied("No puedes cancelar citas de otra persona.")
            if not cita.estado.es_activo:
                raise ValidationError("Esta cita ya no está vigente.")
            if not cita.puede_cancelar_estudiante():
                raise ValidationError(
                    f"Solo puedes cancelar hasta {HORAS_MINIMAS_CANCELACION} horas antes del turno. "
                    "Comunícate con Bienestar Universitario."
                )
        else:
            if not puede_gestionar_cita(usuario, cita):
                raise PermissionDenied("No puedes gestionar citas de otra área.")
            if not cita.estado.es_activo:
                raise ValidationError("Esta cita ya no está vigente.")

        _dar_de_baja(cita, EstadoCita.CANCELADA, usuario, motivo)
    return cita


def reprogramar_cita(cita_id, nuevo_horario_id, usuario, motivo):
    """
    Solo personal (de su área) y administrador. La cita original queda
    Reprogramada y se crea una nueva cita Pendiente en el turno nuevo,
    enlazada con cita_origen. El nuevo turno debe ser de la misma área.
    """
    with transaction.atomic():
        cita = _bloquear_cita(cita_id)
        if not puede_gestionar_cita(usuario, cita):
            raise PermissionDenied("No puedes reprogramar esta cita.")
        if not cita.estado.es_activo:
            raise ValidationError("Esta cita ya no está vigente.")
        if int(nuevo_horario_id) == cita.horario_id:
            raise ValidationError({"horario": "Elija un turno distinto al actual."})

        nuevo = _bloquear_horario(nuevo_horario_id)
        if nuevo.personal.area_id != cita.horario.personal.area_id:
            raise ValidationError({"horario": "El nuevo turno debe ser de la misma área."})

        # Primero se libera la cita original (turno y cupo del área)…
        _dar_de_baja(cita, EstadoCita.REPROGRAMADA, usuario, motivo)
        # …y después se crea la nueva. Si esto falla, se revierte todo.
        nueva = Cita(
            estudiante_id=cita.estudiante_id,
            horario=nuevo,
            estado=EstadoCita.obtener(EstadoCita.PENDIENTE),
            motivo=cita.motivo,
            cita_origen=cita,
        )
        nueva.save()
    return nueva


def registrar_asistencia(cita_id, usuario, asistio):
    """Solo el personal del área, desde la hora del turno: Atendida o No asistió."""
    with transaction.atomic():
        cita = _bloquear_cita(cita_id)
        if area_del_personal(usuario) != cita.horario.personal.area_id:
            raise PermissionDenied("Solo el personal del área registra la asistencia.")
        if not cita.estado.es_activo:
            raise ValidationError("Esta cita ya no está vigente.")
        if not cita.horario.ya_paso:
            raise ValidationError("La asistencia se registra a partir de la hora del turno.")

        cita.estado = EstadoCita.obtener(EstadoCita.ATENDIDA if asistio else EstadoCita.NO_ASISTIO)
        cita.save()
    return cita


# =====================================================================
# Horarios (administrador)
# =====================================================================
def generar_turnos(personal, fecha_desde, fecha_hasta, dias, franjas, duracion):
    """
    Crea turnos consecutivos de `duracion` minutos para el profesional, en los
    días de la semana indicados (0 = lunes … 4 = viernes) y dentro de cada
    franja [(inicio, fin), …] (p. ej. mañana y tarde, con pausa de almuerzo).
    Se omiten los turnos que se crucen con otros, que ya pasaron, que caen en
    una ausencia o en un feriado. Devuelve (creados, omitidos).
    """
    if not personal.esta_activo:
        raise ValidationError("No se pueden crear turnos para un profesional inactivo.")

    paso = timedelta(minutes=duracion)
    ahora = timezone.localtime()

    with transaction.atomic():
        # Evita que dos administradores generen a la vez para el mismo profesional.
        Personal.objects.select_for_update().get(pk=personal.pk)

        existentes = {}
        for fecha, ini, fin in (
            Horario.objects
            .filter(personal=personal, fecha__range=(fecha_desde, fecha_hasta))
            .values_list("fecha", "hora_inicio", "hora_fin")
        ):
            existentes.setdefault(fecha, []).append((ini, fin))

        ausencias = list(
            Ausencia.objects.filter(personal=personal, fecha_desde__lte=fecha_hasta, fecha_hasta__gte=fecha_desde)
        )
        feriados = set(
            Feriado.objects.filter(fecha__range=(fecha_desde, fecha_hasta)).values_list("fecha", flat=True)
        )

        nuevos, omitidos = [], 0
        fecha = fecha_desde
        while fecha <= fecha_hasta:
            if fecha.weekday() in dias:
                for franja_inicio, franja_fin in franjas:
                    inicio = datetime.combine(fecha, franja_inicio)
                    limite = datetime.combine(fecha, franja_fin)
                    while inicio + paso <= limite:
                        ini, fin = inicio.time(), (inicio + paso).time()
                        ya_paso = fecha == ahora.date() and ini <= ahora.time()
                        cruza = any(ini < f and fin > i for i, f in existentes.get(fecha, []))
                        ausente = any(a.cubre(fecha, ini, fin) for a in ausencias)
                        if ya_paso or cruza or ausente or fecha in feriados:
                            omitidos += 1
                        else:
                            nuevos.append(Horario(personal=personal, fecha=fecha, hora_inicio=ini, hora_fin=fin))
                        inicio += paso
            fecha += timedelta(days=1)

        # Las reglas de Horario.clean() ya se verificaron arriba (días, franja,
        # cruces, feriados, profesional activo); bulk_create inserta todo en una sola consulta.
        Horario.objects.bulk_create(nuevos)

    return len(nuevos), omitidos


def quitar_turnos_libres(personal, fecha_desde, fecha_hasta):
    """
    Elimina los turnos futuros del profesional en el rango que NUNCA tuvieron citas.
    Sirve para corregir una generación equivocada. Devuelve cuántos se eliminaron.
    """
    ahora = timezone.localtime()
    turnos = (
        Horario.objects
        .filter(personal=personal, fecha__range=(fecha_desde, fecha_hasta))
        .filter(Q(fecha__gt=ahora.date()) | Q(fecha=ahora.date(), hora_inicio__gt=ahora.time()))
        .exclude(Exists(Cita.objects.filter(horario=OuterRef("pk"))))
    )
    eliminados, _ = turnos.delete()
    return eliminados


# =====================================================================
# Ausencias (administrador)
# =====================================================================
def registrar_ausencia(personal, fecha_desde, fecha_hasta, hora_desde, hora_hasta, motivo, usuario):
    """
    Registra la ausencia y cancela las citas activas que caen en ella. Los turnos
    no se borran: quedan bloqueados mientras exista la ausencia.
    Devuelve (ausencia, cantidad_de_citas_canceladas).
    """
    if not es_administrador(usuario):
        raise PermissionDenied("Solo el administrador registra ausencias.")

    with transaction.atomic():
        Personal.objects.select_for_update().get(pk=personal.pk)
        # Se bloquean los turnos del período para que nadie agende mientras se registra.
        list(Horario.objects.select_for_update()
             .filter(personal=personal, fecha__range=(fecha_desde, fecha_hasta))
             .values_list("pk", flat=True))

        ausencia = Ausencia(
            personal=personal,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            hora_desde=hora_desde,
            hora_hasta=hora_hasta,
            motivo=(motivo or "").strip(),
            registrada_por=usuario,
        )
        ausencia.save()

        ids = list(
            citas_afectadas_por_ausencia(personal, fecha_desde, fecha_hasta, hora_desde, hora_hasta)
            .values_list("pk", flat=True)
        )
        texto = f"Ausencia del profesional: {ausencia.motivo}"
        for cita in Cita.objects.select_for_update().filter(pk__in=ids):
            _dar_de_baja(cita, EstadoCita.CANCELADA, usuario, texto, ausencia=ausencia)

    return ausencia, len(ids)


def eliminar_ausencia(ausencia_id, usuario):
    """Los turnos vuelven a quedar libres; las citas canceladas NO se restauran."""
    if not es_administrador(usuario):
        raise PermissionDenied("Solo el administrador elimina ausencias.")
    with transaction.atomic():
        try:
            ausencia = Ausencia.objects.select_for_update().get(pk=ausencia_id)
        except Ausencia.DoesNotExist:
            raise ValidationError("La ausencia no existe.")
        ausencia.delete()


# =====================================================================
# Feriados (administrador)
# =====================================================================
def registrar_feriado(fecha, nombre, usuario):
    """
    Registra el feriado para todo Bienestar y cancela las citas activas de ese día.
    Devuelve (feriado, cantidad_de_citas_canceladas).
    """
    if not es_administrador(usuario):
        raise PermissionDenied("Solo el administrador registra feriados.")

    with transaction.atomic():
        # Se bloquean los turnos del día para que nadie agende mientras se registra.
        list(Horario.objects.select_for_update().filter(fecha=fecha).values_list("pk", flat=True))

        feriado = Feriado(fecha=fecha, nombre=(nombre or "").strip(), registrado_por=usuario)
        feriado.save()

        ids = list(citas_afectadas_por_feriado(fecha).values_list("pk", flat=True))
        texto = f"Feriado: {feriado.nombre}"
        for cita in Cita.objects.select_for_update().filter(pk__in=ids):
            _dar_de_baja(cita, EstadoCita.CANCELADA, usuario, texto, feriado=feriado)

    return feriado, len(ids)


def eliminar_feriado(feriado_id, usuario):
    """Los turnos de ese día vuelven a quedar libres; las citas canceladas NO se restauran."""
    if not es_administrador(usuario):
        raise PermissionDenied("Solo el administrador elimina feriados.")
    with transaction.atomic():
        try:
            feriado = Feriado.objects.select_for_update().get(pk=feriado_id)
        except Feriado.DoesNotExist:
            raise ValidationError("El feriado no existe.")
        feriado.delete()