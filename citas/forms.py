from datetime import time, timedelta

from django import forms
from django.utils import timezone

from usuarios.models import Personal

from .models import DURACION_TURNO_MINUTOS, HORA_APERTURA, HORA_CIERRE, Feriado, Horario
MAX_DIAS_LOTE = 184  # unos tres meses

DIAS_SEMANA = [
    (0, "Lunes"),
    (1, "Martes"),
    (2, "Miércoles"),
    (3, "Jueves"),
    (4, "Viernes"),
]

DURACIONES = [
    (15, "15 minutos"),
    (20, "20 minutos"),
    (30, "30 minutos"),
    (45, "45 minutos"),
    (60, "1 hora"),
]


def personal_activo():
    return (
        Personal.objects
        .filter(fecha_salida__isnull=True, persona__usuario__is_active=True)
        .select_related("persona", "area")
        .order_by("area__nombre", "persona__apellidos", "persona__nombres")
    )


class PersonalChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.persona.nombre_completo} — {obj.area}"


def _minutos(hora):
    return hora.hour * 60 + hora.minute


def _campo_fecha(etiqueta, **kwargs):
    return forms.DateField(
        label=etiqueta, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), **kwargs
    )


def _campo_hora(etiqueta, **kwargs):
    return forms.TimeField(
        label=etiqueta, widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"), **kwargs
    )


class GenerarTurnosForm(forms.Form):
    personal = PersonalChoiceField(
        label="Profesional", queryset=Personal.objects.none(),
        empty_label="Seleccione un profesional",
    )
    fecha_desde = _campo_fecha("Fecha de inicio")
    fecha_hasta = _campo_fecha("Fecha de fin", help_text=f"Máximo {MAX_DIAS_LOTE} días por cada generación.")
    dias = forms.TypedMultipleChoiceField(
        label="Días de atención", choices=DIAS_SEMANA, coerce=int,
        widget=forms.CheckboxSelectMultiple, initial=[0, 1, 2, 3, 4],
    )
    inicio_1 = _campo_hora("Hora de inicio", initial=time(8, 30))
    fin_1 = _campo_hora("Hora de fin", initial=time(12, 0))
    inicio_2 = _campo_hora("Hora de inicio", required=False, initial=time(13, 0))
    fin_2 = _campo_hora(
        "Hora de fin", required=False, initial=time(16, 0),
        help_text="Deja las dos horas vacías si solo atiende en una franja.",
    )
    duracion = forms.TypedChoiceField(
        label="Duración de cada turno", choices=DURACIONES, coerce=int,
        initial=DURACION_TURNO_MINUTOS,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["personal"].queryset = personal_activo()
        hoy = timezone.localdate()
        self.fields["fecha_desde"].initial = hoy
        self.fields["fecha_hasta"].initial = hoy + timedelta(days=27)

    def _validar_franja(self, inicio, fin, campo_fin, duracion):
        if inicio < HORA_APERTURA or fin > HORA_CIERRE:
            self.add_error(campo_fin, "La franja debe estar entre las 08:00 y las 16:30.")
        elif fin <= inicio:
            self.add_error(campo_fin, "La hora de fin debe ser posterior a la de inicio.")
        elif duracion and _minutos(fin) - _minutos(inicio) < duracion:
            self.add_error(campo_fin, "La franja es más corta que la duración de un turno.")
        else:
            return True
        return False

    def clean(self):
        datos = super().clean()
        desde, hasta = datos.get("fecha_desde"), datos.get("fecha_hasta")
        duracion = datos.get("duracion")

        if desde and desde < timezone.localdate():
            self.add_error("fecha_desde", "La fecha de inicio no puede ser anterior a hoy.")
        if desde and hasta:
            if hasta < desde:
                self.add_error("fecha_hasta", "Debe ser igual o posterior a la fecha de inicio.")
            elif (hasta - desde).days > MAX_DIAS_LOTE:
                self.add_error("fecha_hasta", f"El rango no puede superar {MAX_DIAS_LOTE} días.")

        franjas = []
        i1, f1 = datos.get("inicio_1"), datos.get("fin_1")
        i2, f2 = datos.get("inicio_2"), datos.get("fin_2")

        if i1 and f1 and self._validar_franja(i1, f1, "fin_1", duracion):
            franjas.append((i1, f1))

        if i2 or f2:
            if not (i2 and f2):
                self.add_error("fin_2", "Completa la hora de inicio y la de fin, o deja ambas vacías.")
            elif self._validar_franja(i2, f2, "fin_2", duracion):
                if f1 and i2 < f1:
                    self.add_error("inicio_2", "La segunda franja debe empezar cuando termine la primera o después.")
                else:
                    franjas.append((i2, f2))

        datos["franjas"] = franjas
        return datos

class EditarHorarioForm(forms.ModelForm):
    """Corrige un turno libre. Las reglas (lunes a viernes, 08:00–16:30, sin cruces) las valida el modelo."""

    class Meta:
        model = Horario
        fields = ["fecha", "hora_inicio", "hora_fin"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "hora_inicio": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "hora_fin": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }

    def clean(self):
        datos = super().clean()
        fecha, inicio = datos.get("fecha"), datos.get("hora_inicio")
        ahora = timezone.localtime()
        if fecha and (fecha < ahora.date() or (fecha == ahora.date() and inicio and inicio <= ahora.time())):
            raise forms.ValidationError("El turno debe quedar en una fecha y hora futuras.")
        return datos


class AusenciaForm(forms.Form):
    COMPLETO = "completo"
    PARCIAL = "parcial"

    fecha_desde = _campo_fecha("Desde")
    fecha_hasta = _campo_fecha("Hasta", help_text="Para un solo día, pon la misma fecha en ambos campos.")
    tipo = forms.ChoiceField(
        label="Duración",
        choices=[(COMPLETO, "Día completo"), (PARCIAL, "Solo una parte del día")],
        widget=forms.RadioSelect, initial=COMPLETO,
    )
    hora_desde = _campo_hora("Hora de inicio", required=False)
    hora_hasta = _campo_hora("Hora de fin", required=False)
    motivo = forms.CharField(
        label="Motivo", max_length=200,
        widget=forms.TextInput(attrs={"placeholder": "Capacitación, permiso médico, vacaciones…"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hoy = timezone.localdate()
        self.fields["fecha_desde"].initial = hoy
        self.fields["fecha_hasta"].initial = hoy

    def clean(self):
        datos = super().clean()
        desde, hasta = datos.get("fecha_desde"), datos.get("fecha_hasta")

        if desde and desde < timezone.localdate():
            self.add_error("fecha_desde", "La fecha de inicio no puede ser anterior a hoy.")
        if desde and hasta:
            if hasta < desde:
                self.add_error("fecha_hasta", "Debe ser igual o posterior a la fecha de inicio.")
            elif (hasta - desde).days > MAX_DIAS_LOTE:
                self.add_error("fecha_hasta", f"El rango no puede superar {MAX_DIAS_LOTE} días.")

        if datos.get("tipo") == self.PARCIAL:
            h_ini, h_fin = datos.get("hora_desde"), datos.get("hora_hasta")
            if not h_ini:
                self.add_error("hora_desde", "Indica la hora de inicio.")
            if not h_fin:
                self.add_error("hora_hasta", "Indica la hora de fin.")
            if h_ini and h_fin:
                if h_ini < HORA_APERTURA or h_fin > HORA_CIERRE:
                    self.add_error("hora_hasta", "Las horas deben estar entre las 08:00 y las 16:30.")
                elif h_fin <= h_ini:
                    self.add_error("hora_hasta", "La hora de fin debe ser posterior a la de inicio.")
        else:
            datos["hora_desde"] = None
            datos["hora_hasta"] = None

        return datos
    
class FeriadoForm(forms.Form):
    fecha = _campo_fecha("Fecha")
    nombre = forms.CharField(
        label="Nombre", max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "Día del Trabajo, Fiestas de Guayaquil, Aniversario UAE…"}),
    )

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if fecha < timezone.localdate():
            raise forms.ValidationError("No se pueden registrar feriados en fechas pasadas.")
        if Feriado.objects.filter(fecha=fecha).exists():
            raise forms.ValidationError("Ese día ya está registrado como feriado.")
        return fecha
    
class AgendarCitaForm(forms.Form):
    motivo = forms.CharField(
        label="Motivo de la consulta", max_length=255,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "placeholder": "Ej.: control general, dolor de muela, orientación personal…",
        }),
        help_text="Escribe una descripción breve. No necesitas dar detalles personales; los conversarás en la consulta.",
    )

    def clean_motivo(self):
        motivo = self.cleaned_data["motivo"].strip()
        if not motivo:
            raise forms.ValidationError("Escribe el motivo de la consulta.")
        return motivo
    
