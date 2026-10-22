from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from citas.models import Cita
from estudiantes.models import Docente, Estudiante
from usuarios.comun import AREA_PSICOLOGIA, ValidarAlGuardarMixin, check_constraint, validar_area_de_cita
from usuarios.models import Personal


# =====================================================================
# Sesión psicológica (información confidencial)
# =====================================================================
class SesionPsicologica(ValidarAlGuardarMixin, models.Model):
    cita = models.OneToOneField(Cita, on_delete=models.PROTECT, related_name="sesion_psicologica")
    motivo_consulta = models.TextField("motivo de consulta")
    desarrollo_sesion = models.TextField("desarrollo de la sesión")
    diagnostico_presuntivo = models.TextField("diagnóstico presuntivo", blank=True)
    plan_intervencion = models.TextField("plan de intervención", blank=True)
    requiere_seguimiento = models.BooleanField("requiere seguimiento", default=False)

    class Meta:
        verbose_name = "sesión psicológica"
        verbose_name_plural = "sesiones psicológicas"

    def __str__(self):
        return f"Sesión psicológica - {self.cita}"

    def clean(self):
        validar_area_de_cita(self.cita if self.cita_id else None, AREA_PSICOLOGIA)


# =====================================================================
# Necesidades educativas específicas (NEE)
# =====================================================================
class TipoNEE(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    asociada_discapacidad = models.BooleanField("asociada a discapacidad")

    class Meta:
        verbose_name = "tipo de NEE"
        verbose_name_plural = "tipos de NEE"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class RegistroNEE(models.Model):
    class Estado(models.TextChoices):
        ACTIVO = "ACTIVO", "Activo"
        CERRADO = "CERRADO", "Cerrado"

    estudiante = models.ForeignKey(Estudiante, on_delete=models.PROTECT, related_name="registros_nee")
    tipo_nee = models.ForeignKey(TipoNEE, on_delete=models.PROTECT, related_name="registros", verbose_name="tipo de NEE")
    sesion_origen = models.ForeignKey(
        SesionPsicologica, on_delete=models.PROTECT, null=True, blank=True, related_name="registros_nee",
        verbose_name="sesión en que se detectó",
    )
    registrado_por = models.ForeignKey(
        Personal, on_delete=models.PROTECT, related_name="registros_nee",
        limit_choices_to={"area__nombre": AREA_PSICOLOGIA},
    )
    fecha_registro = models.DateField("fecha de registro", auto_now_add=True)
    descripcion = models.TextField("descripción")
    porcentaje_discapacidad = models.DecimalField(
        "porcentaje de discapacidad", max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ACTIVO)

    class Meta:
        verbose_name = "registro de NEE"
        verbose_name_plural = "registros de NEE"
        ordering = ["-fecha_registro"]
        constraints = [
            check_constraint(
                Q(porcentaje_discapacidad__isnull=True)
                | Q(porcentaje_discapacidad__gte=0, porcentaje_discapacidad__lte=100),
                "ck_nee_porcentaje",
            ),
        ]

    def __str__(self):
        return f"{self.estudiante.persona} - {self.tipo_nee}"


# =====================================================================
# Adaptaciones curriculares y su envío a docentes
# =====================================================================
class AdaptacionCurricular(models.Model):
    class Tipo(models.TextChoices):
        ACCESO = "ACCESO", "De acceso"
        NO_SIGNIFICATIVA = "NO_SIGNIFICATIVA", "No significativa"
        SIGNIFICATIVA = "SIGNIFICATIVA", "Significativa"

    registro_nee = models.ForeignKey(RegistroNEE, on_delete=models.PROTECT, related_name="adaptaciones")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    descripcion = models.TextField("descripción")
    recomendaciones = models.TextField(blank=True)
    consentimiento_estudiante = models.BooleanField(
        "consentimiento del estudiante", default=False,
        help_text="Autorización del estudiante para compartir la adaptación con sus docentes (LOPDP).",
    )
    fecha_consentimiento = models.DateField("fecha del consentimiento", null=True, blank=True)
    fecha_inicio = models.DateField("fecha de inicio")
    fecha_fin = models.DateField("fecha de fin", null=True, blank=True)
    docentes = models.ManyToManyField(Docente, through="AdaptacionDocente", related_name="adaptaciones")

    class Meta:
        verbose_name = "adaptación curricular"
        verbose_name_plural = "adaptaciones curriculares"
        ordering = ["-fecha_inicio"]
        constraints = [
            check_constraint(
                Q(consentimiento_estudiante=False) | Q(fecha_consentimiento__isnull=False),
                "ck_adaptacion_consentimiento",
            ),
            check_constraint(
                Q(fecha_fin__isnull=True) | Q(fecha_fin__gte=F("fecha_inicio")),
                "ck_adaptacion_fechas",
            ),
        ]

    def __str__(self):
        return f"Adaptación {self.get_tipo_display().lower()} - {self.registro_nee.estudiante.persona}"

    def clean(self):
        if self.consentimiento_estudiante and not self.fecha_consentimiento:
            raise ValidationError({"fecha_consentimiento": "Indique la fecha en que el estudiante dio su consentimiento."})


class AdaptacionDocente(ValidarAlGuardarMixin, models.Model):
    """Autoriza a un docente a ver una adaptación. El docente nunca ve la sesión ni el diagnóstico."""

    adaptacion = models.ForeignKey(AdaptacionCurricular, on_delete=models.PROTECT, related_name="asignaciones")
    docente = models.ForeignKey(Docente, on_delete=models.PROTECT, related_name="asignaciones")
    asignatura = models.CharField(max_length=150)
    fecha_asignacion = models.DateTimeField("fecha de asignación", auto_now_add=True)
    fecha_lectura = models.DateTimeField("fecha de lectura", null=True, blank=True)

    class Meta:
        verbose_name = "adaptación asignada a docente"
        verbose_name_plural = "adaptaciones asignadas a docentes"
        constraints = [
            models.UniqueConstraint(fields=["adaptacion", "docente"], name="uq_adaptacion_docente"),
        ]

    def __str__(self):
        return f"{self.docente} - {self.asignatura}"

    def clean(self):
        if self.adaptacion_id and not self.adaptacion.consentimiento_estudiante:
            raise ValidationError("La adaptación no cuenta con el consentimiento del estudiante.")

    @property
    def leida(self):
        return self.fecha_lectura is not None


class ObservacionDocente(models.Model):
    adaptacion_docente = models.ForeignKey(AdaptacionDocente, on_delete=models.PROTECT, related_name="observaciones")
    fecha = models.DateField(auto_now_add=True)
    descripcion = models.TextField("descripción")

    class Meta:
        verbose_name = "observación docente"
        verbose_name_plural = "observaciones docentes"
        ordering = ["-fecha"]

    def __str__(self):
        return f"Observación {self.fecha:%d/%m/%Y} - {self.adaptacion_docente.docente}"