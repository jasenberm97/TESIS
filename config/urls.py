# config/urls.py
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("estudiantes/", include("estudiantes.urls")),
    path("usuarios/", include("usuarios.urls")),
    path("", include("principal.urls")),
]