import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.generic import ListView

from estudiantes.models import Docente
from .forms import (
    CrearDocenteForm,
    CrearPersonalForm,
    DatosCuentaForm,
    EditarEstudianteForm,
    EditarPersonalForm,
)
from .grupos import GRUPO_ADMINISTRADOR, sincronizar_grupo
from .models import Persona, Personal
from .permisos import PerfilRequeridoMixin, perfil_requerido

Usuario = get_user_model()

CLAVE_SESION_CREADO = "usuario_creado"

# Filtro por los 6 perfiles: clave de la URL -> (etiqueta, condición)
FILTROS_PERFIL = {
    "ADMIN": ("Administradores", Q(rol=Usuario.Rol.ADMINISTRADOR)),
    "ESTUDIANTE": ("Estudiantes", Q(rol=Usuario.Rol.ESTUDIANTE)),
    "DOCENTE": ("Docentes", Q(rol=Usuario.Rol.DOCENTE)),
    "MEDICINA": (
        "Personal de Medicina",
        Q(rol=Usuario.Rol.PERSONAL, persona__personal__area__nombre="Medicina"),
    ),
    "ODONTOLOGIA": (
        "Personal de Odontología",
        Q(rol=Usuario.Rol.PERSONAL, persona__personal__area__nombre="Odontología"),
    ),
    "PSICOLOGIA": (
        "Personal de Psicología",
        Q(rol=Usuario.Rol.PERSONAL, persona__personal__area__nombre="Psicología"),
    ),
}

FILTROS_ESTADO = {
    "activos": True,
    "inactivos": False,
}


# =====================================================================
# Funciones de apoyo
# =====================================================================
def generar_contrasena_temporal(longitud=10):
    """Contraseña aleatoria segura, sin caracteres que se confundan (0/O, 1/l/I)."""
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(longitud))


def crear_persona_y_usuario(datos, rol, contrasena):
    """Crea la Persona y su Usuario. Debe llamarse dentro de transaction.atomic()."""
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
        password=contrasena,
        rol=rol,
        persona=persona,
    )
    return persona, usuario


# =====================================================================
# Listado de usuarios
# =====================================================================
class ListaUsuariosView(PerfilRequeridoMixin, ListView):
    perfiles = [GRUPO_ADMINISTRADOR]
    model = Usuario
    template_name = "usuarios/lista.html"
    context_object_name = "usuarios"
    paginate_by = 15

    def get_queryset(self):
        usuarios = (
            Usuario.objects
            .select_related("persona", "persona__personal__area")
            .order_by("persona__apellidos", "persona__nombres", "cedula")
        )

        # Búsqueda: cada palabra debe coincidir con cédula, correo, nombres o apellidos
        texto = self.request.GET.get("q", "").strip()
        for termino in texto.split():
            usuarios = usuarios.filter(
                Q(cedula__icontains=termino)
                | Q(email__icontains=termino)
                | Q(persona__nombres__icontains=termino)
                | Q(persona__apellidos__icontains=termino)
            )

        perfil = self.request.GET.get("perfil", "")
        if perfil in FILTROS_PERFIL:
            usuarios = usuarios.filter(FILTROS_PERFIL[perfil][1])

        estado = self.request.GET.get("estado", "")
        if estado in FILTROS_ESTADO:
            usuarios = usuarios.filter(is_active=FILTROS_ESTADO[estado])

        return usuarios

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        q = self.request.GET.get("q", "").strip()
        perfil = self.request.GET.get("perfil", "")
        estado = self.request.GET.get("estado", "")

        # Para que la paginación conserve los filtros
        parametros = self.request.GET.copy()
        parametros.pop("page", None)

        resumen = Usuario.objects.aggregate(
            total=Count("id"),
            estudiantes=Count("id", filter=Q(rol=Usuario.Rol.ESTUDIANTE)),
            personal=Count("id", filter=Q(rol=Usuario.Rol.PERSONAL)),
            docentes=Count("id", filter=Q(rol=Usuario.Rol.DOCENTE)),
            inactivos=Count("id", filter=Q(is_active=False)),
        )

        context.update({
            "q": q,
            "perfil_actual": perfil,
            "estado_actual": estado,
            "opciones_perfil": [(clave, etiqueta) for clave, (etiqueta, _) in FILTROS_PERFIL.items()],
            "parametros": parametros.urlencode(),
            "hay_filtros": bool(q or perfil or estado),
            "resumen": resumen,
        })
        return context


