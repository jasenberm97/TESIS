"""
Operaciones del módulo de Medicina.

Las reglas de negocio viven aquí para que todas las pantallas las compartan.
Cada operación se ejecuta en una transacción: si algo falla, no se guarda nada.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from citas import servicios as servicios_citas
from citas.models import Cita, EstadoCita
from usuarios.comun import AREA_MEDICINA
from django.utils import timezone

from .models import ConsultaMedica, NotaAclaratoria


def medico_de(usuario):
    """Devuelve el registro de Personal del médico, o 403 si el usuario no es de Medicina."""
    persona = getattr(usuario, "persona", None)
    personal = getattr(persona, "personal", None) if persona else None
    if personal is None or personal.area.nombre != AREA_MEDICINA:
        raise PermissionDenied("Solo el personal de Medicina registra consultas médicas.")
    return personal


# =====================================================================
# Registrar consultas
# =====================================================================
def registrar_consulta_con_cita(consulta, cita_id, usuario):
    """
    Registra la consulta de una cita y la marca como Atendida en la misma transacción.
    `consulta` es una ConsultaMedica sin guardar (viene del formulario).
    Si la cita ya estaba Atendida (de antes de este módulo) y no tiene consulta, solo se vincula.
    """
    medico = medico_de(usuario)

    with transaction.atomic():
        try:
            cita = Cita.objects.select_for_update().select_related("estado", "horario").get(pk=cita_id)
        except Cita.DoesNotExist:
            raise ValidationError("La cita no existe.")

        if cita.horario.personal_id != medico.pk:
            raise PermissionDenied("Esta cita pertenece a otro profesional.")
        if ConsultaMedica.objects.filter(cita_id=cita.pk).exists():
            raise ValidationError("Esta cita ya tiene una consulta registrada.")

        if cita.estado.es_activo:
            # Misma regla que Mi agenda: solo el personal del área y desde la hora del turno.
            cita = servicios_citas.registrar_asistencia(cita.pk, usuario, asistio=True)
        elif cita.estado.nombre != EstadoCita.ATENDIDA:
            raise ValidationError(
                f"No se puede registrar una consulta en una cita {cita.estado.nombre.lower()}."
            )

        consulta.cita = cita
        consulta.estudiante_id = cita.estudiante_id
        consulta.personal = medico
        consulta.tipo = ConsultaMedica.Tipo.PROGRAMADA
        consulta.save()  # full_clean: área, estudiante y médico de la cita, presión, fecha
    return consulta


def registrar_consulta_sin_cita(consulta, estudiante, usuario, cita_a_cancelar=None):
    """
    Atención sin turno (espontánea) o de emergencia. No se vincula a ninguna cita.
    Si el médico lo indica, en la misma transacción se cancela la cita de Medicina
    que el estudiante tenía pendiente (porque ya fue atendido). Devuelve (consulta, cita_cancelada).
    """
    medico = medico_de(usuario)
    if consulta.tipo not in (ConsultaMedica.Tipo.ESPONTANEA, ConsultaMedica.Tipo.EMERGENCIA):
        raise ValidationError({"tipo": "Elija si es una atención sin cita o una emergencia."})

    cancelada = None
    with transaction.atomic():
        consulta.estudiante = estudiante
        consulta.personal = medico
        consulta.cita = None
        consulta.save()

        if cita_a_cancelar is not None:
            cita = Cita.objects.select_for_update().select_related("estado").get(pk=cita_a_cancelar.pk)
            # Solo si sigue vigente y es de este estudiante; si ya no lo está, no hay nada que cancelar.
            if cita.estudiante_id == estudiante.pk and cita.estado.es_activo:
                fecha = timezone.localtime(consulta.fecha_atencion)
                motivo = f"Fue atendido antes, sin cita, el {fecha:%d/%m/%Y}."
                cancelada = servicios_citas.cancelar_cita(cita.pk, usuario, motivo)

    return consulta, cancelada


# =====================================================================
# Correcciones: editar el mismo día o agregar notas aclaratorias
# =====================================================================
def editar_consulta(consulta, usuario):
    """Solo quien registró la consulta y solo el mismo día del registro."""
    medico = medico_de(usuario)
    with transaction.atomic():
        actual = ConsultaMedica.objects.select_for_update().get(pk=consulta.pk)
        if actual.personal_id != medico.pk:
            raise PermissionDenied("Solo quien registró la consulta puede editarla.")
        if not actual.es_editable:
            raise ValidationError(
                "La consulta solo se edita el mismo día en que se registró. Agrega una nota aclaratoria."
            )
        consulta.save()
    return consulta


def agregar_nota(consulta_id, usuario, texto):
    """Nota aclaratoria sobre una consulta ya cerrada. No se edita ni se borra."""
    medico = medico_de(usuario)
    texto = (texto or "").strip()
    if not texto:
        raise ValidationError({"texto": "Escriba la nota aclaratoria."})

    with transaction.atomic():
        try:
            consulta = ConsultaMedica.objects.select_for_update().get(pk=consulta_id)
        except ConsultaMedica.DoesNotExist:
            raise ValidationError("La consulta no existe.")
        if consulta.es_editable:
            raise ValidationError("Hoy todavía puedes editar la consulta directamente.")
        nota = NotaAclaratoria.objects.create(consulta_medica=consulta, texto=texto, registrada_por=medico)
    return nota

def cancelar_cita_por_atencion_previa(cita_id, consulta, usuario):
    """
    Cancela una cita de Medicina que ya no hace falta porque el estudiante
    fue atendido antes, sin cita. El motivo se escribe automáticamente.
    """
    medico_de(usuario)
    try:
        cita_id = int(cita_id)
    except (TypeError, ValueError):
        raise ValidationError("La cita no es válida.")

    with transaction.atomic():
        cita = (
            Cita.objects.select_for_update()
            .select_related("estado", "horario")
            .filter(pk=cita_id)
            .first()
        )
        if cita is None or cita.estudiante_id != consulta.estudiante_id:
            raise ValidationError("La cita no pertenece a este estudiante.")
        if not cita.estado.es_activo:
            raise ValidationError("Esa cita ya no está vigente.")
        if consulta.tipo == ConsultaMedica.Tipo.PROGRAMADA or cita.horario.inicio <= consulta.fecha_atencion:
            raise ValidationError("Esa cita no es posterior a una atención sin cita.")

        fecha = timezone.localtime(consulta.fecha_atencion)
        motivo = f"Fue atendido antes, sin cita, el {fecha:%d/%m/%Y}."
        return servicios_citas.cancelar_cita(cita.pk, usuario, motivo)