from django.contrib.auth.models import Group

# Nombres exactos de los grupos (los mismos del comando crear_grupos)
GRUPO_ADMINISTRADOR = "Administrador"
GRUPO_ESTUDIANTE = "Estudiante"
GRUPO_DOCENTE = "Docente"

# El personal va al grupo con el mismo nombre de su área
GRUPOS_POR_AREA = {
    "Medicina": "Medicina",
    "Odontología": "Odontología",
    "Psicología": "Psicología",
}

GRUPOS_DEL_SISTEMA = [
    GRUPO_ADMINISTRADOR,
    GRUPO_ESTUDIANTE,
    GRUPO_DOCENTE,
    *GRUPOS_POR_AREA.values(),
]


def nombre_grupo(usuario):
    """Devuelve el nombre del grupo que le corresponde al usuario, o None."""
    from .models import Usuario

    if usuario.rol == Usuario.Rol.ADMINISTRADOR:
        return GRUPO_ADMINISTRADOR
    if usuario.rol == Usuario.Rol.ESTUDIANTE:
        return GRUPO_ESTUDIANTE
    if usuario.rol == Usuario.Rol.DOCENTE:
        return GRUPO_DOCENTE
    if usuario.rol == Usuario.Rol.PERSONAL:
        personal = getattr(usuario.persona, "personal", None) if usuario.persona_id else None
        if personal is None:
            return None
        return GRUPOS_POR_AREA.get(personal.area.nombre)
    return None


def sincronizar_grupo(usuario):
    """
    Deja al usuario únicamente en el grupo de su perfil.
    Quita cualquier otro grupo del sistema (por ejemplo, si cambió de área).
    Devuelve el nombre del grupo asignado o None si no se pudo determinar.
    """
    nombre = nombre_grupo(usuario)

    sobrantes = usuario.groups.filter(name__in=GRUPOS_DEL_SISTEMA).exclude(name=nombre)
    usuario.groups.remove(*sobrantes)

    if nombre is None:
        return None

    grupo = Group.objects.filter(name=nombre).first()
    if grupo is None:
        # El grupo aún no existe: hay que ejecutar "python manage.py crear_grupos"
        return None

    usuario.groups.add(grupo)
    return nombre