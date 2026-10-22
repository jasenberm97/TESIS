"""
Utilidades compartidas por todas las apps del sistema:
validación de cédula, perfil único, restricciones CHECK y
generación de códigos secuenciales.
"""
import re

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


# ---------------------------------------------------------------------
# Cédula ecuatoriana (algoritmo módulo 10)
# ---------------------------------------------------------------------
def validar_cedula(valor):
    """Valida una cédula ecuatoriana de 10 dígitos con su dígito verificador."""
    if not re.fullmatch(r"\d{10}", valor or ""):
        raise ValidationError("La cédula debe tener exactamente 10 dígitos.")

    provincia = int(valor[:2])
    if not (1 <= provincia <= 24 or provincia == 30):
        raise ValidationError("Los dos primeros dígitos de la cédula no corresponden a una provincia.")

    if int(valor[2]) >= 6:
        raise ValidationError("El tercer dígito de la cédula no es válido.")

    coeficientes = [2, 1, 2, 1, 2, 1, 2, 1, 2]
    total = 0
    for digito, coeficiente in zip(valor[:9], coeficientes):
        producto = int(digito) * coeficiente
        total += producto - 9 if producto > 9 else producto

    verificador = (10 - total % 10) % 10
    if verificador != int(valor[9]):
        raise ValidationError("La cédula ingresada no es válida.")


# ---------------------------------------------------------------------
# Restricciones CHECK compatibles con Django 4.x y 5.x/6.x
# (desde Django 5.1 el parámetro se llama "condition" en vez de "check")
# ---------------------------------------------------------------------
def check_constraint(condicion, nombre):
    try:
        return models.CheckConstraint(condition=condicion, name=nombre)
    except TypeError:
        return models.CheckConstraint(check=condicion, name=nombre)


# ---------------------------------------------------------------------
# Validar las reglas de negocio también al guardar desde el código
# (Django solo ejecuta clean() en formularios y en el admin)
# ---------------------------------------------------------------------
class ValidarAlGuardarMixin:
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------
# Regla: una persona tiene un solo perfil (estudiante, docente o personal)
# ---------------------------------------------------------------------
PERFILES = (
    ("estudiantes", "Estudiante", "estudiante"),
    ("estudiantes", "Docente", "docente"),
    ("usuarios", "Personal", "personal de atención"),
)


def validar_perfil_unico(instancia):
    if not instancia.persona_id:
        return
    for app_label, modelo_nombre, descripcion in PERFILES:
        modelo = apps.get_model(app_label, modelo_nombre)
        consulta = modelo.objects.filter(persona_id=instancia.persona_id)
        if isinstance(instancia, modelo):
            consulta = consulta.exclude(pk=instancia.pk)
            if consulta.exists():
                raise ValidationError({"persona": "Esta persona ya tiene este perfil registrado."})
        elif consulta.exists():
            raise ValidationError(
                {"persona": f"Esta persona ya está registrada como {descripcion}. "
                            "Cada persona tiene un solo perfil en el sistema."}
            )


# ---------------------------------------------------------------------
# Códigos secuenciales por año: PREFIJO-AAAA-00001
# Debe llamarse dentro de transaction.atomic()
# ---------------------------------------------------------------------
def generar_codigo_secuencial(modelo, campo, prefijo):
    base = f"{prefijo}-{timezone.now().year}-"
    ultimo = (
        modelo.objects.select_for_update()
        .filter(**{f"{campo}__startswith": base})
        .order_by(f"-{campo}")
        .values_list(campo, flat=True)
        .first()
    )
    siguiente = int(ultimo.rsplit("-", 1)[1]) + 1 if ultimo else 1
    return f"{base}{siguiente:05d}"


def guardar_con_codigo(instancia, campo, prefijo, guardar):
    """Genera el código (si falta), valida y guarda en una sola transacción."""
    with transaction.atomic():
        if not getattr(instancia, campo):
            setattr(instancia, campo, generar_codigo_secuencial(type(instancia), campo, prefijo))
        instancia.full_clean()
        guardar()


# Nombres de las áreas tal como se cargan en la base de datos
AREA_MEDICINA = "Medicina"
AREA_ODONTOLOGIA = "Odontología"
AREA_PSICOLOGIA = "Psicología"


def validar_area_de_cita(cita, area_esperada):
    """Comprueba que la cita corresponda al área de la atención que se registra."""
    if cita is None:
        return
    area = cita.horario.personal.area.nombre
    if area != area_esperada:
        raise ValidationError(
            {"cita": f"Esta cita pertenece al área de {area}, no a {area_esperada}."}
        )