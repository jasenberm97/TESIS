from django.urls import path

from . import views

app_name = "citas"

urlpatterns = [
    path("horarios/", views.lista_profesionales, name="horarios"),
    path("horarios/generar/", views.generar_turnos, name="horarios_generar"),
    path("horarios/profesional/<int:pk>/", views.calendario_profesional, name="horarios_calendario"),
    path("horarios/profesional/<int:pk>/ausencia/", views.registrar_ausencia, name="horarios_ausencia"),
    path("horarios/profesional/<int:pk>/quitar-libres/", views.quitar_turnos_libres, name="horarios_quitar"),
    path("horarios/turno/<int:pk>/editar/", views.editar_horario, name="horarios_editar"),
    path("horarios/turno/<int:pk>/eliminar/", views.eliminar_horario, name="horarios_eliminar"),
    path("horarios/ausencia/<int:pk>/eliminar/", views.eliminar_ausencia, name="horarios_eliminar_ausencia"),
    path("horarios/feriados/", views.lista_feriados, name="horarios_feriados"),
    path("horarios/feriados/nuevo/", views.registrar_feriado, name="horarios_feriado_nuevo"),
    path("horarios/feriados/<int:pk>/eliminar/", views.eliminar_feriado, name="horarios_eliminar_feriado"),
    path("agendar/", views.agendar_area, name="agendar"),
    path("agendar/area/<int:area_id>/", views.agendar_turnos, name="agendar_turnos"),
    path("agendar/turno/<int:horario_id>/", views.agendar_confirmar, name="agendar_confirmar"),
    path("agendar/listo/", views.agendar_exito, name="agendar_exito"),
    path("mis-citas/", views.mis_citas, name="mis_citas"),
    path("mis-citas/<int:pk>/cancelar/", views.cancelar_mi_cita, name="mis_citas_cancelar"),
    path("agenda/", views.agenda, name="agenda"),
    path("agenda/cita/<int:pk>/asistencia/", views.agenda_asistencia, name="agenda_asistencia"),
    path("agenda/cita/<int:pk>/cancelar/", views.agenda_cancelar, name="agenda_cancelar"),
    path("agenda/cita/<int:pk>/reprogramar/", views.agenda_reprogramar, name="agenda_reprogramar"),
    path("gestion/", views.gestion_citas, name="gestion_citas"),
    path("gestion/<int:pk>/", views.gestion_cita_detalle, name="gestion_cita_detalle"),
]