# =====================================================================
# Elegir qué tipo de usuario crear
# =====================================================================
@perfil_requerido(GRUPO_ADMINISTRADOR)
def nuevo_usuario(request):
    return render(request, "usuarios/nuevo_usuario.html")


# =====================================================================
# Crear personal de atención
# =====================================================================
@perfil_requerido(GRUPO_ADMINISTRADOR)
def crear_personal(request):
    if request.method == "POST":
        form = CrearPersonalForm(request.POST)
        if form.is_valid():
            datos = form.cleaned_data
            contrasena = generar_contrasena_temporal()
            try:
                with transaction.atomic():
                    persona, usuario = crear_persona_y_usuario(datos, Usuario.Rol.PERSONAL, contrasena)
                    Personal.objects.create(
                        persona=persona,
                        area=datos["area"],
                        cargo=datos["cargo"],
                        registro_profesional=datos["registro_profesional"],
                        fecha_ingreso=datos["fecha_ingreso"],
                    )
                    sincronizar_grupo(usuario)
            except (IntegrityError, ValidationError):
                form.add_error(None, "No se pudo crear el usuario. Verifique la cédula y el correo e intente de nuevo.")
            else:
                request.session[CLAVE_SESION_CREADO] = {"id": usuario.pk, "contrasena": contrasena}
                return redirect("usuarios:usuario_creado")
    else:
        form = CrearPersonalForm()

    return render(request, "usuarios/crear_personal.html", {"form": form})


# =====================================================================
# Crear docente
# =====================================================================
@perfil_requerido(GRUPO_ADMINISTRADOR)
def crear_docente(request):
    if request.method == "POST":
        form = CrearDocenteForm(request.POST)
        if form.is_valid():
            datos = form.cleaned_data
            contrasena = generar_contrasena_temporal()
            try:
                with transaction.atomic():
                    persona, usuario = crear_persona_y_usuario(datos, Usuario.Rol.DOCENTE, contrasena)
                    Docente.objects.create(
                        persona=persona,
                        facultad=datos["facultad"],
                        titulo_academico=datos["titulo_academico"],
                    )
                    sincronizar_grupo(usuario)
            except (IntegrityError, ValidationError):
                form.add_error(None, "No se pudo crear el usuario. Verifique la cédula y el correo e intente de nuevo.")
            else:
                request.session[CLAVE_SESION_CREADO] = {"id": usuario.pk, "contrasena": contrasena}
                return redirect("usuarios:usuario_creado")
    else:
        form = CrearDocenteForm()

    return render(request, "usuarios/crear_docente.html", {"form": form})


# =====================================================================
# Confirmación con la contraseña temporal
# =====================================================================
@never_cache
@perfil_requerido(GRUPO_ADMINISTRADOR)
def usuario_creado(request):
    """Muestra la contraseña temporal UNA sola vez: al recargar ya no aparece."""
    datos = request.session.pop(CLAVE_SESION_CREADO, None)
    if not datos:
        return redirect("usuarios:lista")

    nuevo = get_object_or_404(
        Usuario.objects.select_related("persona", "persona__personal__area"),
        pk=datos["id"],
    )
    return render(request, "usuarios/usuario_creado.html", {
        "nuevo": nuevo,
        "contrasena": datos["contrasena"],
    })


# =====================================================================
# Ver y editar un usuario
# =====================================================================
RELACIONES_DETALLE = (
    "persona",
    "persona__personal__area",
    "persona__docente__facultad",
    "persona__estudiante__carrera__facultad",
)

