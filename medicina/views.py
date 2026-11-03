from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from citas.models import Cita, EstadoCita
from estudiantes.models import Estudiante
from usuarios.comun import AREA_MEDICINA
from usuarios.permisos import perfil_requerido

from . import servicios
from .forms import TIPOS_SIN_CITA, ConsultaMedicaForm, ConsultaSinCitaForm, NotaAclaratoriaForm
from .models import AntecedentesMedicos, ConsultaMedica
from django.utils.http import url_has_allowed_host_and_scheme

# El grupo de permisos se llama igual que el área.
GRUPO_MEDICINA = AREA_MEDICINA
MAXIMO_RESULTADOS = 20


# =====================================================================
# Ayudantes
# =====================================================================
def _edad(nacimiento):
    if not nacimiento:
        return None
    hoy = timezone.localdate()
    return hoy.year - nacimiento.year - ((hoy.month, hoy.day) < (nacimiento.month, nacimiento.day))


def _contexto_paciente(estudiante, excluir=None):
    """Datos del panel izquierdo: paciente, antecedentes y consultas anteriores."""
    anteriores = (
        ConsultaMedica.objects
        .filter(estudiante=estudiante)
        .select_related("personal__persona")
        .order_by("-fecha_atencion")
    )
    if excluir:
        anteriores = anteriores.exclude(pk=excluir)
    return {
        "estudiante": estudiante,
        "edad": _edad(estudiante.persona.fecha_nacimiento),
        "antecedentes": AntecedentesMedicos.objects.filter(estudiante=estudiante).first(),
        "anteriores": list(anteriores[:5]),
    }


def _url_agenda(fecha=None):
    url = reverse("citas:agenda")
    return f"{url}?dia={fecha:%Y-%m-%d}" if fecha else url


def _render_formulario(request, form, estudiante, excluir=None, **extra):
    """Un solo formulario para atender con cita, sin cita y editar."""
    contexto = _contexto_paciente(estudiante, excluir=excluir)
    contexto.update(form=form, **extra)
    return render(request, "medicina/consulta_form.html", contexto)


def _signos(c):
    """Signos vitales registrados, listos para mostrar (se omiten los vacíos)."""
    filas = [
        ("Presión arterial", f"{c.presion_arterial} mmHg" if c.presion_arterial else None),
        ("Frecuencia cardíaca", f"{c.frecuencia_cardiaca} lpm" if c.frecuencia_cardiaca else None),
        ("Frecuencia respiratoria", f"{c.frecuencia_respiratoria} rpm" if c.frecuencia_respiratoria else None),
        ("Temperatura", f"{c.temperatura} °C" if c.temperatura is not None else None),
        ("Saturación de O₂", f"{c.saturacion_oxigeno} %" if c.saturacion_oxigeno else None),
        ("Peso", f"{c.peso} kg" if c.peso else None),
        ("Talla", f"{c.talla} m" if c.talla else None),
        ("IMC", f"{c.imc} · {c.clasificacion_imc}" if c.imc else None),
    ]
    return [(etiqueta, valor) for etiqueta, valor in filas if valor]


# =====================================================================
# Atender una cita
# =====================================================================
@perfil_requerido(GRUPO_MEDICINA)
def atender_cita(request, cita_id):
    medico = servicios.medico_de(request.user)
    cita = get_object_or_404(
        Cita.objects.select_related(
            "estado", "horario", "estudiante__persona__usuario", "estudiante__carrera",
        ),
        pk=cita_id,
        horario__personal=medico,   # una cita de otro profesional da 404
    )
    volver = _url_agenda(cita.horario.fecha)

    existente = ConsultaMedica.objects.filter(cita=cita).first()
    if existente:
        messages.info(request, "Esta cita ya tiene una consulta registrada.")
        return redirect("medicina:consulta_detalle", existente.pk)

    if not (cita.estado.es_activo or cita.estado.nombre == EstadoCita.ATENDIDA):
        messages.error(request, f"No se puede atender una cita {cita.estado.nombre.lower()}.")
        return redirect(volver)
    if not cita.horario.ya_paso:
        messages.error(request, "La consulta se registra a partir de la hora del turno.")
        return redirect(volver)

    if request.method == "POST":
        form = ConsultaMedicaForm(request.POST)
        if form.is_valid():
            consulta = form.save(commit=False)
            try:
                servicios.registrar_consulta_con_cita(consulta, cita.pk, request.user)
            except ValidationError as error:
                form.add_error(None, " ".join(error.messages))
            else:
                messages.success(request, f"Consulta de {cita.estudiante.persona.nombre_completo} registrada.")
                return redirect("medicina:consulta_detalle", consulta.pk)
    else:
        form = ConsultaMedicaForm(initial={
            "fecha_atencion": cita.horario.inicio,
            "motivo_consulta": cita.motivo,
        })

    subtitulo = (f"Cita del {date_format(cita.horario.fecha, r'l j \d\e F')}, "
                 f"{cita.horario.hora_inicio:%H:%M} – {cita.horario.hora_fin:%H:%M}")
    return _render_formulario(
        request, form, cita.estudiante,
        titulo="Atender consulta", subtitulo=subtitulo, boton="Guardar consulta",
        volver=volver, texto_volver="Volver a Mi agenda",
    )


