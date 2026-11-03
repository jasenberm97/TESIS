from django.urls import path

from . import views

app_name = "medicina"

urlpatterns = [
    path("atender/<int:cita_id>/", views.atender_cita, name="atender"),
    path("sin-cita/", views.buscar_paciente, name="sin_cita"),
    path("sin-cita/<int:estudiante_id>/", views.atender_sin_cita, name="atender_sin_cita"),
    path("consultas/<int:pk>/", views.consulta_detalle, name="consulta_detalle"),
    path("consultas/<int:pk>/editar/", views.consulta_editar, name="consulta_editar"),
    path("consultas/<int:pk>/notas/", views.nota_agregar, name="nota_agregar"),
    path("consultas/<int:pk>/cancelar-cita/", views.cancelar_cita_pendiente, name="cancelar_cita_pendiente"),
    path("pacientes/", views.pacientes, name="pacientes"),
    path("pacientes/<int:pk>/", views.paciente_ficha, name="paciente_ficha"),
    path("pacientes/<int:pk>/antecedentes/", views.antecedentes_editar, name="antecedentes_editar"),
]