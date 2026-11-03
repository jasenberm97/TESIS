from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from citas.models import Cita, EstadoCita
from estudiantes.models import Estudiante
from usuarios.comun import AREA_MEDICINA
from usuarios.permisos import perfil_requerido

from . import servicios
from .forms import (
    TIPOS_SIN_CITA, AntecedentesForm, ConsultaMedicaForm, ConsultaSinCitaForm, NotaAclaratoriaForm,
)
from .models import AntecedentesMedicos, ConsultaMedica

# El grupo de permisos se llama igual que el área.
GRUPO_MEDICINA = AREA_MEDICINA
MAXIMO_RESULTADOS = 20
DIAS_RECIENTES = 30


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
        "antecedentes": (
            AntecedentesMedicos.objects
            .filter(estudiante=estudiante)
            .select_related("actualizado_por__persona")
            .first()
        ),
        "anteriores": list(anteriores[:5]),
    }


def _url_agenda(fecha=None):
    url = reverse("citas:agenda")
    return f"{url}?dia={fecha:%Y-%m-%d}" if fecha else url


def _url_segura(request, url, defecto):
    """Solo se aceptan direcciones del mismo sitio para volver después de una acción."""
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return url
    return defecto


def _render_formulario(request, form, estudiante, excluir=None, **extra):
    """Un solo formulario para atender con cita, sin cita y editar."""
    contexto = _contexto_paciente(estudiante, excluir=excluir)
    contexto.update(form=form, en_formulario=True, **extra)
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


def _buscar_estudiantes(q, solo_activos=True):
    """Busca por nombres, apellidos, cédula o código. Cada palabra debe coincidir en algún campo."""
    estudiantes = Estudiante.objects.select_related("persona__usuario", "carrera")
    if solo_activos:
        estudiantes = estudiantes.filter(persona__usuario__is_active=True)
    for palabra in q.split():
        estudiantes = estudiantes.filter(
            Q(persona__nombres__icontains=palabra)
            | Q(persona__apellidos__icontains=palabra)
            | Q(persona__usuario__cedula__icontains=palabra)
            | Q(codigo_estudiantil__icontains=palabra)
        )
    return estudiantes


def _cita_pendiente_medicina(estudiante):
    return (
        Cita.objects
        .filter(estudiante=estudiante, estado__es_activo=True, horario__personal__area__nombre=AREA_MEDICINA)
        .select_related("horario")
        .order_by("horario__fecha", "horario__hora_inicio")
        .first()
    )


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
        resultados = list(
            _buscar_estudiantes(q).order_by("persona__apellidos", "persona__nombres")[:MAXIMO_RESULTADOS]
        )

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
    cita_pendiente = _cita_pendiente_medicina(estudiante)
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

    volver = _url_segura(request, request.GET.get("volver"), reverse("medicina:sin_cita"))
    return _render_formulario(
        request, form, estudiante,
        titulo="Atención sin cita",
        subtitulo="El estudiante no tiene turno o llegó por una emergencia.",
        boton="Guardar consulta", con_tipo=True,
        cita_pendiente=cita_pendiente, cita_ya_empezo=cita_ya_empezo,
        volver=volver, texto_volver="Volver",
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
        cita_por_cancelar = _cita_pendiente_medicina(consulta.estudiante)
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
    volver = _url_segura(request, request.POST.get("volver"), reverse("medicina:consulta_detalle", args=[pk]))

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


# =====================================================================
# Pacientes: lista, ficha con historial y antecedentes
# =====================================================================
@perfil_requerido(GRUPO_MEDICINA)
def pacientes(request):
    servicios.medico_de(request.user)
    q = request.GET.get("q", "").strip()
    resumen = {"ultima": Max("consultas_medicas__fecha_atencion"), "total": Count("consultas_medicas")}

    if len(q) >= 3:
        # Búsqueda en todos los estudiantes (también cuentas desactivadas: su historia se sigue consultando).
        estudiantes = (
            _buscar_estudiantes(q, solo_activos=False)
            .annotate(**resumen)
            .order_by("persona__apellidos", "persona__nombres")
        )
        modo = "busqueda"
    else:
        desde = timezone.now() - timedelta(days=DIAS_RECIENTES)
        estudiantes = (
            Estudiante.objects
            .select_related("persona__usuario", "carrera")
            .annotate(**resumen)
            .filter(ultima__gte=desde)
            .order_by("-ultima")
        )
        modo = "recientes"

    pagina = Paginator(estudiantes, 15).get_page(request.GET.get("page"))
    return render(request, "medicina/pacientes_lista.html", {
        "q": q,
        "corto": 0 < len(q) < 3,
        "modo": modo,
        "page_obj": pagina,
        "dias": DIAS_RECIENTES,
    })


@perfil_requerido(GRUPO_MEDICINA)
def paciente_ficha(request, pk):
    servicios.medico_de(request.user)
    estudiante = get_object_or_404(Estudiante.objects.select_related("persona__usuario", "carrera"), pk=pk)

    consultas = list(
        ConsultaMedica.objects
        .filter(estudiante=estudiante)
        .select_related("personal__persona")
        .annotate(n_notas=Count("notas"))
        .order_by("-fecha_atencion")
    )

    # Historial agrupado por día (fecha local), del más reciente al más antiguo.
    dias = []
    for c in consultas:
        fecha = timezone.localdate(c.fecha_atencion)
        if not dias or dias[-1]["fecha"] != fecha:
            dias.append({"fecha": fecha, "consultas": []})
        dias[-1]["consultas"].append(c)

    contexto = _contexto_paciente(estudiante)
    contexto.update({
        "en_ficha": True,
        "dias": dias,
        "total": len(consultas),
        "emergencias": sum(1 for c in consultas if c.tipo == ConsultaMedica.Tipo.EMERGENCIA),
        "ultima": consultas[0].fecha_atencion if consultas else None,
        "activo": estudiante.persona.usuario.is_active,
        "aqui": request.get_full_path(),
    })
    return render(request, "medicina/paciente_ficha.html", contexto)


@perfil_requerido(GRUPO_MEDICINA)
def antecedentes_editar(request, pk):
    servicios.medico_de(request.user)
    estudiante = get_object_or_404(Estudiante.objects.select_related("persona__usuario", "carrera"), pk=pk)
    antecedentes = (
        AntecedentesMedicos.objects.filter(estudiante=estudiante).select_related("actualizado_por__persona").first()
        or AntecedentesMedicos(estudiante=estudiante)
    )
    ficha = reverse("medicina:paciente_ficha", args=[estudiante.pk])

    form = AntecedentesForm(request.POST or None, instance=antecedentes)
    if request.method == "POST" and form.is_valid():
        try:
            servicios.guardar_antecedentes(form.save(commit=False), request.user)
        except ValidationError as error:
            form.add_error(None, " ".join(error.messages))
        else:
            messages.success(request, "Antecedentes actualizados.")
            return redirect(ficha)

    return render(request, "medicina/antecedentes_form.html", {
        "form": form,
        "estudiante": estudiante,
        "antecedentes": antecedentes if antecedentes.pk else None,
        "volver": ficha,
    })