# Formulario de edición según el rol
FORMULARIOS_EDICION = {
    Usuario.Rol.ADMINISTRADOR: DatosCuentaForm,
    Usuario.Rol.PERSONAL: EditarPersonalForm,
    Usuario.Rol.DOCENTE: CrearDocenteForm,
    Usuario.Rol.ESTUDIANTE: EditarEstudianteForm,
}

# Campos propios de cada perfil (se guardan en Personal, Docente o Estudiante)
CAMPOS_PERFIL = {
    Usuario.Rol.PERSONAL: ["area", "cargo", "registro_profesional", "fecha_ingreso", "fecha_salida"],
    Usuario.Rol.DOCENTE: ["facultad", "titulo_academico"],
    Usuario.Rol.ESTUDIANTE: ["carrera", "semestre", "estado_academico"],
}

CAMPOS_PERSONA = ["nombres", "apellidos", "fecha_nacimiento", "sexo", "telefono"]


def obtener_usuario(pk):
    return get_object_or_404(Usuario.objects.select_related(*RELACIONES_DETALLE), pk=pk)


def registro_de_perfil(usuario):
    """Devuelve el Personal, Docente o Estudiante del usuario, o None si no tiene."""
    if usuario.persona is None:
        return None
    relacion = {
        Usuario.Rol.PERSONAL: "personal",
        Usuario.Rol.DOCENTE: "docente",
        Usuario.Rol.ESTUDIANTE: "estudiante",
    }.get(usuario.rol)
    return getattr(usuario.persona, relacion, None) if relacion else None


def datos_iniciales(usuario, registro):
    persona = usuario.persona
    inicial = {campo: getattr(persona, campo) for campo in CAMPOS_PERSONA}
    inicial["cedula"] = usuario.cedula
    inicial["correo"] = usuario.email
    if registro is not None:
        for campo in CAMPOS_PERFIL[usuario.rol]:
            inicial[campo] = getattr(registro, campo)
    return inicial


@perfil_requerido(GRUPO_ADMINISTRADOR)
def detalle_usuario(request, pk):
    u = obtener_usuario(pk)
    return render(request, "usuarios/detalle.html", {
        "u": u,
        "registro": registro_de_perfil(u),
    })


@perfil_requerido(GRUPO_ADMINISTRADOR)
def editar_usuario(request, pk):
    u = obtener_usuario(pk)
    persona = u.persona
    registro = registro_de_perfil(u)
    FormularioClase = FORMULARIOS_EDICION.get(u.rol)

    # La cuenta técnica (sin persona) o un perfil incompleto no se editan aquí
    sin_datos = persona is None or (u.rol in CAMPOS_PERFIL and registro is None)
    if FormularioClase is None or sin_datos:
        messages.warning(request, "Esta cuenta no tiene datos completos y no se puede editar desde aquí.")
        return redirect("usuarios:detalle", pk=u.pk)

    if request.method == "POST":
        form = FormularioClase(request.POST, usuario=u)
        if form.is_valid():
            datos = form.cleaned_data
            try:
                with transaction.atomic():
                    for campo in CAMPOS_PERSONA:
                        setattr(persona, campo, datos[campo])
                    persona.save()

                    # La cédula no se modifica; solo el correo
                    u.email = datos["correo"]
                    u.save(update_fields=["email"])

                    if registro is not None:
                        for campo in CAMPOS_PERFIL[u.rol]:
                            setattr(registro, campo, datos[campo])
                        registro.save()

                    # Si cambió el área, cambia también su grupo de permisos
                    sincronizar_grupo(u)
            except (IntegrityError, ValidationError):
                form.add_error(None, "No se pudieron guardar los cambios. Verifique los datos e intente de nuevo.")
            else:
                messages.success(request, "Los datos del usuario se actualizaron correctamente.")
                return redirect("usuarios:detalle", pk=u.pk)
    else:
        form = FormularioClase(usuario=u, initial=datos_iniciales(u, registro))

    return render(request, "usuarios/editar.html", {"form": form, "u": u})