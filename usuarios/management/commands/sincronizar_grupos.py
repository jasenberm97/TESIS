from django.core.management.base import BaseCommand
from django.db import transaction

from usuarios.grupos import sincronizar_grupo
from usuarios.models import Usuario


class Command(BaseCommand):
    help = "Asigna a cada usuario el grupo que corresponde a su rol y área."

    @transaction.atomic
    def handle(self, *args, **options):
        asignados = 0
        sin_grupo = []

        usuarios = Usuario.objects.select_related("persona", "persona__personal__area")
        for usuario in usuarios:
            nombre = sincronizar_grupo(usuario)
            if nombre:
                asignados += 1
                self.stdout.write(f"  {usuario.cedula} -> {nombre}")
            else:
                sin_grupo.append(usuario)

        self.stdout.write(self.style.SUCCESS(f"\n{asignados} usuario(s) con grupo asignado."))

        if sin_grupo:
            self.stdout.write(self.style.WARNING("Sin grupo (revisar rol, persona o área):"))
            for u in sin_grupo:
                self.stdout.write(self.style.WARNING(f"  - {u.cedula} (rol {u.rol})"))