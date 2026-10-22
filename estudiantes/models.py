from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from usuarios.comun import ValidarAlGuardarMixin, check_constraint, guardar_con_codigo, validar_perfil_unico
from usuarios.models import Persona


class Facultad(models.Model):
    nombre = models.CharField(max_length=150, unique=True)

    class Meta:
        verbose_name = "facultad"
        verbose_name_plural = "facultades"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Carrera(models.Model):
    nombre = models.CharField(max_length=150)
    facultad = models.ForeignKey(Facultad, on_delete=models.PROTECT, related_name="carreras")

    class Meta:
        verbose_name = "carrera"
        verbose_name_plural = "carreras"
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(fields=["facultad", "nombre"], name="uq_carrera"),
        ]

    def __str__(self):
        return self.nombre


class Estudiante(models.Model):
    class EstadoAcademico(models.TextChoices):
        ACTIVO = "ACTIVO", "Activo"
        SUSPENDIDO = "SUSPENDIDO", "Suspendido"
        RETIRADO = "RETIRADO", "Retirado"
        EGRESADO = "EGRESADO", "Egresado"

    persona = models.OneToOneField(Persona, on_delete=models.PROTECT, related_name="estudiante")
    codigo_estudiantil = models.CharField(
        "código estudiantil", max_length=20, unique=True, blank=True, editable=False,
        help_text="Se genera automáticamente con el formato UAE-AAAA-00001.",
    )
    carrera = models.ForeignKey(Carrera, on_delete=models.PROTECT, related_name="estudiantes")
    semestre = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)])
    estado_academico = models.CharField(
        "estado académico", max_length=10, choices=EstadoAcademico.choices, default=EstadoAcademico.ACTIVO
    )
    # Prueba del consentimiento LOPDP aceptado en el registro
    fecha_consentimiento = models.DateTimeField(
        "fecha de consentimiento de datos", null=True, blank=True, editable=False,
        help_text="Fecha y hora en que el estudiante aceptó el aviso de privacidad.",
    )

    class Meta:
        verbose_name = "estudiante"
        verbose_name_plural = "estudiantes"
        ordering = ["persona__apellidos", "persona__nombres"]
        constraints = [
            check_constraint(Q(semestre__gte=1, semestre__lte=10), "ck_estudiante_semestre"),
        ]

    def __str__(self):
        return f"{self.codigo_estudiantil} - {self.persona}"

    def clean(self):
        validar_perfil_unico(self)

    def save(self, *args, **kwargs):
        # El código se genera una sola vez y nunca se modifica
        guardar_con_codigo(self, "codigo_estudiantil", "UAE", lambda: super(Estudiante, self).save(*args, **kwargs))


class Docente(ValidarAlGuardarMixin, models.Model):
    persona = models.OneToOneField(Persona, on_delete=models.PROTECT, related_name="docente")
    facultad = models.ForeignKey(Facultad, on_delete=models.PROTECT, related_name="docentes")
    titulo_academico = models.CharField("título académico", max_length=150, blank=True)

    class Meta:
        verbose_name = "docente"
        verbose_name_plural = "docentes"
        ordering = ["persona__apellidos", "persona__nombres"]

    def __str__(self):
        return str(self.persona)

    def clean(self):
        validar_perfil_unico(self)