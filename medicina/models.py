from datetime import timedelta

from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from citas.models import Cita
from usuarios.comun import (
    AREA_MEDICINA,
    ValidarAlGuardarMixin,
    check_constraint,
    guardar_con_codigo,
    validar_area_de_cita,
)


# =====================================================================
# Consulta médica
# =====================================================================
class ConsultaMedica(ValidarAlGuardarMixin, models.Model):
    cita = models.OneToOneField(Cita, on_delete=models.PROTECT, related_name="consulta_medica")
    motivo_consulta = models.TextField("motivo de consulta")
    anamnesis = models.TextField(blank=True)
    presion_arterial = models.CharField("presión arterial", max_length=10, blank=True, help_text="Ej.: 120/80")
    temperatura = models.DecimalField("temperatura (°C)", max_digits=4, decimal_places=1, null=True, blank=True)
    frecuencia_cardiaca = models.PositiveSmallIntegerField("frecuencia cardíaca (lpm)", null=True, blank=True)
    peso = models.DecimalField("peso (kg)", max_digits=5, decimal_places=2, null=True, blank=True)
    talla = models.DecimalField("talla (m)", max_digits=4, decimal_places=2, null=True, blank=True)
    diagnostico = models.TextField("diagnóstico")
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "consulta médica"
        verbose_name_plural = "consultas médicas"
        constraints = [
            check_constraint(Q(temperatura__isnull=True) | Q(temperatura__gte=30, temperatura__lte=45),
                             "ck_consulta_temperatura"),
            check_constraint(Q(peso__isnull=True) | Q(peso__gt=0), "ck_consulta_peso"),
            check_constraint(Q(talla__isnull=True) | Q(talla__gt=0), "ck_consulta_talla"),
        ]

    def __str__(self):
        return f"Consulta médica - {self.cita}"

    def clean(self):
        validar_area_de_cita(self.cita if self.cita_id else None, AREA_MEDICINA)

    @property
    def imc(self):
        """Dato derivado: índice de masa corporal, no se guarda."""
        if self.peso and self.talla:
            return round(float(self.peso) / float(self.talla) ** 2, 2)
        return None


# =====================================================================
# Receta
# =====================================================================
class Medicamento(models.Model):
    nombre = models.CharField(max_length=150)
    presentacion = models.CharField("presentación", max_length=100, help_text="Ej.: Tabletas, Ampollas, Jarabe")

    class Meta:
        verbose_name = "medicamento"
        verbose_name_plural = "medicamentos"
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(fields=["nombre", "presentacion"], name="uq_medicamento"),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.presentacion})"


class Receta(models.Model):
    consulta_medica = models.ForeignKey(ConsultaMedica, on_delete=models.PROTECT, related_name="recetas")
    fecha_emision = models.DateField("fecha de emisión", auto_now_add=True)
    indicaciones_generales = models.TextField("indicaciones generales", blank=True)

    class Meta:
        verbose_name = "receta"
        verbose_name_plural = "recetas"
        ordering = ["-fecha_emision"]

    def __str__(self):
        return f"Receta {self.pk} - {self.fecha_emision:%d/%m/%Y}"


class DetalleReceta(models.Model):
    receta = models.ForeignKey(Receta, on_delete=models.CASCADE, related_name="detalles")
    medicamento = models.ForeignKey(Medicamento, on_delete=models.PROTECT, related_name="detalles_receta")
    cantidad = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    via_administracion = models.CharField("vía de administración", max_length=30, help_text="Ej.: Oral, Intramuscular")
    indicacion = models.CharField("indicación", max_length=255, help_text="Ej.: 1 cada 8 horas")

    class Meta:
        verbose_name = "detalle de receta"
        verbose_name_plural = "detalles de receta"
        constraints = [
            models.UniqueConstraint(fields=["receta", "medicamento"], name="uq_detalle_receta"),
            check_constraint(Q(cantidad__gt=0), "ck_detalle_receta_cantidad"),
        ]

    def __str__(self):
        return f"{self.medicamento} #{self.cantidad}"


