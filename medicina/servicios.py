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

from .models import ConsultaMedica


def medico_de(usuario):
    """Devuelve el registro de Personal del médico, o 403 si el usuario no es de Medicina."""
    persona = getattr(usuario, "persona", None)
    personal = getattr(persona, "personal", None) if persona else None
    if personal is None or personal.area.nombre != AREA_MEDICINA:
        raise PermissionDenied("Solo el personal de Medicina registra consultas médicas.")
    return personal


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