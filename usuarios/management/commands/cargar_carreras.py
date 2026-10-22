"""
Carga las facultades y carreras de la Universidad Agraria del Ecuador.

Uso:  python manage.py cargar_carreras

Se puede ejecutar varias veces: solo agrega lo que falte, no duplica nada.
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction

FACULTADES = {
    "Facultad de Ciencias Agrarias": [
        "Agronomía",
        "Agroindustrial",
        "Ambiental",
        "Ciencias de la Computación",
    ],
    "Facultad de Economía Agrícola": [
        "Economía",
        "Economía Agrícola",
    ],
    "Facultad de Medicina Veterinaria y Zootecnia": [
        "Medicina Veterinaria",
    ],
}


class Command(BaseCommand):
    help = "Carga las facultades y carreras de la UAE."

    @transaction.atomic
    def handle(self, *args, **options):
        Facultad = apps.get_model("estudiantes", "Facultad")
        Carrera = apps.get_model("estudiantes", "Carrera")

        total_carreras = 0
        for nombre_facultad, carreras in FACULTADES.items():
            facultad, _ = Facultad.objects.get_or_create(nombre=nombre_facultad)
            for nombre_carrera in carreras:
                Carrera.objects.get_or_create(facultad=facultad, nombre=nombre_carrera)
                total_carreras += 1

        self.stdout.write(self.style.SUCCESS(
            f"Listo: {len(FACULTADES)} facultades y {total_carreras} carreras."
        ))