from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from citas.models import Cita
from usuarios.comun import (
    AREA_MEDICINA,
    ValidarAlGuardarMixin,
    check_constraint,
    guardar_con_codigo,
    validar_area_de_cita,
)

# Estados de cita que no pueden tener una consulta registrada
ESTADOS_CITA_SIN_ATENCION = ("Cancelada", "Reprogramada", "No asistió")


# =====================================================================
# Antecedentes médicos (una ficha por estudiante, se actualiza)
# =====================================================================
class AntecedentesMedicos(models.Model):
    class TipoSangre(models.TextChoices):
        A_POS = "A+", "A+"
        A_NEG = "A-", "A-"
        B_POS = "B+", "B+"
        B_NEG = "B-", "B-"
        AB_POS = "AB+", "AB+"
        AB_NEG = "AB-", "AB-"
        O_POS = "O+", "O+"
        O_NEG = "O-", "O-"

    estudiante = models.OneToOneField(
        "estudiantes.Estudiante", on_delete=models.PROTECT, related_name="antecedentes_medicos"
    )
    tipo_sangre = models.CharField("tipo de sangre", max_length=3, choices=TipoSangre.choices, blank=True)
    alergias = models.TextField(blank=True, help_text="Medicamentos, alimentos u otras. Dejar vacío si no refiere.")
    personales = models.TextField("antecedentes personales patológicos", blank=True)
    quirurgicos = models.TextField("antecedentes quirúrgicos", blank=True)
    familiares = models.TextField("antecedentes familiares", blank=True)
    medicacion_habitual = models.TextField("medicación habitual", blank=True)
    habitos = models.TextField("hábitos", blank=True, help_text="Tabaco, alcohol, actividad física, sueño, etc.")
    gineco_obstetricos = models.TextField("antecedentes gineco-obstétricos", blank=True)
    actualizado_por = models.ForeignKey(
        "usuarios.Personal", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="antecedentes_actualizados",
    )
    fecha_actualizacion = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "antecedentes médicos"
        verbose_name_plural = "antecedentes médicos"

    def __str__(self):
        return f"Antecedentes médicos - {self.estudiante}"


