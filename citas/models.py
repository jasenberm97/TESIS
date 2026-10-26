from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, OuterRef, Q
from django.utils import timezone

from estudiantes.models import Estudiante
from usuarios.comun import ValidarAlGuardarMixin, check_constraint
from usuarios.models import Personal

HORA_APERTURA = time(8, 0)
HORA_CIERRE = time(16, 30)
HORAS_MINIMAS_CANCELACION = 24
DURACION_TURNO_MINUTOS = 30


def _combinar(fecha, hora):
    """Fecha y hora de un turno, con zona horaria si el proyecto la usa."""
    momento = datetime.combine(fecha, hora)
    return timezone.make_aware(momento) if settings.USE_TZ else momento


# =====================================================================
# Feriado: día no laborable para todo Bienestar Universitario
# =====================================================================
class Feriado(ValidarAlGuardarMixin, models.Model):
    fecha = models.DateField(unique=True)
    nombre = models.CharField(max_length=100, help_text="Ej.: Día del Trabajo, Fiestas de Guayaquil, Aniversario UAE")
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="feriados_registrados",
        editable=False,
    )
    fecha_registro = models.DateTimeField("fecha de registro", auto_now_add=True)

    class Meta:
        verbose_name = "feriado"
        verbose_name_plural = "feriados"
        ordering = ["fecha"]

    def __str__(self):
        return f"{self.fecha:%d/%m/%Y} - {self.nombre}"

    def clean(self):
        if self.nombre is not None and not self.nombre.strip():
            raise ValidationError({"nombre": "Indique el nombre del feriado."})


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

        if Feriado.objects.filter(fecha=self.fecha).exists():
            raise ValidationError({"fecha": "Ese día está registrado como feriado."})

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
    def fin(self):
        return _combinar(self.fecha, self.hora_fin)

    @property
    def ya_paso(self):
        return self.inicio <= timezone.now()

    @property
    def esta_disponible(self):
        """Dato derivado: un turno está libre si no tiene una cita activa."""
        return not self.citas.filter(estado__es_activo=True).exists()

    @property
    def es_feriado(self):
        return Feriado.objects.filter(fecha=self.fecha).exists()

    @property
    def en_ausencia(self):
        """True si una ausencia registrada del profesional cubre este turno."""
        return (
            Ausencia.objects
            .filter(personal_id=self.personal_id, fecha_desde__lte=self.fecha, fecha_hasta__gte=self.fecha)
            .filter(Q(hora_desde__isnull=True)
                    | Q(hora_desde__lt=self.hora_fin, hora_hasta__gt=self.hora_inicio))
            .exists()
        )


# =====================================================================
# Ausencia del profesional (día completo o parte del día)
# =====================================================================
class Ausencia(ValidarAlGuardarMixin, models.Model):
    personal = models.ForeignKey(Personal, on_delete=models.PROTECT, related_name="ausencias")
    fecha_desde = models.DateField("desde")
    fecha_hasta = models.DateField("hasta")
    hora_desde = models.TimeField(
        "hora de inicio", null=True, blank=True,
        help_text="Vacía junto con la hora de fin = día completo.",
    )
    hora_hasta = models.TimeField("hora de fin", null=True, blank=True)
    motivo = models.CharField(max_length=200)
    registrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ausencias_registradas",
        editable=False,
    )
    fecha_registro = models.DateTimeField("fecha de registro", auto_now_add=True)

    class Meta:
        verbose_name = "ausencia"
        verbose_name_plural = "ausencias"
        ordering = ["-fecha_desde", "-hora_desde"]
        constraints = [
            check_constraint(Q(fecha_hasta__gte=F("fecha_desde")), "ck_ausencia_fechas"),
            check_constraint(
                Q(hora_desde__isnull=True, hora_hasta__isnull=True)
                | Q(hora_desde__isnull=False, hora_hasta__isnull=False, hora_hasta__gt=F("hora_desde")),
                "ck_ausencia_horas",
            ),
        ]

    def __str__(self):
        return f"{self.personal} | {self.descripcion_fechas} | {self.descripcion_horario}"

    def clean(self):
        if self.fecha_desde and self.fecha_hasta and self.fecha_hasta < self.fecha_desde:
            raise ValidationError({"fecha_hasta": "Debe ser igual o posterior a la fecha de inicio."})

        if (self.hora_desde is None) != (self.hora_hasta is None):
            raise ValidationError("Indique la hora de inicio y la de fin, o ninguna si es el día completo.")

        if self.hora_desde is not None and self.hora_hasta is not None:
            if self.hora_hasta <= self.hora_desde:
                raise ValidationError({"hora_hasta": "La hora de fin debe ser posterior a la de inicio."})
            if self.hora_desde < HORA_APERTURA or self.hora_hasta > HORA_CIERRE:
                raise ValidationError("Las horas deben estar entre las 08:00 y las 16:30.")

        if self.motivo is not None and not self.motivo.strip():
            raise ValidationError({"motivo": "Indique el motivo de la ausencia."})

    @property
    def es_dia_completo(self):
        return self.hora_desde is None

    @property
    def descripcion_fechas(self):
        if self.fecha_desde == self.fecha_hasta:
            return f"{self.fecha_desde:%d/%m/%Y}"
        return f"{self.fecha_desde:%d/%m/%Y} al {self.fecha_hasta:%d/%m/%Y}"

    @property
    def descripcion_horario(self):
        if self.es_dia_completo:
            return "Día completo"
        return f"{self.hora_desde:%H:%M} a {self.hora_hasta:%H:%M}"

    def cubre(self, fecha, hora_inicio, hora_fin):
        """¿Esta ausencia afecta al turno de esa fecha y horas?"""
        if not (self.fecha_desde <= fecha <= self.fecha_hasta):
            return False
        return self.es_dia_completo or (self.hora_desde < hora_fin and self.hora_hasta > hora_inicio)


