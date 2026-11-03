from django import forms

from .models import ConsultaMedica
from .models import AntecedentesMedicos, ConsultaMedica

FORMATO_FECHA_HORA = "%Y-%m-%dT%H:%M"


class ConsultaMedicaForm(forms.ModelForm):
    """Estudiante, médico, cita y tipo NO están en el formulario: los pone el servicio."""

    class Meta:
        model = ConsultaMedica
        fields = [
            "motivo_consulta", "anamnesis",
            "presion_sistolica", "presion_diastolica", "frecuencia_cardiaca", "frecuencia_respiratoria",
            "temperatura", "saturacion_oxigeno", "peso", "talla",
            "examen_fisico", "diagnostico", "observaciones", "derivacion",
            "fecha_atencion",
        ]
        widgets = {
            "motivo_consulta": forms.Textarea(attrs={"rows": 2}),
            "anamnesis": forms.Textarea(attrs={"rows": 4}),
            "examen_fisico": forms.Textarea(attrs={"rows": 4}),
            "diagnostico": forms.Textarea(attrs={"rows": 3}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
            "derivacion": forms.Textarea(attrs={"rows": 2}),
            "fecha_atencion": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=FORMATO_FECHA_HORA),
        }

    EJEMPLOS = {
        "presion_sistolica": "120",
        "presion_diastolica": "80",
        "frecuencia_cardiaca": "72",
        "frecuencia_respiratoria": "16",
        "temperatura": "36.5",
        "saturacion_oxigeno": "98",
        "peso": "65.5",
        "talla": "1.65",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_atencion"].input_formats = [FORMATO_FECHA_HORA]
        for nombre, ejemplo in self.EJEMPLOS.items():
            self.fields[nombre].widget.attrs["placeholder"] = ejemplo
        self.fields["diagnostico"].widget.attrs["placeholder"] = "Diagnóstico presuntivo o definitivo"


TIPOS_SIN_CITA = [
    (valor, etiqueta) for valor, etiqueta in ConsultaMedica.Tipo.choices
    if valor != ConsultaMedica.Tipo.PROGRAMADA
]


class ConsultaSinCitaForm(ConsultaMedicaForm):
    """Igual que la consulta normal, más el tipo: Sin cita o Emergencia."""
    tipo = forms.ChoiceField(label="Tipo de atención", choices=TIPOS_SIN_CITA, widget=forms.RadioSelect)
    cancelar_cita_pendiente = forms.BooleanField(required=False)  # no se guarda en la consulta

    class Meta(ConsultaMedicaForm.Meta):
        fields = ["tipo"] + ConsultaMedicaForm.Meta.fields


class NotaAclaratoriaForm(forms.Form):
    texto = forms.CharField(
        label="Nueva nota aclaratoria",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Corrección o información adicional"}),
    )
    

class AntecedentesForm(forms.ModelForm):
    class Meta:
        model = AntecedentesMedicos
        fields = [
            "tipo_sangre", "alergias", "personales", "quirurgicos", "familiares",
            "medicacion_habitual", "habitos", "gineco_obstetricos",
        ]
        widgets = {
            campo: forms.Textarea(attrs={"rows": 2})
            for campo in ("alergias", "personales", "quirurgicos", "familiares",
                          "medicacion_habitual", "habitos", "gineco_obstetricos")
        }
        help_texts = {
            "gineco_obstetricos": "Solo si aplica.",
        }