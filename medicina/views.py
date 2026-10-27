from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from citas.models import Cita, EstadoCita
from usuarios.comun import AREA_MEDICINA
from usuarios.permisos import perfil_requerido

from . import servicios
from .forms import ConsultaMedicaForm
from .models import AntecedentesMedicos, ConsultaMedica

# El grupo de permisos se llama igual que el área.
GRUPO_MEDICINA = AREA_MEDICINA


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
                messages.success(
                    request, f"Consulta de {cita.estudiante.persona.nombre_completo} registrada."
                )
                return redirect("medicina:consulta_detalle", consulta.pk)
    else:
        form = ConsultaMedicaForm(initial={
            "fecha_atencion": cita.horario.inicio,
            "motivo_consulta": cita.motivo,
        })

    contexto = _contexto_paciente(cita.estudiante)
    contexto.update({"form": form, "cita": cita, "volver": volver})
    return render(request, "medicina/atender.html", contexto)


# =====================================================================
# Ver una consulta
# =====================================================================
@perfil_requerido(GRUPO_MEDICINA)
def consulta_detalle(request, pk):
    consulta = get_object_or_404(
        ConsultaMedica.objects.select_related(
            "estudiante__persona__usuario", "estudiante__carrera",
            "personal__persona", "cita__horario",
        ),
        pk=pk,
    )
    contexto = _contexto_paciente(consulta.estudiante, excluir=consulta.pk)
    contexto.update({
        "consulta": consulta,
        "signos": _signos(consulta),
        "notas": consulta.notas.select_related("registrada_por__persona"),
        "volver": _url_agenda(consulta.cita.horario.fecha if consulta.cita_id else None),
    })
    return render(request, "medicina/consulta_detalle.html", contexto)