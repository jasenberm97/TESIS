import re
from datetime import date

from django import forms
from django.contrib.auth import get_user_model

from estudiantes.models import Carrera, Estudiante, Facultad
from .comun import validar_cedula
from .models import Area, Persona

Usuario = get_user_model()


class CampoFecha(forms.DateInput):
    """Selector de fecha nativo del navegador."""
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


def calcular_edad(fecha):
    hoy = date.today()
    return hoy.year - fecha.year - ((hoy.month, hoy.day) < (fecha.month, fecha.day))


# =====================================================================
# Datos comunes: persona + cuenta (crear y editar cualquier perfil)
# =====================================================================
class DatosCuentaForm(forms.Form):
    EDAD_MINIMA = 18
    EDAD_MAXIMA = 80

    nombres = forms.CharField(max_length=100)
    apellidos = forms.CharField(max_length=100)
    cedula = forms.CharField(label="Cédula", max_length=10, validators=[validar_cedula])
    fecha_nacimiento = forms.DateField(label="Fecha de nacimiento", widget=CampoFecha())
    sexo = forms.ChoiceField(choices=[("", "Seleccione...")] + list(Persona.Sexo.choices))
    correo = forms.EmailField(label="Correo electrónico")
    telefono = forms.CharField(
        label="Celular", max_length=10, required=False, help_text="Formato: 09XXXXXXXX"
    )

    def __init__(self, *args, usuario=None, **kwargs):
        """usuario = None al crear; al editar, el usuario que se está modificando. """
        super().__init__(*args, **kwargs)
        self.usuario_actual = usuario
        if usuario is not None:
            # Al editar, la cédula no se modifica: es la identidad y el usuario de acceso.
            # Un campo deshabilitado toma su valor de "initial" (no del POST),
            # por eso se fija aquí la cédula real del usuario.
            self.initial["cedula"] = usuario.cedula
            self.fields["cedula"].disabled = True
            self.fields["cedula"].help_text = "La cédula no se puede modificar."

    def _otros_usuarios(self):
        """Todos los usuarios menos el que se está editando (para validar duplicados)."""
        usuarios = Usuario.objects.all()
        if self.usuario_actual is not None:
            usuarios = usuarios.exclude(pk=self.usuario_actual.pk)
        return usuarios

    def clean_nombres(self):
        return " ".join(self.cleaned_data["nombres"].split())

    def clean_apellidos(self):
        return " ".join(self.cleaned_data["apellidos"].split())

    def clean_cedula(self):
        cedula = self.cleaned_data["cedula"].strip()
        if self._otros_usuarios().filter(cedula=cedula).exists():
            raise forms.ValidationError("Ya existe un usuario registrado con esta cédula.")
        return cedula

    def clean_correo(self):
        correo = self.cleaned_data["correo"].strip().lower()
        if self._otros_usuarios().filter(email__iexact=correo).exists():
            raise forms.ValidationError("Este correo ya está registrado en otra cuenta.")
        return correo

    def clean_telefono(self):
        telefono = self.cleaned_data["telefono"].strip()
        if telefono and not re.fullmatch(r"09\d{8}", telefono):
            raise forms.ValidationError("Ingrese un celular válido de 10 dígitos que empiece con 09.")
        return telefono

    def clean_fecha_nacimiento(self):
        fecha = self.cleaned_data["fecha_nacimiento"]
        edad = calcular_edad(fecha)
        if edad < self.EDAD_MINIMA or edad > self.EDAD_MAXIMA:
            raise forms.ValidationError(
                f"La edad debe estar entre {self.EDAD_MINIMA} y {self.EDAD_MAXIMA} años."
            )
        return fecha


# =====================================================================
# Personal de atención (Medicina, Odontología, Psicología)
# =====================================================================
class CrearPersonalForm(DatosCuentaForm):
    area = forms.ModelChoiceField(
        label="Área", queryset=Area.objects.all(), empty_label="Seleccione..."
    )
    cargo = forms.CharField(
        max_length=100, help_text="Ej.: Médico general, Odontólogo, Psicólogo clínico"
    )
    registro_profesional = forms.CharField(
        label="Registro profesional", max_length=50, required=False,
        help_text="Número de registro del título (opcional)",
    )
    fecha_ingreso = forms.DateField(
        label="Fecha de ingreso", widget=CampoFecha(), initial=date.today
    )

    def clean_fecha_ingreso(self):
        fecha = self.cleaned_data["fecha_ingreso"]
        if fecha > date.today():
            raise forms.ValidationError("La fecha de ingreso no puede ser futura.")
        return fecha


class EditarPersonalForm(CrearPersonalForm):
    fecha_salida = forms.DateField(
        label="Fecha de salida", widget=CampoFecha(), required=False,
        help_text="Solo si el profesional dejó de trabajar en Bienestar",
    )

    def clean(self):
        datos = super().clean()
        ingreso = datos.get("fecha_ingreso")
        salida = datos.get("fecha_salida")
        if ingreso and salida and salida < ingreso:
            self.add_error("fecha_salida", "La fecha de salida no puede ser anterior a la de ingreso.")
        return datos


# =====================================================================
# Docente (el mismo formulario sirve para crear y editar)
# =====================================================================
class CrearDocenteForm(DatosCuentaForm):
    facultad = forms.ModelChoiceField(
        label="Facultad", queryset=Facultad.objects.all(), empty_label="Seleccione..."
    )
    titulo_academico = forms.CharField(
        label="Título académico", max_length=150, required=False,
        help_text="Ej.: Ingeniero Agrónomo, Magíster en Economía",
    )


# =====================================================================
# Estudiante (solo edición: los estudiantes se registran por su cuenta)
# =====================================================================
class CampoCarrera(forms.ModelChoiceField):
    def label_from_instance(self, carrera):
        return f"{carrera.nombre} — {carrera.facultad.nombre}"


class EditarEstudianteForm(DatosCuentaForm):
    EDAD_MINIMA = 15

    carrera = CampoCarrera(
        queryset=Carrera.objects.select_related("facultad").order_by("facultad__nombre", "nombre"),
        empty_label="Seleccione...",
    )
    semestre = forms.TypedChoiceField(
        coerce=int, choices=[(i, f"{i}.º semestre") for i in range(1, 11)]
    )
    estado_academico = forms.ChoiceField(
        label="Estado académico", choices=Estudiante.EstadoAcademico.choices
    )