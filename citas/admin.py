from django.contrib import admin

from .models import Cita, EstadoCita, Horario


@admin.register(Horario)
class HorarioAdmin(admin.ModelAdmin):
    list_display = ["personal", "fecha", "hora_inicio", "hora_fin"]
    list_filter = ["personal__area", "fecha"]


@admin.register(Cita)
class CitaAdmin(admin.ModelAdmin):
    list_display = ["estudiante", "horario", "estado", "fecha_registro"]
    list_filter = ["estado", "horario__personal__area"]


admin.site.register(EstadoCita)