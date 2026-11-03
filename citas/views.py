from datetime import date, datetime, time, timedelta
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from usuarios.grupos import GRUPO_ADMINISTRADOR, GRUPO_ESTUDIANTE, GRUPOS_POR_AREA
from usuarios.models import Area, Personal
from usuarios.permisos import perfil_requerido
from usuarios.comun import AREA_MEDICINA
from medicina.models import ConsultaMedica

from . import servicios
from .forms import (
    AgendarCitaForm, AusenciaForm, CancelarCitaForm, EditarHorarioForm, FeriadoForm, GenerarTurnosForm,
    MotivoForm, ReprogramarCitaForm, personal_activo,
)
from .models import (
    Ausencia, Cita, EstadoCita, Feriado, Horario, ausencia_del_turno, feriado_del_turno,
)

# Grupos del personal de atención (Medicina, Odontología, Psicología).
GRUPOS_PERSONAL = tuple(GRUPOS_POR_AREA.values()) if hasattr(GRUPOS_POR_AREA, "values") else tuple(GRUPOS_POR_AREA)
ESTADOS_DE_BAJA = (EstadoCita.CANCELADA, EstadoCita.REPROGRAMADA)
# =====================================================================
# Ayudantes
# =====================================================================
def _fecha(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


def _lunes(valor=None):
    """Lunes de la semana pedida. Sin fecha: esta semana, o la próxima si hoy es fin de semana."""
    fecha = _fecha(valor)
    if fecha is None:
        fecha = timezone.localdate()
        if fecha.weekday() >= 5:
            fecha += timedelta(days=7 - fecha.weekday())
    return fecha - timedelta(days=fecha.weekday())


def _url_calendario(personal_id, fecha=None):
    url = reverse("citas:horarios_calendario", args=[personal_id])
    return f"{url}?semana={fecha:%Y-%m-%d}" if fecha else url


# =====================================================================
# Horarios (administrador): tarjetas por profesional
# =====================================================================
@perfil_requerido(GRUPO_ADMINISTRADOR)
def lista_profesionales(request):
    lunes = _lunes()
    viernes = lunes + timedelta(days=4)
    hoy = timezone.localdate()
    en_semana = Q(horarios__fecha__range=(lunes, viernes))

    profesionales = (
        Personal.objects
        .select_related("persona__usuario", "area")
        .annotate(
            turnos_semana=Count("horarios", filter=en_semana, distinct=True),
            citas_semana=Count(
                "horarios", filter=en_semana & Q(horarios__citas__estado__es_activo=True), distinct=True,
            ),
            turnos_futuros=Count("horarios", filter=Q(horarios__fecha__gte=hoy), distinct=True),
        )
        .prefetch_related(Prefetch(
            "ausencias",
            queryset=Ausencia.objects.filter(fecha_hasta__gte=hoy).order_by("fecha_desde"),
            to_attr="ausencias_vigentes",
        ))
        .order_by("area__nombre", "persona__apellidos", "persona__nombres")
    )
    activos = [p for p in profesionales if p.esta_activo]
    inactivos = [p for p in profesionales if not p.esta_activo]

    return render(request, "citas/horarios_profesionales.html", {
        "activos": activos,
        "inactivos": inactivos,
        "lunes": lunes,
        "viernes": viernes,
        "hoy": hoy,
    })


# =====================================================================
# Calendario semanal de un profesional
# =====================================================================
@perfil_requerido(GRUPO_ADMINISTRADOR)
def calendario_profesional(request, pk):
    personal = get_object_or_404(Personal.objects.select_related("persona__usuario", "area"), pk=pk)
    lunes = _lunes(request.GET.get("semana"))
    viernes = lunes + timedelta(days=4)
    hoy = timezone.localdate()

    turnos = (
        Horario.objects
        .filter(personal=personal, fecha__range=(lunes, viernes))
        .annotate(
            bloqueado=Exists(ausencia_del_turno()),
            en_feriado=Exists(feriado_del_turno()),
            tiene_citas=Exists(Cita.objects.filter(horario=OuterRef("pk"))),
        )
        .prefetch_related(Prefetch(
            "citas",
            queryset=Cita.objects.filter(estado__es_activo=True).select_related("estudiante__persona"),
            to_attr="citas_activas",
        ))
        .order_by("fecha", "hora_inicio")
    )
    ausencias = list(
        Ausencia.objects
        .filter(personal=personal, fecha_desde__lte=viernes, fecha_hasta__gte=lunes)
        .select_related("registrada_por__persona")
        .order_by("fecha_desde", "hora_desde")
    )
    feriados = {f.fecha: f for f in Feriado.objects.filter(fecha__range=(lunes, viernes))}

    por_fecha = {}
    for h in turnos:
        por_fecha.setdefault(h.fecha, []).append(h)

    resumen = {"total": 0, "citas": 0, "libres": 0, "bloqueados": 0}
    dias = []
    for i in range(5):
        fecha = lunes + timedelta(days=i)
        items, anterior = [], None
        for h in por_fecha.get(fecha, []):
            if anterior and h.hora_inicio > anterior.hora_fin:
                items.append({"tipo": "pausa", "desde": anterior.hora_fin, "hasta": h.hora_inicio})

            cita = h.citas_activas[0] if h.citas_activas else None
            if cita:
                estado = "cita"
                resumen["citas"] += 1
            elif h.en_feriado:
                estado = "feriado"
                resumen["bloqueados"] += 1
            elif h.bloqueado:
                estado = "ausencia"
                resumen["bloqueados"] += 1
            elif h.ya_paso:
                estado = "pasado"
            else:
                estado = "libre"
                resumen["libres"] += 1
            resumen["total"] += 1

            items.append({
                "tipo": "turno",
                "h": h,
                "estado": estado,
                "cita": cita,
                "editable": estado == "libre" and not h.tiene_citas,
            })
            anterior = h

        dias.append({
            "fecha": fecha,
            "es_hoy": fecha == hoy,
            "items": items,
            "feriado": feriados.get(fecha),
            "ausencias": [a for a in ausencias if a.fecha_desde <= fecha <= a.fecha_hasta],
        })

    return render(request, "citas/horarios_calendario.html", {
        "personal": personal,
        "activo": personal.esta_activo,
        "dias": dias,
        "ausencias": ausencias,
        "resumen": resumen,
        "lunes": lunes,
        "viernes": viernes,
        "semana_actual": _lunes(),
        "semana_anterior": lunes - timedelta(days=7),
        "semana_siguiente": lunes + timedelta(days=7),
    })


# =====================================================================
# Generar turnos
# =====================================================================
@perfil_requerido(GRUPO_ADMINISTRADOR)
def generar_turnos(request):
    if request.method == "POST":
        form = GenerarTurnosForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                creados, omitidos = servicios.generar_turnos(
                    personal=d["personal"],
                    fecha_desde=d["fecha_desde"],
                    fecha_hasta=d["fecha_hasta"],
                    dias=d["dias"],
                    franjas=d["franjas"],
                    duracion=d["duracion"],
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                nombre = d["personal"].persona.nombre_completo
                if creados:
                    texto = f"Se crearon {creados} turnos para {nombre}."
                    if omitidos:
                        texto += (f" Se omitieron {omitidos} porque ya existían, se cruzaban, "
                                  "ya pasaron o coinciden con una ausencia.")
                    messages.success(request, texto)
                else:
                    messages.warning(
                        request,
                        "No se creó ningún turno: todos ya existían, se cruzaban, ya pasaron "
                        "o coinciden con una ausencia.",
                    )
                return redirect(_url_calendario(d["personal"].pk, d["fecha_desde"]))
        personal_id = request.POST.get("personal")
    else:
        personal_id = request.GET.get("personal")
        inicial = {"personal": int(personal_id)} if personal_id and personal_id.isdigit() else {}
        form = GenerarTurnosForm(initial=inicial)

    volver = (_url_calendario(int(personal_id)) if personal_id and personal_id.isdigit()
              else reverse("citas:horarios"))
    return render(request, "citas/horarios_generar.html", {"form": form, "volver": volver})


@perfil_requerido(GRUPO_ADMINISTRADOR)
@require_POST
def quitar_turnos_libres(request, pk):
    personal = get_object_or_404(Personal, pk=pk)
    desde, hasta = _fecha(request.POST.get("desde")), _fecha(request.POST.get("hasta"))
    if not desde or not hasta or hasta < desde:
        messages.error(request, "Revisa el rango de fechas.")
        return redirect(_url_calendario(pk, desde))

    eliminados = servicios.quitar_turnos_libres(personal, desde, hasta)
    if eliminados:
        messages.success(
            request,
            f"Se quitaron {eliminados} turnos libres. Los turnos con citas o ya pasados se conservaron.",
        )
    else:
        messages.warning(request, "No había turnos libres para quitar en ese rango.")
    return redirect(_url_calendario(pk, desde))


# =====================================================================
# Editar y eliminar un turno
# =====================================================================
def _turno_modificable(request, horario):
    if horario.citas.exists():
        messages.error(
            request,
            "Este turno tiene citas registradas y se conserva por el historial. "
            "Si la cita está vigente, reprográmala o cancélala.",
        )
        return False
    if horario.ya_paso:
        messages.error(request, "No se pueden modificar turnos que ya pasaron.")
        return False
    return True


@perfil_requerido(GRUPO_ADMINISTRADOR)
def editar_horario(request, pk):
    horario = get_object_or_404(Horario.objects.select_related("personal__persona", "personal__area"), pk=pk)
    volver = _url_calendario(horario.personal_id, horario.fecha)
    if not _turno_modificable(request, horario):
        return redirect(volver)

    original = {"fecha": horario.fecha, "inicio": horario.hora_inicio, "fin": horario.hora_fin}
    form = EditarHorarioForm(request.POST or None, instance=horario)
    if request.method == "POST" and form.is_valid():
        turno = form.save()
        messages.success(
            request,
            f"Turno actualizado: {turno.fecha:%d/%m/%Y}, {turno.hora_inicio:%H:%M} – {turno.hora_fin:%H:%M}.",
        )
        return redirect(_url_calendario(turno.personal_id, turno.fecha))

    return render(request, "citas/horario_editar.html", {
        "form": form,
        "horario": horario,
        "original": original,
        "volver": volver,
    })


@perfil_requerido(GRUPO_ADMINISTRADOR)
@require_POST
def eliminar_horario(request, pk):
    horario = get_object_or_404(Horario, pk=pk)
    volver = _url_calendario(horario.personal_id, horario.fecha)
    if _turno_modificable(request, horario):
        horario.delete()
        messages.success(request, "Turno eliminado.")
    return redirect(volver)


# =====================================================================
# Ausencias
# =====================================================================
@perfil_requerido(GRUPO_ADMINISTRADOR)
def registrar_ausencia(request, pk):
    personal = get_object_or_404(Personal.objects.select_related("persona__usuario", "area"), pk=pk)
    afectadas = None
    vista_previa = None

    if request.method == "POST":
        form = AusenciaForm(request.POST)
        if form.is_valid() and "modificar" not in request.POST:
            d = form.cleaned_data
            if request.POST.get("confirmar") == "1":
                try:
                    ausencia, canceladas = servicios.registrar_ausencia(
                        personal=personal,
                        fecha_desde=d["fecha_desde"],
                        fecha_hasta=d["fecha_hasta"],
                        hora_desde=d["hora_desde"],
                        hora_hasta=d["hora_hasta"],
                        motivo=d["motivo"],
                        usuario=request.user,
                    )
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    texto = "Ausencia registrada."
                    if canceladas:
                        texto += (f" Se cancelaron {canceladas} citas; los estudiantes verán la "
                                  "cancelación y el motivo en «Mis citas».")
                    messages.success(request, texto)
                    return redirect(_url_calendario(personal.pk, d["fecha_desde"]))
            else:
                # Primera pasada: mostrar las citas que se cancelarían antes de confirmar.
                afectadas = list(servicios.citas_afectadas_por_ausencia(
                    personal, d["fecha_desde"], d["fecha_hasta"], d["hora_desde"], d["hora_hasta"],
                ))
                vista_previa = Ausencia(
                    personal=personal,
                    fecha_desde=d["fecha_desde"], fecha_hasta=d["fecha_hasta"],
                    hora_desde=d["hora_desde"], hora_hasta=d["hora_hasta"],
                    motivo=d["motivo"],
                )
    else:
        form = AusenciaForm()

    return render(request, "citas/ausencia_form.html", {
        "form": form,
        "personal": personal,
        "afectadas": afectadas,
        "vista_previa": vista_previa,
        "volver": _url_calendario(personal.pk),
    })


@perfil_requerido(GRUPO_ADMINISTRADOR)
@require_POST
def eliminar_ausencia(request, pk):
    ausencia = get_object_or_404(Ausencia, pk=pk)
    personal_id, fecha = ausencia.personal_id, ausencia.fecha_desde
    try:
        servicios.eliminar_ausencia(pk, request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            "Ausencia eliminada. Los turnos vuelven a estar disponibles; "
            "las citas que se habían cancelado no se restauran.",
        )
    return redirect(_url_calendario(personal_id, _fecha(request.POST.get("semana")) or fecha))

# =====================================================================
# Feriados
# =====================================================================
def _url_feriados(anio):
    return f"{reverse('citas:horarios_feriados')}?anio={anio}"


@perfil_requerido(GRUPO_ADMINISTRADOR)
def lista_feriados(request):
    hoy = timezone.localdate()
    valor = request.GET.get("anio", "")
    anio = int(valor) if valor.isdigit() and 2000 <= int(valor) <= 2100 else hoy.year

    feriados = (
        Feriado.objects
        .filter(fecha__year=anio)
        .select_related("registrado_por__persona")
        .annotate(canceladas=Count("citas_canceladas"))
        .order_by("fecha")
    )
    return render(request, "citas/feriados_lista.html", {
        "feriados": feriados,
        "anio": anio,
        "anio_anterior": anio - 1,
        "anio_siguiente": anio + 1,
        "hoy": hoy,
    })


@perfil_requerido(GRUPO_ADMINISTRADOR)
def registrar_feriado(request):
    afectadas = None

    if request.method == "POST":
        form = FeriadoForm(request.POST)
        if form.is_valid() and "modificar" not in request.POST:
            d = form.cleaned_data
            if request.POST.get("confirmar") == "1":
                try:
                    feriado, canceladas = servicios.registrar_feriado(d["fecha"], d["nombre"], request.user)
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    texto = f"Feriado registrado: {feriado.nombre} ({feriado.fecha:%d/%m/%Y})."
                    if canceladas:
                        texto += f" Se cancelaron {canceladas} citas de ese día."
                    messages.success(request, texto)
                    return redirect(_url_feriados(feriado.fecha.year))
            else:
                afectadas = list(servicios.citas_afectadas_por_feriado(d["fecha"]))
    else:
        form = FeriadoForm()

    return render(request, "citas/feriado_form.html", {"form": form, "afectadas": afectadas})


@perfil_requerido(GRUPO_ADMINISTRADOR)
@require_POST
def eliminar_feriado(request, pk):
    feriado = get_object_or_404(Feriado, pk=pk)
    anio = feriado.fecha.year
    try:
        servicios.eliminar_feriado(pk, request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            "Feriado eliminado. Los turnos de ese día vuelven a estar disponibles; "
            "las citas que se habían cancelado no se restauran.",
        )
    return redirect(_url_feriados(anio))

# =====================================================================
# Agendar cita (estudiante)
# =====================================================================
def _estudiante_actual(request):
    persona = request.user.persona
    estudiante = getattr(persona, "estudiante", None) if persona else None
    if estudiante is None:
        raise PermissionDenied("Tu cuenta no tiene datos de estudiante.")
    return estudiante


def _cita_activa_en_area(estudiante, area_id):
    return (
        Cita.objects
        .filter(estudiante=estudiante, estado__es_activo=True, horario__personal__area_id=area_id)
        .select_related("horario__personal__persona", "horario__personal__area")
        .first()
    )


@perfil_requerido(GRUPO_ESTUDIANTE)
def agendar_area(request):
    estudiante = _estudiante_actual(request)

    activas = {
        c.horario.personal.area_id: c
        for c in Cita.objects
        .filter(estudiante=estudiante, estado__es_activo=True)
        .select_related("horario__personal__area", "horario__personal__persona")
    }
    libres = {
        fila["personal__area"]: fila["n"]
        for fila in servicios.turnos_reservables()
        .order_by().values("personal__area").annotate(n=Count("pk"))
    }
    profesionales = {}
    for p in personal_activo():
        profesionales.setdefault(p.area_id, []).append(p)

    opciones = [
        {
            "area": area,
            "cita": activas.get(area.pk),
            "libres": libres.get(area.pk, 0),
            "profesionales": profesionales.get(area.pk, []),
        }
        for area in Area.objects.order_by("nombre")
    ]
    return render(request, "citas/agendar_area.html", {
        "opciones": opciones,
        "horas_cancelacion": servicios.HORAS_MINIMAS_CANCELACION,
    })


@perfil_requerido(GRUPO_ESTUDIANTE)
def agendar_turnos(request, area_id):
    estudiante = _estudiante_actual(request)
    area = get_object_or_404(Area, pk=area_id)

    cita = _cita_activa_en_area(estudiante, area.pk)
    if cita:
        messages.info(
            request,
            f"Ya tienes una cita activa en {area}: {cita.horario.fecha:%d/%m/%Y} a las "
            f"{cita.horario.hora_inicio:%H:%M}. Para elegir otro horario, primero cancélala.",
        )
        return redirect("citas:agendar")

    limite = servicios.limite_reserva()
    primera_semana = _lunes()
    ultima_semana = limite - timedelta(days=limite.weekday())
    lunes = min(max(_lunes(request.GET.get("semana")), primera_semana), ultima_semana)
    viernes = lunes + timedelta(days=4)
    hoy = timezone.localdate()

    turnos = list(servicios.turnos_reservables(area).filter(fecha__range=(lunes, viernes)))
    por_fecha = {}
    for h in turnos:
        por_fecha.setdefault(h.fecha, []).append(h)
    feriados = {f.fecha: f for f in Feriado.objects.filter(fecha__range=(lunes, viernes))}

    dias = []
    for i in range(5):
        fecha = lunes + timedelta(days=i)
        dias.append({
            "fecha": fecha,
            "turnos": por_fecha.get(fecha, []),
            "feriado": feriados.get(fecha),
            "pasado": fecha < hoy,
            "fuera": fecha > limite,
        })

    proximo = None if turnos else servicios.turnos_reservables(area).first()

    return render(request, "citas/agendar_turnos.html", {
        "area": area,
        "profesionales": list(personal_activo().filter(area=area)),
        "dias": dias,
        "total": len(turnos),
        "proximo": proximo,
        "lunes": lunes,
        "viernes": viernes,
        "semana_anterior": lunes - timedelta(days=7) if lunes > primera_semana else None,
        "semana_siguiente": lunes + timedelta(days=7) if lunes < ultima_semana else None,
        "limite": limite,
    })


@perfil_requerido(GRUPO_ESTUDIANTE)
def agendar_confirmar(request, horario_id):
    estudiante = _estudiante_actual(request)
    horario = get_object_or_404(
        Horario.objects.select_related("personal__persona", "personal__area"), pk=horario_id,
    )
    area = horario.personal.area
    volver = f"{reverse('citas:agendar_turnos', args=[area.pk])}?semana={horario.fecha:%Y-%m-%d}"

    if _cita_activa_en_area(estudiante, area.pk):
        messages.info(request, f"Ya tienes una cita activa en {area}.")
        return redirect("citas:agendar")

    if not servicios.turnos_reservables(area).filter(pk=horario.pk).exists():
        messages.error(request, "Ese turno ya no está disponible. Elige otro horario.")
        return redirect(volver)

    form = AgendarCitaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            cita = servicios.agendar_cita(estudiante, horario.pk, form.cleaned_data["motivo"])
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return redirect(volver)
        request.session["cita_agendada_id"] = cita.pk
        return redirect("citas:agendar_exito")

    return render(request, "citas/agendar_confirmar.html", {
        "horario": horario,
        "form": form,
        "volver": volver,
        "horas_cancelacion": servicios.HORAS_MINIMAS_CANCELACION,
    })


@never_cache
@perfil_requerido(GRUPO_ESTUDIANTE)
def agendar_exito(request):
    estudiante = _estudiante_actual(request)
    cita_id = request.session.pop("cita_agendada_id", None)
    cita = (
        Cita.objects
        .select_related("horario__personal__persona", "horario__personal__area")
        .filter(pk=cita_id, estudiante=estudiante)
        .first()
    ) if cita_id else None
    if cita is None:
        return redirect("citas:agendar")
    return render(request, "citas/agendar_exito.html", {"cita": cita})

# =====================================================================
# Mis citas (estudiante)
# =====================================================================
@perfil_requerido(GRUPO_ESTUDIANTE)
def mis_citas(request):
    estudiante = _estudiante_actual(request)
    proximas = list(servicios.citas_activas_futuras(estudiante))

    historial = (
        Cita.objects
        .filter(estudiante=estudiante)
        .exclude(pk__in=[c.pk for c in proximas])
        .select_related("estado", "horario__personal__persona", "horario__personal__area")
        .prefetch_related("reprogramaciones__horario")
        .order_by("-horario__fecha", "-horario__hora_inicio")
    )
    pagina = Paginator(historial, 10).get_page(request.GET.get("page"))

    return render(request, "citas/mis_citas.html", {
        "proximas": proximas,
        "page_obj": pagina,
        "horas_cancelacion": servicios.HORAS_MINIMAS_CANCELACION,
    })


@perfil_requerido(GRUPO_ESTUDIANTE)
def cancelar_mi_cita(request, pk):
    estudiante = _estudiante_actual(request)
    cita = get_object_or_404(
        Cita.objects.select_related("estado", "horario__personal__persona", "horario__personal__area"),
        pk=pk, estudiante=estudiante,   # una cita de otro estudiante da 404
    )

    if not cita.puede_cancelar_estudiante():
        messages.error(
            request,
            f"Esta cita ya no se puede cancelar en línea (solo hasta {servicios.HORAS_MINIMAS_CANCELACION} "
            "horas antes). Comunícate con Bienestar Universitario.",
        )
        return redirect("citas:mis_citas")

    form = CancelarCitaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            servicios.cancelar_cita(cita.pk, request.user, form.cleaned_data["motivo"])
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, "Tu cita fue cancelada. El turno quedó libre para otro estudiante.")
        return redirect("citas:mis_citas")

    return render(request, "citas/cancelar_cita.html", {"cita": cita, "form": form})

# =====================================================================
# Mi agenda (personal de atención)
# =====================================================================
def _personal_actual(request):
    persona = request.user.persona
    personal = getattr(persona, "personal", None) if persona else None
    if personal is None:
        raise PermissionDenied("Tu cuenta no tiene datos de personal de atención.")
    return personal


def _volver(request, defecto):
    """Dirección a la que se regresa después de una acción (solo del mismo sitio)."""
    url = request.POST.get("volver") or request.GET.get("volver")
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return url
    return defecto


def _dia_habil(fecha, paso):
    """Siguiente (paso=1) o anterior (paso=-1) día de lunes a viernes."""
    fecha += timedelta(days=paso)
    while fecha.weekday() >= 5:
        fecha += timedelta(days=paso)
    return fecha

def _marcar_atencion_previa(citas):
    """
    Marca en cada cita la última atención SIN cita del estudiante anterior a su turno
    (últimos 30 días), para recordarle al médico que esa cita quizá ya no hace falta.
    """
    if not citas:
        return
    desde = min(c.horario.fecha for c in citas) - timedelta(days=30)
    inicio = timezone.make_aware(datetime.combine(desde, time.min))
    atenciones = {}
    for consulta in (
        ConsultaMedica.objects
        .filter(estudiante_id__in={c.estudiante_id for c in citas}, cita__isnull=True, fecha_atencion__gte=inicio)
        .order_by("fecha_atencion")
    ):
        atenciones.setdefault(consulta.estudiante_id, []).append(consulta)
    for c in citas:
        previas = [a for a in atenciones.get(c.estudiante_id, []) if a.fecha_atencion < c.horario.inicio]
        c.atencion_previa = previas[-1] if previas else None

@perfil_requerido(*GRUPOS_PERSONAL)
def agenda(request):
    personal = _personal_actual(request)
    es_medicina = personal.area.nombre == AREA_MEDICINA
    hoy = timezone.localdate()
    dia = _fecha(request.GET.get("dia"))
    if dia is None:
        dia = hoy if hoy.weekday() < 5 else _dia_habil(hoy, 1)
    lunes = dia - timedelta(days=dia.weekday())
    viernes = lunes + timedelta(days=4)

    vigentes = Cita.objects.exclude(estado__nombre__in=ESTADOS_DE_BAJA)

    # Franja de la semana: cuántas citas hay cada día
    conteo = {
        fila["horario__fecha"]: fila["n"]
        for fila in vigentes
        .filter(horario__personal=personal, horario__fecha__range=(lunes, viernes))
        .order_by().values("horario__fecha").annotate(n=Count("pk"))
    }
    semana = []
    for i in range(5):
        fecha = lunes + timedelta(days=i)
        semana.append({"fecha": fecha, "citas": conteo.get(fecha, 0), "es_hoy": fecha == hoy, "elegido": fecha == dia})

    # Turnos del día
    turnos = (
        Horario.objects
        .filter(personal=personal, fecha=dia)
        .annotate(bloqueado=Exists(ausencia_del_turno()), en_feriado=Exists(feriado_del_turno()))
        .prefetch_related(Prefetch(
            "citas",
            queryset=vigentes.select_related(
                "estado", "estudiante__persona__usuario", "estudiante__carrera", "consulta_medica",
            ),
            to_attr="citas_vigentes",
        ))
        .order_by("hora_inicio")
    )
    items, anterior = [], None
    resumen = {"citas": 0, "atendidas": 0, "por_atender": 0, "libres": 0}
    for h in turnos:
        if anterior and h.hora_inicio > anterior.hora_fin:
            items.append({"tipo": "pausa", "desde": anterior.hora_fin, "hasta": h.hora_inicio})
        cita = h.citas_vigentes[0] if h.citas_vigentes else None
        if cita:
            resumen["citas"] += 1
            if cita.estado.nombre == EstadoCita.ATENDIDA:
                resumen["atendidas"] += 1
            elif cita.estado.es_activo:
                resumen["por_atender"] += 1
        elif not (h.bloqueado or h.en_feriado or h.ya_paso):
            resumen["libres"] += 1
        items.append({
            "tipo": "turno",
            "h": h,
            "cita": cita,
            "bloqueado": h.bloqueado or h.en_feriado,
            "empezo": h.ya_paso,
        })
        anterior = h

    # Citas de días anteriores que quedaron sin registrar
    sin_registrar = list(
        Cita.objects
        .filter(horario__personal=personal, estado__es_activo=True, horario__fecha__lt=hoy)
        .select_related("estudiante__persona", "horario")
        .order_by("horario__fecha", "horario__hora_inicio")
    )

    if es_medicina:
        _marcar_atencion_previa(
            [i["cita"] for i in items if i.get("cita") and i["cita"].estado.es_activo] + sin_registrar
        )
    
    return render(request, "citas/agenda.html", {
        "personal": personal,
        "es_medicina": es_medicina,
        "dia": dia,
        "hoy": hoy,
        "dia_anterior": _dia_habil(dia, -1),
        "dia_siguiente": _dia_habil(dia, 1),
        "semana": semana,
        "items": items,
        "resumen": resumen,
        "sin_registrar": sin_registrar,
        "feriado": Feriado.objects.filter(fecha=dia).first(),
        "ausencias": list(Ausencia.objects.filter(personal=personal, fecha_desde__lte=dia, fecha_hasta__gte=dia)),
        "volver": request.get_full_path(),
    })


@perfil_requerido(*GRUPOS_PERSONAL)
@require_POST
def agenda_asistencia(request, pk):
    asistio = request.POST.get("asistio") == "1"
    try:
        cita = servicios.registrar_asistencia(pk, request.user, asistio)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        nombre = cita.estudiante.persona.nombre_completo
        messages.success(request, f"{nombre}: {'atendida' if asistio else 'no asistió'}.")
    return redirect(_volver(request, reverse("citas:agenda")))


def _cita_gestionable(request, pk):
    cita = get_object_or_404(
        Cita.objects.select_related(
            "estado", "estudiante__persona", "horario__personal__persona", "horario__personal__area",
        ),
        pk=pk,
    )
    if not servicios.puede_gestionar_cita(request.user, cita):
        raise PermissionDenied("No puedes gestionar citas de otra área.")
    return cita


def _volver_por_defecto(request):
    return reverse("citas:agenda") if servicios.area_del_personal(request.user) else reverse("citas:gestion_citas")

@perfil_requerido(GRUPO_ADMINISTRADOR, *GRUPOS_PERSONAL)
def agenda_cancelar(request, pk):
    cita = _cita_gestionable(request, pk)
    volver = _volver(request, _volver_por_defecto(request))
    if not cita.estado.es_activo:
        messages.error(request, "Esta cita ya no está vigente.")
        return redirect(volver)

    form = MotivoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            servicios.cancelar_cita(cita.pk, request.user, form.cleaned_data["motivo"])
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, "Cita cancelada. El estudiante verá el motivo en «Mis citas».")
        return redirect(volver)

    return render(request, "citas/cita_cancelar.html", {"cita": cita, "form": form, "volver": volver})


