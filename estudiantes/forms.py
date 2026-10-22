# estudiantes/forms.py
import re
from datetime import date

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError

from usuarios.models import Persona
from .models import Carrera, Facultad

Usuario = get_user_model()

SOLO_LETRAS = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' ]+$")
CELULAR_EC = re.compile(r"^09\d{8}$")
EDAD_MINIMA, EDAD_MAXIMA = 15, 80
SEMESTRES = [(n, f"{n}.º semestre") for n in range(1, 11)]  # CAMBIO


class RegistroEstudianteForm(forms.Form):
    # --- Cuenta ---
    cedula = forms.CharField(label="Cédula", max_length=10)
    correo = forms.EmailField(label="Correo electrónico")
    password1 = forms.CharField(
        label="Contraseña", strip=False, widget=forms.PasswordInput
    )
    password2 = forms.CharField(
        label="Confirmar contraseña", strip=False, widget=forms.PasswordInput
    )

    # --- Datos personales ---
    nombres = forms.CharField(label="Nombres", max_length=100)
    apellidos = forms.CharField(label="Apellidos", max_length=100)
    fecha_nacimiento = forms.DateField(
        label="Fecha de nacimiento",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    sexo = forms.ChoiceField(label="Sexo", choices=[])
    telefono = forms.CharField(label="Teléfono celular", max_length=10)

    # --- Datos académicos ---
    facultad = forms.ModelChoiceField(
        label="Facultad",
        queryset=Facultad.objects.order_by("nombre"),
        empty_label="Seleccione una facultad",
    )
    carrera = forms.ModelChoiceField(
        label="Carrera",
        queryset=Carrera.objects.select_related("facultad").order_by("nombre"),
        empty_label="Seleccione una carrera",
    )
    semestre = forms.TypedChoiceField(  # CAMBIO
        label="Semestre",
        choices=[("", "Seleccione el semestre")] + SEMESTRES,
        coerce=int,
    )

    # --- Consentimiento (LOPDP) ---
    acepta_tratamiento_datos = forms.BooleanField(
        label="Autorizo el tratamiento de mis datos personales conforme a la "
              "Ley Orgánica de Protección de Datos Personales.",
        error_messages={"required": "Debe aceptar el tratamiento de datos para registrarse."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        opciones = Persona._meta.get_field("sexo").choices
        self.fields["sexo"].choices = [("", "Seleccione")] + list(opciones)

    # ---------- Validaciones por campo ----------

    def clean_cedula(self):
        cedula = self.cleaned_data["cedula"].strip()
        # Ejecuta validar_cedula (módulo 10) definido en el modelo
        Usuario._meta.get_field("cedula").run_validators(cedula)
        if Usuario.objects.filter(cedula=cedula).exists():
            raise ValidationError("Ya existe una cuenta registrada con esta cédula.")
        return cedula

    def clean_correo(self):
        correo = self.cleaned_data["correo"].strip().lower()
        if Usuario.objects.filter(email__iexact=correo).exists():  # CAMBIO
            raise ValidationError("Este correo ya está registrado.")
        return correo

    def _limpiar_nombre(self, campo):
        valor = " ".join(self.cleaned_data[campo].split())
        if not SOLO_LETRAS.match(valor):
            raise ValidationError("Solo se permiten letras y espacios.")
        return valor.title()

    def clean_nombres(self):
        return self._limpiar_nombre("nombres")

    def clean_apellidos(self):
        return self._limpiar_nombre("apellidos")

    def clean_fecha_nacimiento(self):
        fn = self.cleaned_data["fecha_nacimiento"]
        hoy = date.today()
        if fn >= hoy:
            raise ValidationError("La fecha de nacimiento debe ser anterior a hoy.")
        edad = hoy.year - fn.year - ((hoy.month, hoy.day) < (fn.month, fn.day))
        if not EDAD_MINIMA <= edad <= EDAD_MAXIMA:
            raise ValidationError(
                f"La edad debe estar entre {EDAD_MINIMA} y {EDAD_MAXIMA} años."
            )
        return fn

    def clean_telefono(self):
        telefono = self.cleaned_data["telefono"].strip()
        if not CELULAR_EC.match(telefono):
            raise ValidationError("Ingrese un celular válido de 10 dígitos (09XXXXXXXX).")
        return telefono

    # ---------- Validaciones entre campos ----------

    def clean(self):
        datos = super().clean()

        p1, p2 = datos.get("password1"), datos.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "Las contraseñas no coinciden.")
            else:
                temporal = Usuario(cedula=datos.get("cedula"), email=datos.get("correo"))  # CAMBIO
                try:
                    password_validation.validate_password(p1, user=temporal)
                except ValidationError as e:
                    self.add_error("password1", e)

        facultad, carrera = datos.get("facultad"), datos.get("carrera")
        if facultad and carrera and carrera.facultad_id != facultad.id:
            self.add_error("carrera", "La carrera no pertenece a la facultad seleccionada.")

        return datos

class PerfilEstudianteForm(forms.Form):
    """Datos de contacto que el estudiante puede actualizar por sí mismo."""

    correo = forms.EmailField(label="Correo electrónico")
    telefono = forms.CharField(label="Teléfono celular", max_length=10)

    def __init__(self, *args, usuario, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)

    def clean_correo(self):
        correo = self.cleaned_data["correo"].strip().lower()
        # El correo no puede pertenecer a OTRA cuenta (el suyo propio sí se permite)
        if Usuario.objects.filter(email__iexact=correo).exclude(pk=self.usuario.pk).exists():
            raise ValidationError("Este correo ya está registrado en otra cuenta.")
        return correo

    def clean_telefono(self):
        telefono = self.cleaned_data["telefono"].strip()
        if not CELULAR_EC.match(telefono):
            raise ValidationError("Ingrese un celular válido de 10 dígitos (09XXXXXXXX).")
        return telefono