# =====================================================================
# Consulta médica
# =====================================================================
class ConsultaMedica(ValidarAlGuardarMixin, models.Model):
    class Tipo(models.TextChoices):
        PROGRAMADA = "PROGRAMADA", "Con cita"
        ESPONTANEA = "ESPONTANEA", "Sin cita"
        EMERGENCIA = "EMERGENCIA", "Emergencia"

    estudiante = models.ForeignKey(
        "estudiantes.Estudiante", on_delete=models.PROTECT, related_name="consultas_medicas"
    )
    personal = models.ForeignKey(
        "usuarios.Personal", on_delete=models.PROTECT, related_name="consultas_medicas",
        verbose_name="médico",
    )
    cita = models.OneToOneField(
        Cita, on_delete=models.PROTECT, related_name="consulta_medica", null=True, blank=True
    )
    tipo = models.CharField("tipo de atención", max_length=10, choices=Tipo.choices)
    fecha_atencion = models.DateTimeField(
        "fecha y hora de atención", default=timezone.now,
        help_text="En emergencias, la hora real en que se atendió.",
    )

    # Anamnesis y examen
    motivo_consulta = models.TextField("motivo de consulta")
    anamnesis = models.TextField("enfermedad actual", blank=True)
    examen_fisico = models.TextField("examen físico", blank=True)

    # Signos vitales (todos opcionales)
    presion_sistolica = models.PositiveSmallIntegerField(
        "presión sistólica (mmHg)", null=True, blank=True,
        validators=[MinValueValidator(60), MaxValueValidator(250)],
    )
    presion_diastolica = models.PositiveSmallIntegerField(
        "presión diastólica (mmHg)", null=True, blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(150)],
    )
    frecuencia_cardiaca = models.PositiveSmallIntegerField(
        "frecuencia cardíaca (lpm)", null=True, blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(220)],
    )
    frecuencia_respiratoria = models.PositiveSmallIntegerField(
        "frecuencia respiratoria (rpm)", null=True, blank=True,
        validators=[MinValueValidator(8), MaxValueValidator(60)],
    )
    temperatura = models.DecimalField(
        "temperatura (°C)", max_digits=4, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(45)],
    )
    saturacion_oxigeno = models.PositiveSmallIntegerField(
        "saturación de oxígeno (%)", null=True, blank=True,
        validators=[MinValueValidator(50), MaxValueValidator(100)],
    )
    peso = models.DecimalField(
        "peso (kg)", max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(300)],
    )
    talla = models.DecimalField(
        "talla (m)", max_digits=3, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0.5), MaxValueValidator(2.5)],
    )

    # Conclusión
    diagnostico = models.TextField("diagnóstico")
    observaciones = models.TextField("indicaciones y observaciones", blank=True)
    derivacion = models.TextField(
        "derivación", blank=True,
        help_text="Si se refirió al paciente a otra casa de salud: a dónde y por qué.",
    )

    fecha_registro = models.DateTimeField("fecha de registro", auto_now_add=True)
    fecha_modificacion = models.DateTimeField("última modificación", auto_now=True)

    class Meta:
        verbose_name = "consulta médica"
        verbose_name_plural = "consultas médicas"
        ordering = ["-fecha_atencion"]
        indexes = [
            models.Index(fields=["estudiante", "fecha_atencion"], name="ix_consulta_est_fecha"),
        ]
        constraints = [
            # Con cita <=> tipo PROGRAMADA
            check_constraint(
                Q(tipo="PROGRAMADA", cita__isnull=False) | (~Q(tipo="PROGRAMADA") & Q(cita__isnull=True)),
                "ck_consulta_tipo_cita",
            ),
            check_constraint(
                Q(presion_sistolica__isnull=True, presion_diastolica__isnull=True)
                | Q(presion_sistolica__gt=F("presion_diastolica")),
                "ck_consulta_presion",
            ),
            check_constraint(Q(temperatura__isnull=True) | Q(temperatura__gte=30, temperatura__lte=45),
                             "ck_consulta_temperatura"),
            check_constraint(Q(peso__isnull=True) | Q(peso__gt=0), "ck_consulta_peso"),
            check_constraint(Q(talla__isnull=True) | Q(talla__gt=0), "ck_consulta_talla"),
            check_constraint(Q(saturacion_oxigeno__isnull=True) | Q(saturacion_oxigeno__lte=100),
                             "ck_consulta_saturacion"),
        ]

    def __str__(self):
        return f"Consulta médica - {self.estudiante} - {timezone.localtime(self.fecha_atencion):%d/%m/%Y %H:%M}"

    def clean(self):
        validar_area_de_cita(self.cita if self.cita_id else None, AREA_MEDICINA)
        errores = {}

        if self.personal_id and self.personal.area.nombre != AREA_MEDICINA:
            errores["personal"] = "Solo el personal del área de Medicina puede registrar consultas médicas."

        if self.tipo == self.Tipo.PROGRAMADA and not self.cita_id:
            errores["cita"] = "Una atención con cita debe estar vinculada a la cita."
        elif self.tipo and self.tipo != self.Tipo.PROGRAMADA and self.cita_id:
            errores["cita"] = "Una atención sin cita o de emergencia no se vincula a una cita."

        if self.cita_id:
            if self.estudiante_id and self.cita.estudiante_id != self.estudiante_id:
                errores["estudiante"] = "El estudiante no coincide con el de la cita."
            if self.personal_id and self.cita.horario.personal_id != self.personal_id:
                errores["personal"] = "La cita pertenece a otro profesional."
            if self.cita.estado.nombre in ESTADOS_CITA_SIN_ATENCION:
                errores["cita"] = f"No se puede registrar una consulta en una cita {self.cita.estado.nombre.lower()}."

        if self.fecha_atencion and self.fecha_atencion > timezone.now():
            errores["fecha_atencion"] = "La fecha de atención no puede estar en el futuro."

        if (self.presion_sistolica is None) != (self.presion_diastolica is None):
            errores["presion_diastolica"] = "Ingrese ambos valores de la presión arterial o ninguno."
        elif self.presion_sistolica is not None and self.presion_sistolica <= self.presion_diastolica:
            errores["presion_diastolica"] = "La presión diastólica debe ser menor que la sistólica."

        if errores:
            raise ValidationError(errores)

    # ----- Datos derivados (no se guardan) -----
    @property
    def presion_arterial(self):
        if self.presion_sistolica and self.presion_diastolica:
            return f"{self.presion_sistolica}/{self.presion_diastolica}"
        return ""

    @property
    def imc(self):
        if self.peso and self.talla:
            return round(float(self.peso) / float(self.talla) ** 2, 2)
        return None

    @property
    def clasificacion_imc(self):
        imc = self.imc
        if imc is None:
            return ""
        if imc < 18.5:
            return "Bajo peso"
        if imc < 25:
            return "Normal"
        if imc < 30:
            return "Sobrepeso"
        return "Obesidad"

    @property
    def es_editable(self):
        """Solo se edita el mismo día en que se registró; después, notas aclaratorias."""
        return bool(self.fecha_registro) and timezone.localdate(self.fecha_registro) == timezone.localdate()