# =====================================================================
# Certificado médico
# =====================================================================
class CertificadoMedico(models.Model):
    consulta_medica = models.ForeignKey(ConsultaMedica, on_delete=models.PROTECT, related_name="certificados")
    codigo = models.CharField(
        "código", max_length=20, unique=True, blank=True, editable=False,
        help_text="Se genera automáticamente con el formato CM-AAAA-00001.",
    )
    fecha_emision = models.DateField("fecha de emisión", auto_now_add=True)
    fecha_inicio_reposo = models.DateField("inicio del reposo")
    dias_reposo = models.PositiveSmallIntegerField("días de reposo", validators=[MinValueValidator(1)])
    motivo = models.TextField()

    class Meta:
        verbose_name = "certificado médico"
        verbose_name_plural = "certificados médicos"
        ordering = ["-fecha_emision"]
        constraints = [
            check_constraint(Q(dias_reposo__gt=0), "ck_certificado_dias"),
        ]

    def __str__(self):
        return self.codigo

    def save(self, *args, **kwargs):
        guardar_con_codigo(self, "codigo", "CM", lambda: super(CertificadoMedico, self).save(*args, **kwargs))

    @property
    def fecha_fin_reposo(self):
        """Dato derivado: no se guarda, se calcula."""
        return self.fecha_inicio_reposo + timedelta(days=self.dias_reposo - 1)


# =====================================================================
# Pedido de exámenes de laboratorio
# =====================================================================
class CategoriaExamen(models.Model):
    nombre = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "categoría de examen"
        verbose_name_plural = "categorías de examen"
        ordering = ["id"]

    def __str__(self):
        return self.nombre


class Examen(models.Model):
    nombre = models.CharField(max_length=150)
    categoria = models.ForeignKey(CategoriaExamen, on_delete=models.PROTECT, related_name="examenes")

    class Meta:
        verbose_name = "examen"
        verbose_name_plural = "exámenes"
        ordering = ["categoria_id", "id"]
        constraints = [
            models.UniqueConstraint(fields=["categoria", "nombre"], name="uq_examen"),
        ]

    def __str__(self):
        return self.nombre


class OrdenExamen(models.Model):
    consulta_medica = models.ForeignKey(ConsultaMedica, on_delete=models.PROTECT, related_name="ordenes_examen")
    fecha_emision = models.DateField("fecha de emisión", auto_now_add=True)
    examenes = models.ManyToManyField(Examen, through="DetalleOrdenExamen", related_name="ordenes")
    observaciones = models.TextField(blank=True)
    laboratorio = models.CharField(max_length=150, blank=True, help_text="Laboratorio externo donde se realizó.")
    fecha_recepcion = models.DateField("fecha de recepción de resultados", null=True, blank=True)
    interpretacion = models.TextField("interpretación del médico", blank=True)

    class Meta:
        verbose_name = "orden de exámenes"
        verbose_name_plural = "órdenes de exámenes"
        ordering = ["-fecha_emision"]
        constraints = [
            check_constraint(
                Q(fecha_recepcion__isnull=True) | Q(fecha_recepcion__gte=F("fecha_emision")),
                "ck_orden_fechas",
            ),
        ]

    def __str__(self):
        return f"Orden {self.pk} - {self.estado}"

    @property
    def estado(self):
        """Dato derivado: depende de si ya se recibieron los resultados."""
        return "Resultados recibidos" if self.fecha_recepcion else "Emitida"


class DetalleOrdenExamen(models.Model):
    orden = models.ForeignKey(OrdenExamen, on_delete=models.CASCADE, related_name="detalles")
    examen = models.ForeignKey(Examen, on_delete=models.PROTECT, related_name="detalles_orden")

    class Meta:
        verbose_name = "examen solicitado"
        verbose_name_plural = "exámenes solicitados"
        constraints = [
            models.UniqueConstraint(fields=["orden", "examen"], name="uq_detalle_orden"),
        ]

    def __str__(self):
        return str(self.examen)


class ArchivoResultado(models.Model):
    orden = models.ForeignKey(OrdenExamen, on_delete=models.PROTECT, related_name="archivos")
    archivo = models.FileField(
        upload_to="resultados_examenes/%Y/%m/",
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
    )
    fecha_subida = models.DateTimeField("fecha de subida", auto_now_add=True)

    class Meta:
        verbose_name = "archivo de resultados"
        verbose_name_plural = "archivos de resultados"

    def __str__(self):
        return self.archivo.name