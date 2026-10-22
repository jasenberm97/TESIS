from django.urls import path

from . import views

app_name = "usuarios"

urlpatterns = [
    path("", views.ListaUsuariosView.as_view(), name="lista"),
    path("nuevo/", views.nuevo_usuario, name="nuevo"),
    path("personal/nuevo/", views.crear_personal, name="crear_personal"),
    path("docentes/nuevo/", views.crear_docente, name="crear_docente"),
    path("creado/", views.usuario_creado, name="usuario_creado"),
    path("<int:pk>/", views.detalle_usuario, name="detalle"),
    path("<int:pk>/editar/", views.editar_usuario, name="editar"),
]