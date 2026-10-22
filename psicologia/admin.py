from django.contrib import admin

from .models import (
    AdaptacionCurricular, AdaptacionDocente, ObservacionDocente, RegistroNEE, SesionPsicologica, TipoNEE,
)


class AdaptacionDocenteInline(admin.TabularInline):
    model = AdaptacionDocente
    extra = 0
    readonly_fields = ["fecha_asignacion", "fecha_lectura"]


@admin.register(AdaptacionCurricular)
class AdaptacionCurricularAdmin(admin.ModelAdmin):
    list_display = ["registro_nee", "tipo", "consentimiento_estudiante", "fecha_inicio"]
    inlines = [AdaptacionDocenteInline]


for modelo in (SesionPsicologica, TipoNEE, RegistroNEE, AdaptacionDocente, ObservacionDocente):
    admin.site.register(modelo)