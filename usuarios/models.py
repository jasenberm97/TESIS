from datetime import date

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .comun import ValidarAlGuardarMixin, check_constraint, validar_cedula, validar_perfil_unico


# =====================================================================
# Persona: datos personales comunes a todos los perfiles
# =====================================================================
class Persona(models.Model):
    class Sexo(models.TextChoices):
        MASCULINO = "M", "Masculino"
        FEMENINO = "F", "Femenino"

    nombres = models.CharField(max_length=100)
    apellidos = models.CharField(max_length=100)
    fecha_nacimiento = models.DateField("fecha de nacimiento")
    sexo = models.CharField(max_length=1, choices=Sexo.choices)
    telefono = models.CharField("teléfono", max_length=15, blank=True)

    class Meta:
        verbose_name = "persona"
        verbose_name_plural = "personas"
        ordering = ["apellidos", "nombres"]
        constraints = [
            check_constraint(Q(sexo__in=["M", "F"]), "ck_persona_sexo"),
        ]

    def __str__(self):
        return self.nombre_completo

    @property
    def nombre_completo(self):
        return f"{self.nombres} {self.apellidos}".strip()

    @property
    def primer_nombre(self):
        return self.nombres.split()[0] if self.nombres else ""

    @property
    def nombre_corto(self):
        """Primer nombre y primer apellido. Ej.: 'Maribel Segura'."""
        primer_apellido = self.apellidos.split()[0] if self.apellidos else ""
        return f"{self.primer_nombre} {primer_apellido}".strip()

    @property
    def edad(self):
        """Dato derivado: no se guarda, se calcula desde la fecha de nacimiento."""
        hoy = date.today()
        return hoy.year - self.fecha_nacimiento.year - (
            (hoy.month, hoy.day) < (self.fecha_nacimiento.month, self.fecha_nacimiento.day)
        )


# =====================================================================
# Usuario: credenciales y acceso (inicio de sesión con cédula)
# =====================================================================
class UsuarioManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, cedula, email, password, **extra_fields):
        if not cedula:
            raise ValueError("La cédula es obligatoria.")
        if not email:
            raise ValueError("El correo electrónico es obligatorio.")
        email = self.normalize_email(email)
        usuario = self.model(cedula=cedula, email=email, **extra_fields)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_user(self, cedula, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("rol", Usuario.Rol.ESTUDIANTE)
        return self._create_user(cedula, email, password, **extra_fields)

    def create_superuser(self, cedula, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("rol", Usuario.Rol.ADMINISTRADOR)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("El superusuario debe tener is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("El superusuario debe tener is_superuser=True.")
        return self._create_user(cedula, email, password, **extra_fields)


class Usuario(AbstractUser):
    class Rol(models.TextChoices):
        ADMINISTRADOR = "ADMIN", "Administrador"
        ESTUDIANTE = "ESTUDIANTE", "Estudiante"
        DOCENTE = "DOCENTE", "Docente"
        PERSONAL = "PERSONAL", "Personal de atención"

    # Se quitan los campos de AbstractUser que duplicarían datos de Persona (3FN)
    username = None
    first_name = None
    last_name = None

    cedula = models.CharField("cédula", max_length=10, unique=True, validators=[validar_cedula])
    email = models.EmailField("correo electrónico", unique=True)
    rol = models.CharField(max_length=10, choices=Rol.choices, default=Rol.ESTUDIANTE)
    # Obligatoria para todos los usuarios del sistema; se permite vacía solo
    # para el superusuario técnico creado con "createsuperuser".
    persona = models.OneToOneField(
        Persona, on_delete=models.PROTECT, null=True, blank=True, related_name="usuario"
    )

    USERNAME_FIELD = "cedula"
    REQUIRED_FIELDS = ["email"]

    objects = UsuarioManager()

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        constraints = [
            check_constraint(Q(rol__in=["ADMIN", "ESTUDIANTE", "DOCENTE", "PERSONAL"]), "ck_usuario_rol"),
        ]

    def __str__(self):
        return f"{self.cedula} - {self.perfil}"

    def get_full_name(self):
        return self.persona.nombre_completo if self.persona_id else self.cedula

    def get_short_name(self):
        return self.persona.nombres if self.persona_id else self.cedula

    @property
    def perfil(self):
        """Los 6 perfiles funcionales: el rol más el área del personal."""
        if self.rol == self.Rol.PERSONAL and self.persona_id and hasattr(self.persona, "personal"):
            return f"Personal de {self.persona.personal.area.nombre}"
        return self.get_rol_display()


# =====================================================================
# Áreas de atención y personal de Bienestar Universitario
# =====================================================================
class Area(models.Model):
    nombre = models.CharField(max_length=50, unique=True)

    class Meta:
        verbose_name = "área"
        verbose_name_plural = "áreas"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Personal(ValidarAlGuardarMixin, models.Model):
    persona = models.OneToOneField(Persona, on_delete=models.PROTECT, related_name="personal")
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="personal")
    cargo = models.CharField(max_length=100, help_text="Ej.: Médico general, Odontólogo, Psicólogo clínico")
    registro_profesional = models.CharField("registro profesional", max_length=50, blank=True)
    fecha_ingreso = models.DateField("fecha de ingreso")
    fecha_salida = models.DateField("fecha de salida", null=True, blank=True)

    class Meta:
        verbose_name = "personal de atención"
        verbose_name_plural = "personal de atención"
        ordering = ["area__nombre", "persona__apellidos"]
        constraints = [
            check_constraint(
                Q(fecha_salida__isnull=True) | Q(fecha_salida__gte=models.F("fecha_ingreso")),
                "ck_personal_fechas",
            ),
        ]

    def __str__(self):
        return f"{self.persona} ({self.area})"

    def clean(self):
        validar_perfil_unico(self)
        if self.fecha_salida and self.fecha_ingreso and self.fecha_salida < self.fecha_ingreso:
            raise ValidationError({"fecha_salida": "La fecha de salida no puede ser anterior a la de ingreso."})

    @property
    def esta_activo(self):
        """Un profesional retirado conserva su historial pero no recibe nuevas citas."""
        usuario = getattr(self.persona, "usuario", None)
        cuenta_activa = usuario.is_active if usuario else False
        return cuenta_activa and self.fecha_salida is None