class NotaAclaratoria(models.Model):
    """Corrección o ampliación de una consulta ya cerrada. No se edita ni se borra."""
    consulta_medica = models.ForeignKey(ConsultaMedica, on_delete=models.PROTECT, related_name="notas")
    texto = models.TextField()
    registrada_por = models.ForeignKey("usuarios.Personal", on_delete=models.PROTECT, related_name="notas_medicas")
    fecha_registro = models.DateTimeField("fecha de registro", auto_now_add=True)

    class Meta:
        verbose_name = "nota aclaratoria"
        verbose_name_plural = "notas aclaratorias"
        ordering = ["fecha_registro"]

    def __str__(self):
        return f"Nota {self.pk} - consulta {self.consulta_medica_id}"


# =====================================================================
# Receta
# =====================================================================
class Medicamento(models.Model):
    nombre = models.CharField(max_length=150, help_text="Nombre genérico. Ej.: Paracetamol")
    concentracion = models.CharField("concentración", max_length=50, blank=True, default="",
                                     help_text="Ej.: 500 mg, 250 mg/5 ml")
    presentacion = models.CharField("presentación", max_length=100, help_text="Ej.: Tabletas, Ampollas, Jarabe")

    class Meta:
        verbose_name = "medicamento"
        verbose_name_plural = "medicamentos"
        ordering = ["nombre", "concentracion"]
        constraints = [
            models.UniqueConstraint(fields=["nombre", "concentracion", "presentacion"], name="uq_medicamento"),
        ]

    def __str__(self):
        partes = [self.nombre, self.concentracion]
        return f"{' '.join(p for p in partes if p)} ({self.presentacion})"


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
    indicacion = models.CharField("indicación", max_length=255, help_text="Ej.: 1 cada 8 horas por 5 días")

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
    """Los archivos se sirven por una vista con permisos, nunca por /media/ público."""
    orden = models.ForeignKey(OrdenExamen, on_delete=models.PROTECT, related_name="archivos")
    archivo = models.FileField(
        upload_to="resultados_examenes/%Y/%m/",
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
    )
    subido_por = models.ForeignKey("usuarios.Personal", on_delete=models.PROTECT, related_name="resultados_subidos")
    fecha_subida = models.DateTimeField("fecha de subida", auto_now_add=True)

    class Meta:
        verbose_name = "archivo de resultados"
        verbose_name_plural = "archivos de resultados"
        ordering = ["fecha_subida"]

    def __str__(self):
        return self.archivo.name