# =====================================================================
# Atención sin cita / emergencia
# =====================================================================
@perfil_requerido(GRUPO_MEDICINA)
def buscar_paciente(request):
    servicios.medico_de(request.user)
    q = request.GET.get("q", "").strip()
    resultados = []

    if len(q) >= 3:
        estudiantes = (
            Estudiante.objects
            .filter(persona__usuario__is_active=True)
            .select_related("persona__usuario", "carrera")
        )
        for palabra in q.split():
            estudiantes = estudiantes.filter(
                Q(persona__nombres__icontains=palabra)
                | Q(persona__apellidos__icontains=palabra)
                | Q(persona__usuario__cedula__icontains=palabra)
                | Q(codigo_estudiantil__icontains=palabra)
            )
        resultados = list(estudiantes.order_by("persona__apellidos", "persona__nombres")[:MAXIMO_RESULTADOS])

    return render(request, "medicina/buscar_paciente.html", {
        "q": q,
        "corto": 0 < len(q) < 3,
        "resultados": resultados,
        "hay_mas": len(resultados) == MAXIMO_RESULTADOS,
        "volver": _url_agenda(),
    })

@perfil_requerido(GRUPO_MEDICINA)
def atender_sin_cita(request, estudiante_id):
    servicios.medico_de(request.user)
    estudiante = get_object_or_404(
        Estudiante.objects.select_related("persona__usuario", "carrera"),
        pk=estudiante_id,
        persona__usuario__is_active=True,
    )

    # Cita de Medicina que el estudiante ya tiene pendiente (si existe).
    cita_pendiente = (
        Cita.objects
        .filter(estudiante=estudiante, estado__es_activo=True, horario__personal__area__nombre=AREA_MEDICINA)
        .select_related("horario")
        .order_by("horario__fecha", "horario__hora_inicio")
        .first()
    )
    cita_ya_empezo = bool(cita_pendiente and cita_pendiente.horario.ya_paso)

    if request.method == "POST":
        form = ConsultaSinCitaForm(request.POST)
        if form.is_valid():
            consulta = form.save(commit=False)
            cancelar = cita_pendiente if form.cleaned_data["cancelar_cita_pendiente"] else None
            try:
                _, cancelada = servicios.registrar_consulta_sin_cita(consulta, estudiante, request.user, cancelar)
            except ValidationError as error:
                form.add_error(None, " ".join(error.messages))
            else:
                texto = f"Consulta de {estudiante.persona.nombre_completo} registrada."
                if cancelada:
                    texto += (f" Se canceló su cita del {cancelada.horario.fecha:%d/%m/%Y} "
                              "y el turno quedó libre.")
                messages.success(request, texto)
                return redirect("medicina:consulta_detalle", consulta.pk)
    else:
        inicial = {"fecha_atencion": timezone.localtime().replace(second=0, microsecond=0)}
        tipo = request.GET.get("tipo")
        if tipo in dict(TIPOS_SIN_CITA):
            inicial["tipo"] = tipo
        form = ConsultaSinCitaForm(initial=inicial)

    return _render_formulario(
        request, form, estudiante,
        titulo="Atención sin cita",
        subtitulo="El estudiante no tiene turno o llegó por una emergencia.",
        boton="Guardar consulta", con_tipo=True,
        cita_pendiente=cita_pendiente, cita_ya_empezo=cita_ya_empezo,
        volver=reverse("medicina:sin_cita"), texto_volver="Volver a la búsqueda",
    )


