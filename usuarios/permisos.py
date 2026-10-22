from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied


def grupos_del_usuario(usuario):
    """Nombres de los grupos del usuario (se consulta una sola vez por petición)."""
    if not usuario.is_authenticated:
        return set()
    if not hasattr(usuario, "_grupos_cache"):
        usuario._grupos_cache = set(usuario.groups.values_list("name", flat=True))
    return usuario._grupos_cache


def tiene_perfil(usuario, *grupos):
    """True si el usuario está activo y pertenece a alguno de los grupos indicados."""
    return usuario.is_active and bool(grupos_del_usuario(usuario) & set(grupos))


def perfil_requerido(*grupos):
    """
    Decorador para vistas con función.
    Uso: @perfil_requerido(GRUPO_ADMINISTRADOR)
    """
    def decorador(vista):
        @wraps(vista)
        @login_required
        def envoltura(request, *args, **kwargs):
            if not tiene_perfil(request.user, *grupos):
                raise PermissionDenied
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador


class PerfilRequeridoMixin(LoginRequiredMixin, UserPassesTestMixin):
    """
    Mixin para vistas con clase.
    Uso: class MiVista(PerfilRequeridoMixin, ListView):
             perfiles = [GRUPO_ADMINISTRADOR]
    """
    perfiles = []

    def test_func(self):
        return tiene_perfil(self.request.user, *self.perfiles)