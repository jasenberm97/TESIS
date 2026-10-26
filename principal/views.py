# principal/views.py
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.urls import reverse
from citas import servicios as servicios_citas
from datetime import timedelta

from django.utils import timezone

from citas.models import Cita, EstadoCita


class LoginCedulaForm(AuthenticationForm):
    """Formulario de login de Django con mensajes en español y propios del sistema."""

    error_messages = {
        "invalid_login": "Cédula o contraseña incorrectas. Verifique e intente de nuevo.",
        "inactive": "Esta cuenta está desactivada. Comuníquese con Bienestar Universitario.",
    }


class IniciarSesionView(auth_views.LoginView):
    template_name = "principal/login.html"
    authentication_form = LoginCedulaForm
    redirect_authenticated_user = True


def inicio(request):
    if request.user.is_authenticated:
        return redirect("principal:panel")
    return redirect("principal:login")


@login_required
def panel(request):
    contexto = {}
    persona = request.user.persona
    if request.user.rol == "ESTUDIANTE" and persona:
        estudiante = getattr(persona, "estudiante", None)
        if estudiante:
            # Como máximo hay una cita activa por área (3 en total); se muestran las 2 más cercanas.
            proximas = list(servicios_citas.citas_activas_futuras(estudiante))
            contexto["proximas_citas"] = proximas[:2]
            contexto["otras_citas"] = max(len(proximas) - 2, 0)
            # Inasistencia de los últimos 7 días, para invitarlo a agendar de nuevo
            contexto["inasistencia"] = (
                Cita.objects
                .filter(
                    estudiante=estudiante,
                    estado__nombre=EstadoCita.NO_ASISTIO,
                    horario__fecha__gte=timezone.localdate() - timedelta(days=7),
                )
                .select_related("horario__personal__area")
                .order_by("-horario__fecha")
                .first()
            )
    return render(request, "principal/panel.html", contexto)

class CambiarContrasenaView(auth_views.PasswordChangeView):
    """Cambio de contraseña para cualquier perfil (ya exige sesión iniciada)."""

    template_name = "principal/cambiar_contrasena.html"

    def form_valid(self, form):
        messages.success(self.request, "Tu contraseña se cambió correctamente.")
        return super().form_valid(form)

    def get_success_url(self):
        # El estudiante vuelve a "Mi perfil"; los demás, al inicio
        if self.request.user.rol == "ESTUDIANTE":
            return reverse("estudiantes:mi_perfil")
        return reverse("principal:panel")