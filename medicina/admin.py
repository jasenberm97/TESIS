from django.contrib import admin

from .models import (
    ArchivoResultado, CategoriaExamen, CertificadoMedico, ConsultaMedica, DetalleOrdenExamen,
    DetalleReceta, Examen, Medicamento, OrdenExamen, Receta,
)


class DetalleRecetaInline(admin.TabularInline):
    model = DetalleReceta
    extra = 1


@admin.register(Receta)
class RecetaAdmin(admin.ModelAdmin):
    inlines = [DetalleRecetaInline]


class DetalleOrdenInline(admin.TabularInline):
    model = DetalleOrdenExamen
    extra = 1


class ArchivoResultadoInline(admin.TabularInline):
    model = ArchivoResultado
    extra = 0


@admin.register(OrdenExamen)
class OrdenExamenAdmin(admin.ModelAdmin):
    inlines = [DetalleOrdenInline, ArchivoResultadoInline]


@admin.register(Examen)
class ExamenAdmin(admin.ModelAdmin):
    list_display = ["nombre", "categoria"]
    list_filter = ["categoria"]


@admin.register(CertificadoMedico)
class CertificadoMedicoAdmin(admin.ModelAdmin):
    list_display = ["codigo", "consulta_medica", "fecha_inicio_reposo", "dias_reposo"]
    readonly_fields = ["codigo"]


admin.site.register(ConsultaMedica)
admin.site.register(Medicamento)
admin.site.register(CategoriaExamen)