def ausencia_del_turno():
    """Subconsulta para anotar horarios: Exists(ausencia_del_turno())."""
    return (
        Ausencia.objects
        .filter(personal=OuterRef("personal"),
                fecha_desde__lte=OuterRef("fecha"),
                fecha_hasta__gte=OuterRef("fecha"))
        .filter(Q(hora_desde__isnull=True)
                | Q(hora_desde__lt=OuterRef("hora_fin"), hora_hasta__gt=OuterRef("hora_inicio")))
    )


def feriado_del_turno():
    """Subconsulta para anotar horarios: Exists(feriado_del_turno())."""
    return Feriado.objects.filter(fecha=OuterRef("fecha"))


# =====================================================================
# Estados de la cita
# =====================================================================
class EstadoCita(models.Model):
    PENDIENTE = "Pendiente"        # al agendar
    CONFIRMADA = "Confirmada"      # reservado para la etapa de correos
    ATENDIDA = "Atendida"
    CANCELADA = "Cancelada"
    REPROGRAMADA = "Reprogramada"  # la cita original cuando se mueve a otro turno
    NO_ASISTIO = "No asistió"

    ESTADOS_CON_MOTIVO = (CANCELADA, REPROGRAMADA)

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

    @classmethod
    def obtener(cls, nombre):
        """Devuelve el estado por su nombre, p. ej. EstadoCita.obtener(EstadoCita.PENDIENTE)."""
        return cls.objects.get(nombre=nombre)


# =====================================================================
# Cita
# =====================================================================
class Cita(ValidarAlGuardarMixin, models.Model):
    estudiante = models.ForeignKey(Estudiante, on_delete=models.PROTECT, related_name="citas")
    horario = models.ForeignKey(Horario, on_delete=models.PROTECT, related_name="citas")
    estado = models.ForeignKey(EstadoCita, on_delete=models.PROTECT, related_name="citas")
    motivo = models.CharField(max_length=255)
    fecha_registro = models.DateTimeField("fecha de registro", auto_now_add=True)
    motivo_cancelacion = models.CharField(
        "motivo de cancelación", max_length=255, blank=True,
        help_text="Obligatorio cuando la cita se cancela o se reprograma.",
    )
    cancelada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="citas_canceladas", editable=False,
        verbose_name="cancelada o reprogramada por",
    )
    fecha_cancelacion = models.DateTimeField(
        "fecha de cancelación", null=True, blank=True, editable=False,
    )
    ausencia = models.ForeignKey(
        Ausencia, on_delete=models.SET_NULL, null=True, blank=True, editable=False,
        related_name="citas_canceladas", verbose_name="cancelada por la ausencia",
    )
    feriado = models.ForeignKey(
        Feriado, on_delete=models.SET_NULL, null=True, blank=True, editable=False,
        related_name="citas_canceladas", verbose_name="cancelada por el feriado",
    )
    cita_origen = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="reprogramaciones",
        verbose_name="cita original", help_text="Se llena cuando esta cita es una reprogramación.",
    )

    class Meta:
        verbose_name = "cita"
        verbose_name_plural = "citas"
        ordering = ["-horario__fecha", "-horario__hora_inicio"]
        constraints = [
            check_constraint(
                Q(cancelada_por__isnull=True, fecha_cancelacion__isnull=True)
                | Q(cancelada_por__isnull=False, fecha_cancelacion__isnull=False),
                "ck_cita_cancelacion",
            ),
        ]

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
            if self.horario.es_feriado:
                raise ValidationError({"horario": "Ese día es feriado; no hay atención."})
            if self.horario.en_ausencia:
                raise ValidationError({"horario": "El profesional no atenderá en ese horario (ausencia registrada)."})

        if self.estado.nombre in EstadoCita.ESTADOS_CON_MOTIVO and not self.motivo_cancelacion.strip():
            raise ValidationError({"motivo_cancelacion": "Indique el motivo de la cancelación o reprogramación."})

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

    @property
    def limite_cancelacion_estudiante(self):
        return self.horario.inicio - timedelta(hours=HORAS_MINIMAS_CANCELACION)

    def puede_cancelar_estudiante(self):
        """El estudiante solo puede cancelar (no reprogramar) hasta 24 horas antes del turno."""
        return self.estado.es_activo and timezone.now() <= self.limite_cancelacion_estudiante