# =====================================================================
# Ver, editar y agregar notas
# =====================================================================
def _consulta(pk):
    return get_object_or_404(
        ConsultaMedica.objects.select_related(
            "estudiante__persona__usuario", "estudiante__carrera",
            "personal__persona", "cita__horario",
        ),
        pk=pk,
    )


@perfil_requerido(GRUPO_MEDICINA)
def consulta_detalle(request, pk):
    medico = servicios.medico_de(request.user)
    consulta = _consulta(pk)

    # Recordatorio: si fue una atención sin cita y aún tiene una cita de Medicina después, ofrecer cancelarla.
    cita_por_cancelar = None
    if consulta.tipo != ConsultaMedica.Tipo.PROGRAMADA:
        cita_por_cancelar = (
            Cita.objects
            .filter(estudiante=consulta.estudiante, estado__es_activo=True,
                    horario__personal__area__nombre=AREA_MEDICINA)
            .select_related("horario")
            .order_by("horario__fecha", "horario__hora_inicio")
            .first()
        )
        if cita_por_cancelar and cita_por_cancelar.horario.inicio <= consulta.fecha_atencion:
            cita_por_cancelar = None

    contexto = _contexto_paciente(consulta.estudiante, excluir=consulta.pk)
    contexto.update({
        "consulta": consulta,
        "signos": _signos(consulta),
        "notas": consulta.notas.select_related("registrada_por__persona"),
        "puede_editar": consulta.es_editable and consulta.personal_id == medico.pk,
        "form_nota": None if consulta.es_editable else NotaAclaratoriaForm(),
        "cita_por_cancelar": cita_por_cancelar,
        "volver": _url_agenda(consulta.cita.horario.fecha if consulta.cita_id else None),
    })
    return render(request, "medicina/consulta_detalle.html", contexto)

@perfil_requerido(GRUPO_MEDICINA)
def consulta_editar(request, pk):
    medico = servicios.medico_de(request.user)
    consulta = _consulta(pk)
    detalle = reverse("medicina:consulta_detalle", args=[consulta.pk])

    if consulta.personal_id != medico.pk or not consulta.es_editable:
        messages.error(
            request,
            "Esta consulta ya no se puede editar. Si necesitas corregir algo, agrega una nota aclaratoria.",
        )
        return redirect(detalle)

    form = ConsultaMedicaForm(request.POST or None, instance=consulta)
    if request.method == "POST" and form.is_valid():
        try:
            servicios.editar_consulta(form.save(commit=False), request.user)
        except ValidationError as error:
            form.add_error(None, " ".join(error.messages))
        else:
            messages.success(request, "Consulta actualizada.")
            return redirect(detalle)

    subtitulo = (f"Registrada hoy a las {timezone.localtime(consulta.fecha_registro):%H:%M}. "
                 "Se puede editar hasta las 23:59 de hoy.")
    return _render_formulario(
        request, form, consulta.estudiante, excluir=consulta.pk,
        titulo="Editar consulta", subtitulo=subtitulo, boton="Guardar cambios",
        volver=detalle, texto_volver="Volver a la consulta",
    )


@perfil_requerido(GRUPO_MEDICINA)
@require_POST
def nota_agregar(request, pk):
    form = NotaAclaratoriaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escribe el texto de la nota aclaratoria.")
    else:
        try:
            servicios.agregar_nota(pk, request.user, form.cleaned_data["texto"])
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, "Nota aclaratoria agregada.")
    return redirect("medicina:consulta_detalle", pk)

@perfil_requerido(GRUPO_MEDICINA)
@require_POST
def cancelar_cita_pendiente(request, pk):
    """Cancela con un clic la cita que ya no hace falta (el estudiante fue atendido antes sin cita)."""
    consulta = get_object_or_404(ConsultaMedica, pk=pk)
    volver = request.POST.get("volver")
    if not (volver and url_has_allowed_host_and_scheme(
            volver, allowed_hosts={request.get_host()}, require_https=request.is_secure())):
        volver = reverse("medicina:consulta_detalle", args=[pk])

    try:
        cita = servicios.cancelar_cita_por_atencion_previa(request.POST.get("cita"), consulta, request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            f"Se canceló la cita del {cita.horario.fecha:%d/%m/%Y}. El turno quedó libre "
            "y el estudiante verá el motivo en «Mis citas».",
        )
    return redirect(volver)