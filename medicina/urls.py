from django.urls import path

from . import views

app_name = "medicina"

urlpatterns = [
    path("atender/<int:cita_id>/", views.atender_cita, name="atender"),
    path("consultas/<int:pk>/", views.consulta_detalle, name="consulta_detalle"),
]