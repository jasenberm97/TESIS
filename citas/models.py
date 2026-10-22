from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from estudiantes.models import Estudiante
from usuarios.comun import ValidarAlGuardarMixin, check_constraint
from usuarios.models import Personal

HORA_APERTURA = time(8, 0)
HORA_CIERRE = time(16, 30)
HORAS_MINIMAS_CANCELACION = 24


def _combinar(fecha, hora):
    """Fecha y hora de un turno, con zona horaria si el proyecto la usa."""
    momento = datetime.combine(fecha, hora)
    return timezone.make_aware(momento) if settings.USE_TZ else momento


# =====================================================================
# Horario: un turno concreto de un profesional
# =====================================================================
class Horario(ValidarAlGuardarMixin, models.Model):
    personal = models.ForeignKey(Personal, on_delete=models.PROTECT, related_name="horarios")
    fecha = models.DateField()
    hora_inicio = models.TimeField("hora de inicio")
    hora_fin = models.TimeField("hora de fin")

    class Meta:
        verbose_name = "horario"
        verbose_name_plural = "horarios"
        ordering = ["fecha", "hora_inicio"]
        constraints = [
            models.UniqueConstraint(fields=["personal", "fecha", "hora_inicio"], name="uq_horario"),
            check_constraint(Q(hora_fin__gt=F("hora_inicio")), "ck_horario_horas"),
            check_constraint(Q(hora_inicio__gte=HORA_APERTURA, hora_fin__lte=HORA_CIERRE), "ck_horario_atencion"),
        ]

    def __str__(self):
        return f"{self.personal} | {self.fecha:%d/%m/%Y} {self.hora_inicio:%H:%M}-{self.hora_fin:%H:%M}"

    def clean(self):
        if not (self.fecha and self.hora_inicio and self.hora_fin and self.personal_id):
            return

        if self.fecha.weekday() >= 5:
            raise ValidationError({"fecha": "Solo se atiende de lunes a viernes."})

        if self.hora_inicio < HORA_APERTURA or self.hora_fin > HORA_CIERRE:
            raise ValidationError("El turno debe estar dentro del horario de atención (08:00 a 16:30).")

        if self.hora_fin <= self.hora_inicio:
            raise ValidationError({"hora_fin": "La hora de fin debe ser posterior a la de inicio."})

        if self._state.adding and not self.personal.esta_activo:
            raise ValidationError({"personal": "No se pueden crear turnos para un profesional inactivo."})

        cruce = (
            Horario.objects.filter(
                personal_id=self.personal_id,
                fecha=self.fecha,
                hora_inicio__lt=self.hora_fin,
                hora_fin__gt=self.hora_inicio,
            )
            .exclude(pk=self.pk)
            .exists()
        )
        if cruce:
            raise ValidationError("El turno se cruza con otro turno del mismo profesional.")

    @property
    def area(self):
        """Se obtiene a través del profesional; no se guarda en el horario (3FN)."""
        return self.personal.area

    @property
    def inicio(self):
        return _combinar(self.fecha, self.hora_inicio)

    @property
    def esta_disponible(self):
        """Dato derivado: un turno está libre si no tiene una cita activa."""
        return not self.citas.filter(estado__es_activo=True).exists()


# =====================================================================
# Estados de la cita
# =====================================================================
class EstadoCita(models.Model):
    PENDIENTE = "Pendiente"
    CONFIRMADA = "Confirmada"
    ATENDIDA = "Atendida"
    CANCELADA = "Cancelada"
    REPROGRAMADA = "Reprogramada"
    NO_ASISTIO = "No asistió"

    nombre = models.CharField(max_length=30, unique=True)
    es_activo = models.BooleanField(
        "ocupa el turno", default=False,
        help_text="Marcar para los estados en los que la cita sigue vigente (Pendiente, Confirmada).",
    )

    class Meta:
        verbose_name = "estado de cita"
        verbose_name_plural = "estados de cita"
        ordering = ["id"]

    def __str__(self):
        return self.nombre


# =====================================================================
# Cita
# =====================================================================
class Cita(ValidarAlGuardarMixin, models.Model):
    estudiante = models.ForeignKey(Estudiante, on_delete=models.PROTECT, related_name="citas")
    horario = models.ForeignKey(Horario, on_delete=models.PROTECT, related_name="citas")
    estado = models.ForeignKey(EstadoCita, on_delete=models.PROTECT, related_name="citas")
    motivo = models.CharField(max_length=255)
    fecha_registro = models.DateTimeField("fecha de registro", auto_now_add=True)
    motivo_cancelacion = models.CharField("motivo de cancelación", max_length=255, blank=True)
    cita_origen = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="reprogramaciones",
        verbose_name="cita original", help_text="Se llena cuando esta cita es una reprogramación.",
    )

    class Meta:
        verbose_name = "cita"
        verbose_name_plural = "citas"
        ordering = ["-horario__fecha", "-horario__hora_inicio"]

    def __str__(self):
        return f"{self.estudiante.persona} | {self.horario}"

    def clean(self):
        if not (self.horario_id and self.estudiante_id and self.estado_id):
            return

        if self._state.adding:
            if self.horario.inicio <= timezone.now():
                raise ValidationError({"horario": "No se puede reservar un turno que ya pasó."})
            if not self.horario.personal.esta_activo:
                raise ValidationError({"horario": "El profesional de este turno está inactivo."})

        if not self.estado.es_activo:
            return

        activas = Cita.objects.filter(estado__es_activo=True).exclude(pk=self.pk)

        if activas.filter(horario_id=self.horario_id).exists():
            raise ValidationError({"horario": "Este turno ya tiene una cita activa."})

        area = self.horario.personal.area_id
        if activas.filter(estudiante_id=self.estudiante_id, horario__personal__area_id=area).exists():
            raise ValidationError(
                f"El estudiante ya tiene una cita activa en {self.horario.personal.area}. "
                "Debe esperar a que se atienda o cancelarla antes de reservar otra."
            )

    @property
    def area(self):
        return self.horario.personal.area

    @property
    def profesional(self):
        return self.horario.personal

    def puede_cancelar_estudiante(self):
        """El estudiante puede cancelar o reprogramar hasta 24 horas antes del turno."""
        limite = self.horario.inicio - timedelta(hours=HORAS_MINIMAS_CANCELACION)
        return self.estado.es_activo and timezone.now() <= limite