@perfil_requerido(GRUPO_ADMINISTRADOR, *GRUPOS_PERSONAL)
def agenda_reprogramar(request, pk):
    cita = _cita_gestionable(request, pk)
    volver = _volver(request, _volver_por_defecto(request))
    if not cita.estado.es_activo:
        messages.error(request, "Esta cita ya no está vigente.")
        return redirect(volver)

    turnos = (
        servicios.turnos_disponibles(cita.horario.personal.area)
        .exclude(pk=cita.horario_id)
        .filter(fecha__lte=timezone.localdate() + timedelta(days=60))
        .order_by("fecha", "hora_inicio")
    )
    form = ReprogramarCitaForm(request.POST or None, turnos=turnos)
    if request.method == "POST" and form.is_valid():
        try:
            nueva = servicios.reprogramar_cita(
                cita.pk, form.cleaned_data["horario"].pk, request.user, form.cleaned_data["motivo"],
            )
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(
                request,
                f"Cita reprogramada al {nueva.horario.fecha:%d/%m/%Y} a las {nueva.horario.hora_inicio:%H:%M}. "
                "El estudiante la verá en «Mis citas».",
            )
            return redirect(volver)

    dias = {}
    for h in turnos:
        dias.setdefault(h.fecha, []).append(h)

    return render(request, "citas/cita_reprogramar.html", {
        "cita": cita,
        "form": form,
        "dias": list(dias.items()),
        "elegido": str(form.data.get("horario", "")),
        "volver": volver,
    })
    
