from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q

from citas.models import Cita
from usuarios.comun import AREA_ODONTOLOGIA, ValidarAlGuardarMixin, check_constraint, validar_area_de_cita


# =====================================================================
# Consulta odontológica
# =====================================================================
class ConsultaOdontologica(ValidarAlGuardarMixin, models.Model):
    cita = models.OneToOneField(Cita, on_delete=models.PROTECT, related_name="consulta_odontologica")
    motivo_consulta = models.TextField("motivo de consulta")
    examen_clinico = models.TextField("examen clínico", blank=True)
    diagnostico = models.TextField("diagnóstico")
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "consulta odontológica"
        verbose_name_plural = "consultas odontológicas"

    def __str__(self):
        return f"Consulta odontológica - {self.cita}"

    def clean(self):
        validar_area_de_cita(self.cita if self.cita_id else None, AREA_ODONTOLOGIA)


# =====================================================================
# Catálogos (las tablas del odontograma son provisionales)
# =====================================================================
class Procedimiento(models.Model):
    nombre = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "procedimiento"
        verbose_name_plural = "procedimientos"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class PiezaDental(models.Model):
    class Tipo(models.TextChoices):
        PERMANENTE = "PERMANENTE", "Permanente"
        TEMPORAL = "TEMPORAL", "Temporal"

    codigo_fdi = models.CharField("código FDI", max_length=2, unique=True)
    nombre = models.CharField(max_length=60)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)

    class Meta:
        verbose_name = "pieza dental"
        verbose_name_plural = "piezas dentales"
        ordering = ["codigo_fdi"]

    def __str__(self):
        return f"{self.codigo_fdi} - {self.nombre}"


class SuperficieDental(models.Model):
    nombre = models.CharField(max_length=30, unique=True)

    class Meta:
        verbose_name = "superficie dental"
        verbose_name_plural = "superficies dentales"

    def __str__(self):
        return self.nombre


class CondicionDental(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    color = models.CharField(
        max_length=7,
        validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Use un color hexadecimal, por ejemplo #D32F2F.")],
        help_text="Color con el que se pinta en el odontograma.",
    )

    class Meta:
        verbose_name = "condición dental"
        verbose_name_plural = "condiciones dentales"

    def __str__(self):
        return self.nombre


# =====================================================================
# Tratamientos y controles
# =====================================================================
class TratamientoOdontologico(models.Model):
    class Estado(models.TextChoices):
        PLANIFICADO = "PLANIFICADO", "Planificado"
        EN_CURSO = "EN_CURSO", "En curso"
        FINALIZADO = "FINALIZADO", "Finalizado"
        SUSPENDIDO = "SUSPENDIDO", "Suspendido"

    consulta_odontologica = models.ForeignKey(
        ConsultaOdontologica, on_delete=models.PROTECT, related_name="tratamientos",
        help_text="Consulta en la que se planificó el tratamiento.",
    )
    procedimiento = models.ForeignKey(Procedimiento, on_delete=models.PROTECT, related_name="tratamientos")
    pieza = models.ForeignKey(PiezaDental, on_delete=models.PROTECT, null=True, blank=True, related_name="tratamientos")
    descripcion = models.TextField("descripción", blank=True)
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.PLANIFICADO)
    fecha_inicio = models.DateField("fecha de inicio")
    fecha_fin = models.DateField("fecha de fin", null=True, blank=True)

    class Meta:
        verbose_name = "tratamiento odontológico"
        verbose_name_plural = "tratamientos odontológicos"
        ordering = ["-fecha_inicio"]
        constraints = [
            check_constraint(
                Q(fecha_fin__isnull=True) | Q(fecha_fin__gte=F("fecha_inicio")),
                "ck_tratamiento_fechas",
            ),
        ]

    def __str__(self):
        pieza = f" - pieza {self.pieza.codigo_fdi}" if self.pieza_id else ""
        return f"{self.procedimiento}{pieza} ({self.get_estado_display()})"


class ControlOdontologico(ValidarAlGuardarMixin, models.Model):
    tratamiento = models.ForeignKey(TratamientoOdontologico, on_delete=models.PROTECT, related_name="controles")
    consulta_odontologica = models.ForeignKey(
        ConsultaOdontologica, on_delete=models.PROTECT, related_name="controles",
        help_text="Consulta en la que se realizó el control.",
    )
    avance = models.TextField()
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "control odontológico"
        verbose_name_plural = "controles odontológicos"
        constraints = [
            models.UniqueConstraint(fields=["tratamiento", "consulta_odontologica"], name="uq_control"),
        ]

    def __str__(self):
        return f"Control de {self.tratamiento}"

    def clean(self):
        if self.tratamiento_id and self.consulta_odontologica_id:
            estudiante_tratamiento = self.tratamiento.consulta_odontologica.cita.estudiante_id
            estudiante_control = self.consulta_odontologica.cita.estudiante_id
            if estudiante_tratamiento != estudiante_control:
                raise ValidationError("El control debe registrarse en una consulta del mismo estudiante del tratamiento.")


# =====================================================================
# Odontograma (provisional)
# =====================================================================
class RegistroOdontograma(models.Model):
    consulta_odontologica = models.ForeignKey(
        ConsultaOdontologica, on_delete=models.PROTECT, related_name="registros_odontograma"
    )
    pieza = models.ForeignKey(PiezaDental, on_delete=models.PROTECT, related_name="registros")
    superficie = models.ForeignKey(
        SuperficieDental, on_delete=models.PROTECT, null=True, blank=True, related_name="registros",
        help_text="Vacío cuando la condición afecta a toda la pieza.",
    )
    condicion = models.ForeignKey(CondicionDental, on_delete=models.PROTECT, related_name="registros")
    observacion = models.TextField("observación", blank=True)

    class Meta:
        verbose_name = "registro de odontograma"
        verbose_name_plural = "registros de odontograma"
        constraints = [
            models.UniqueConstraint(
                fields=["consulta_odontologica", "pieza", "superficie", "condicion"],
                name="uq_registro_odontograma",
            ),
        ]

    def __str__(self):
        return f"Pieza {self.pieza.codigo_fdi}: {self.condicion}"