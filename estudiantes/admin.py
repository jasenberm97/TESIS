from django.contrib import admin

from .models import Carrera, Docente, Estudiante, Facultad


@admin.register(Estudiante)
class EstudianteAdmin(admin.ModelAdmin):
    list_display = ["codigo_estudiantil", "persona", "carrera", "semestre", "estado_academico"]
    list_filter = ["carrera", "estado_academico"]
    search_fields = ["codigo_estudiantil", "persona__nombres", "persona__apellidos"]
    readonly_fields = ["codigo_estudiantil"]


@admin.register(Docente)
class DocenteAdmin(admin.ModelAdmin):
    list_display = ["persona", "facultad", "titulo_academico"]
    list_filter = ["facultad"]


admin.site.register(Facultad)
admin.site.register(Carrera)