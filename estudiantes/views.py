# estudiantes/views.py
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from usuarios.grupos import GRUPO_ESTUDIANTE, sincronizar_grupo
from usuarios.models import Persona
from usuarios.permisos import perfil_requerido
from .forms import PerfilEstudianteForm, RegistroEstudianteForm
from .models import Estudiante

Usuario = get_user_model()

CLAVE_SESION_REGISTRO = "registro_estudiante_id"


# =====================================================================
# Registro público de estudiantes
# =====================================================================
def registro_estudiante(request):
    if request.method == "POST":
        form = RegistroEstudianteForm(request.POST)
        if form.is_valid():
            datos = form.cleaned_data
            try:
                # Todo se guarda junto: si algo falla, no queda nada a medias
                with transaction.atomic():
                    persona = Persona.objects.create(
                        nombres=datos["nombres"],
                        apellidos=datos["apellidos"],
                        fecha_nacimiento=datos["fecha_nacimiento"],
                        sexo=datos["sexo"],
                        telefono=datos["telefono"],
                    )
                    usuario = Usuario.objects.create_user(
                        cedula=datos["cedula"],
                        email=datos["correo"],
                        password=datos["password1"],
                        rol=Usuario.Rol.ESTUDIANTE,
                        persona=persona,
                    )
                    estudiante = Estudiante.objects.create(
                        persona=persona,
                        carrera=datos["carrera"],
                        semestre=datos["semestre"],
                        fecha_consentimiento=timezone.now(),
                    )
                    # Asigna el grupo "Estudiante" según su rol
                    sincronizar_grupo(usuario)
            except IntegrityError:
                form.add_error(None, "No se pudo completar el registro. Verifique su cédula y correo e intente de nuevo.")
            else:
                request.session[CLAVE_SESION_REGISTRO] = estudiante.pk
                return redirect("estudiantes:registro_exitoso")
    else:
        form = RegistroEstudianteForm()

    return render(request, "estudiantes/registro.html", {"form": form})


def registro_exitoso(request):
    """Muestra el resultado del registro. Si se recarga (F5), no reenvía el formulario."""
    estudiante_id = request.session.get(CLAVE_SESION_REGISTRO)
    if not estudiante_id:
        return redirect("estudiantes:registro")

    estudiante = get_object_or_404(
        Estudiante.objects.select_related("persona__usuario", "carrera__facultad"),
        pk=estudiante_id,
    )
    return render(request, "estudiantes/registro_exitoso.html", {"estudiante": estudiante})


# =====================================================================
# Mi perfil (solo estudiantes con sesión iniciada)
# =====================================================================
@perfil_requerido(GRUPO_ESTUDIANTE)
def mi_perfil(request):
    usuario = request.user
    persona = usuario.persona

    # Además del grupo, debe tener su registro de estudiante
    if persona is None or not hasattr(persona, "estudiante"):
        raise PermissionDenied

    estudiante = persona.estudiante

    if request.method == "POST":
        form = PerfilEstudianteForm(request.POST, usuario=usuario)
        if form.is_valid():
            with transaction.atomic():
                usuario.email = form.cleaned_data["correo"]
                usuario.save(update_fields=["email"])
                persona.telefono = form.cleaned_data["telefono"]
                persona.save(update_fields=["telefono"])
            messages.success(request, "Tus datos de contacto se actualizaron correctamente.")
            return redirect("estudiantes:mi_perfil")
    else:
        form = PerfilEstudianteForm(
            usuario=usuario,
            initial={"correo": usuario.email, "telefono": persona.telefono},
        )

    return render(request, "estudiantes/mi_perfil.html", {"form": form, "estudiante": estudiante}) 