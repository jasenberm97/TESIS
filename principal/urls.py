# principal/urls.py
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "principal"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("login/", views.IniciarSesionView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("panel/", views.panel, name="panel"),
    path("cuenta/contrasena/", views.CambiarContrasenaView.as_view(), name="cambiar_contrasena"),
]