# =====================================================================
# Citas (administrador)
# =====================================================================
def _entero(valor):
    return int(valor) if valor and str(valor).isdigit() else None


@perfil_requerido(GRUPO_ADMINISTRADOR)
def gestion_citas(request):
    g = request.GET
    hoy = timezone.localdate()
    f = {
        "q": g.get("q", "").strip(),
        "area": _entero(g.get("area")),
        "estado": g.get("estado", ""),
        # Por defecto desde hoy; si llega "desde" vacío, se muestra todo el historial.
        "desde": _fecha(g.get("desde")) if "desde" in g else hoy,
        "hasta": _fecha(g.get("hasta")),
    }

    citas = Cita.objects.select_related(
        "estado", "estudiante__persona__usuario", "horario__personal__persona", "horario__personal__area",
    )
    for palabra in f["q"].split():
        citas = citas.filter(
            Q(estudiante__persona__nombres__icontains=palabra)
            | Q(estudiante__persona__apellidos__icontains=palabra)
            | Q(estudiante__persona__usuario__cedula__icontains=palabra)
            | Q(estudiante__codigo_estudiantil__icontains=palabra)
        )
    if f["area"]:
        citas = citas.filter(horario__personal__area_id=f["area"])
    if f["estado"] == "activas":
        citas = citas.filter(estado__es_activo=True)
    elif f["estado"]:
        citas = citas.filter(estado__nombre=f["estado"])
    if f["desde"]:
        citas = citas.filter(horario__fecha__gte=f["desde"])
    if f["hasta"]:
        citas = citas.filter(horario__fecha__lte=f["hasta"])

    # Mirando hacia adelante: de la más cercana a la más lejana. Historial: la más reciente primero.
    if f["desde"]:
        citas = citas.order_by("horario__fecha", "horario__hora_inicio")
    else:
        citas = citas.order_by("-horario__fecha", "-horario__hora_inicio")

    pagina = Paginator(citas, 15).get_page(g.get("page"))
    consulta = g.copy()
    consulta.pop("page", None)

    del_mes = Cita.objects.filter(horario__fecha__year=hoy.year, horario__fecha__month=hoy.month)
    resumen = {
        "hoy": Cita.objects.exclude(estado__nombre__in=ESTADOS_DE_BAJA).filter(horario__fecha=hoy).count(),
        "activas": Cita.objects.filter(estado__es_activo=True, horario__fecha__gte=hoy).count(),
        "atendidas_mes": del_mes.filter(estado__nombre=EstadoCita.ATENDIDA).count(),
        "no_asistio_mes": del_mes.filter(estado__nombre=EstadoCita.NO_ASISTIO).count(),
        "canceladas_mes": del_mes.filter(estado__nombre=EstadoCita.CANCELADA).count(),
    }

    return render(request, "citas/gestion_citas.html", {
        "page_obj": pagina,
        "filtros": f,
        "areas": Area.objects.order_by("nombre"),
        "estados": EstadoCita.objects.all(),
        "querystring": consulta.urlencode(),
        "hay_filtros": bool(g),
        "resumen": resumen,
        "volver": request.get_full_path(),
    })


@perfil_requerido(GRUPO_ADMINISTRADOR)
def gestion_cita_detalle(request, pk):
    relacionados = (
        "estado", "estudiante__persona__usuario", "estudiante__carrera",
        "horario__personal__persona", "horario__personal__area",
        "cancelada_por__persona", "ausencia", "feriado",
    )
    consulta = Cita.objects.select_related(*relacionados)
    cita = get_object_or_404(consulta, pk=pk)

    # Cadena de reprogramaciones: se busca la cita original y se sigue hacia adelante.
    raiz, pasos = cita, 0
    while raiz.cita_origen_id and pasos < 20:
        raiz = consulta.get(pk=raiz.cita_origen_id)
        pasos += 1
    historia, actual = [raiz], raiz
    while len(historia) < 20:
        siguiente = consulta.filter(cita_origen=actual).first()
        if siguiente is None:
            break
        historia.append(siguiente)
        actual = siguiente

    return render(request, "citas/gestion_cita_detalle.html", {
        "cita": cita,
        "historia": historia,
        "volver": _volver(request, reverse("citas:gestion_citas")),
        "aqui": request.get_full_path(),
    })