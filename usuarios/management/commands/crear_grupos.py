from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

# "apps": se dan TODOS los permisos de todos los modelos de esa app.
# "permisos": permisos sueltos con formato "app_label.codename".

PERMISOS_COMUNES_PERSONAL = [
    "usuarios.view_persona",
    "estudiantes.view_estudiante",
    "estudiantes.view_carrera",
    "estudiantes.view_facultad",
    "citas.view_horario",
    "citas.view_estadocita",
    "citas.view_cita",
    "citas.change_cita",
]

GRUPOS = {
    "Administrador": {
        "apps": ["usuarios", "estudiantes"],
        "permisos": [
            "citas.view_horario",
            "citas.add_horario",
            "citas.change_horario",
            "citas.delete_horario",
            "citas.view_estadocita",
            "citas.view_cita",
            "citas.change_cita",
        ],
    },
    "Estudiante": {
        "apps": [],
        "permisos": [
            "citas.view_horario",
            "citas.view_estadocita",
            "citas.view_cita",
            "citas.add_cita",
            "citas.change_cita",
        ],
    },
    "Docente": {
        "apps": [],
        "permisos": [
            "psicologia.view_adaptaciondocente",
            "psicologia.change_adaptaciondocente",
        ],
    },
    "Medicina": {
        "apps": ["medicina"],
        "permisos": PERMISOS_COMUNES_PERSONAL,
    },
    "Odontología": {
        "apps": ["odontologia"],
        "permisos": PERMISOS_COMUNES_PERSONAL,
    },
    "Psicología": {
        "apps": ["psicologia"],
        "permisos": PERMISOS_COMUNES_PERSONAL,
    },
}


class Command(BaseCommand):
    help = "Crea o actualiza los 6 grupos del sistema con sus permisos."

    @transaction.atomic
    def handle(self, *args, **options):
        faltantes = []

        for nombre, config in GRUPOS.items():
            grupo, creado = Group.objects.get_or_create(name=nombre)
            permisos = set()

            for app in config["apps"]:
                de_la_app = Permission.objects.filter(content_type__app_label=app)
                if not de_la_app.exists():
                    faltantes.append(f"{nombre}: la app '{app}' no tiene permisos")
                permisos.update(de_la_app)

            for clave in config["permisos"]:
                app_label, codename = clave.split(".")
                try:
                    permisos.add(
                        Permission.objects.get(
                            content_type__app_label=app_label,
                            codename=codename,
                        )
                    )
                except Permission.DoesNotExist:
                    faltantes.append(f"{nombre}: no existe {clave}")

            # set() reemplaza todo: el código es la única fuente de verdad
            grupo.permissions.set(permisos)

            estado = "creado" if creado else "actualizado"
            self.stdout.write(
                self.style.SUCCESS(f"{nombre}: {estado} ({len(permisos)} permisos)")
            )

        if faltantes:
            self.stdout.write(self.style.WARNING("\nPermisos no encontrados:"))
            for f in faltantes:
                self.stdout.write(self.style.WARNING(f"  - {f}"))
        else:
            self.stdout.write(self.style.SUCCESS("\nTodos los permisos se asignaron."))