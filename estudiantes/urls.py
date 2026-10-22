# estudiantes/urls.py
from django.urls import path
from django.views.generic import TemplateView

from . import views

app_name = "estudiantes"

urlpatterns = [
    path("registro/", views.registro_estudiante, name="registro"),
    path("registro/exitoso/", views.registro_exitoso, name="registro_exitoso"),
    path(
        "privacidad/",
        TemplateView.as_view(template_name="estudiantes/aviso_privacidad.html"),
        name="privacidad",
    ),
        path("perfil/", views.mi_perfil, name="mi_perfil"),
]