class CancelarCitaForm(forms.Form):
    motivo = forms.CharField(
        label="¿Por qué cancelas?", max_length=255,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Ej.: tengo clases a esa hora, ya me siento mejor…"}),
        help_text="Nos ayuda a mejorar la atención.",
    )

    def clean_motivo(self):
        motivo = self.cleaned_data["motivo"].strip()
        if not motivo:
            raise forms.ValidationError("Escribe el motivo de la cancelación.")
        return motivo
    
class MotivoForm(forms.Form):
    """Motivo que escribe el personal o el administrador al cancelar o reprogramar."""
    motivo = forms.CharField(
        label="Motivo", max_length=255,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Ej.: el profesional tiene una emergencia, cambio de horario…"}),
        help_text="El estudiante verá este motivo en «Mis citas».",
    )

    def clean_motivo(self):
        motivo = self.cleaned_data["motivo"].strip()
        if not motivo:
            raise forms.ValidationError("Escribe el motivo.")
        return motivo


class ReprogramarCitaForm(MotivoForm):
    horario = forms.ModelChoiceField(
        queryset=Horario.objects.none(),
        error_messages={
            "required": "Elige el nuevo turno.",
            "invalid_choice": "Ese turno ya no está disponible. Elige otro.",
        },
    )

    def __init__(self, *args, turnos=None, **kwargs):
        super().__init__(*args, **kwargs)
        if turnos is not None:
            self.fields["horario"].queryset = turnos