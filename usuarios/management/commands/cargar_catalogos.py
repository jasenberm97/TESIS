"""
Carga los catálogos iniciales del sistema y crea los grupos de permisos.

Uso:  python manage.py cargar_catalogos

Se puede ejecutar varias veces: solo agrega lo que falte, no duplica nada.
"""
from django.apps import apps
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

AREAS = ["Medicina", "Odontología", "Psicología"]

ESTADOS_CITA = [
    ("Pendiente", True),
    ("Confirmada", True),
    ("Atendida", False),
    ("Cancelada", False),
    ("Reprogramada", False),
    ("No asistió", False),
]

# Tomados del formato físico de pedido de exámenes de laboratorio
EXAMENES = {
    "Hematología y coagulación": [
        "Hematocrito", "Hemoglobina", "Leucocitos", "Hematíes", "Plaquetas", "Reticulocitos",
        "Fórmula leucocitaria", "Eritrosedimentación", "Glucosa 6 PDH", "Piruvato kinasa",
        "Plasmodium (gota gruesa)", "Células LE", "Tiempo de coagulación", "Tiempo de sangría",
        "Retracción del coágulo", "Tiempo de protrombina (TP)", "Tiempo parcial de tromboplastina (TPT)",
        "Fibrinógeno", "Grupo sanguíneo y factor RH", "Fenotipo / genotipo RH", "Prueba de Coombs directa",
    ],
    "Química sanguínea": [
        "Glucosa", "Glucosa post prandial", "Urea", "Creatinina", "Ácido úrico", "Hierro sérico",
        "Transferrina", "Fructosamina", "Electroforesis de proteínas", "Electroforesis de hemoglobina",
        "Curva de tolerancia a la glucosa", "Hemoglobina glicosilada", "Colesterol", "Triglicéridos",
        "HDL colesterol", "LDL colesterol", "Lípidos totales", "Proteínas totales", "Albúmina",
        "Globulinas", "Bilirrubina total", "Bilirrubina directa", "Bilirrubina indirecta",
    ],
    "Electrolitos": [
        "Sodio", "Potasio", "Cloro", "Calcio", "Magnesio", "Fósforo", "Amoníaco", "Litio",
    ],
    "Enzimas": [
        "Creatin kinasa", "Amilasa", "Lipasa", "Fosfatasa ácida total", "Fosfatasa ácida prostática",
        "Fosfatasa alcalina", "CPK", "CPK-MB", "GOT (AST)", "GPT (ALT)", "Gamma GT", "LDH",
        "Colinesterasa (CHE)",
    ],
    "Orina": [
        "Elemental y microscópico (EMO)", "Prueba de embarazo", "HCG beta específica",
        "Depuración de creatinina", "Proteína de Bence Jones", "Identificación del germen",
        "Contaje de colonias", "Cultivo y antibiograma", "Gram gota fresca",
    ],
    "Heces": [
        "Coproparasitario", "Sangre oculta", "Coprocultivo", "Tinción Gram / Wright",
        "Investigación de levaduras", "Azúcares reductores", "Rotavirus", "Moco fecal",
    ],
    "Inmunología": [
        "Proteína C reactiva", "Factor reumatoideo (RA Test)", "ASTO", "IgG", "IgA", "IgE",
        "C3", "C4", "Anti DNA", "Anticuerpos antinucleares (ANA)",
    ],
    "Hormonas": [
        "T3", "T4", "TSH", "Hormona de crecimiento (HGH)", "Estradiol", "Progesterona", "LH", "FSH",
        "Beta HCG", "Prolactina", "Testosterona", "ACTH", "Cortisol AM / PM", "PTH (parathormona)",
    ],
    "Marcadores tumorales": [
        "Alfa feto proteína (AFP)", "Antígeno carcinoembrionario (CEA)",
        "Antígeno prostático (PSA) total y libre", "Beta HCG", "CA 125", "CA 19-9", "CA 15-3",
        "CA 72-4", "Ferritina", "Fosfatasa ácida prostática",
    ],
    "Moco nasal": [
        "Citología", "Eosinófilos",
    ],
    "Inmunodiagnóstico infeccioso": [
        "VDRL cualitativo", "Reacción de Widal", "Reacción de Weil Félix", "Reacción para Brucella",
        "Mononucleosis", "Toxoplasma IgG / IgM", "Rubéola IgG / IgM", "Citomegalovirus IgG / IgM",
        "Herpes 1 IgG / IgM", "Herpes 2 IgG / IgM", "TORCH IgG / IgM", "Chlamydia IgG / IgM",
        "Cisticercosis", "FTA-Abs", "Helicobacter pylori", "Hepatitis A", "Hepatitis B (HBsAg)",
        "Hepatitis B (Anti HBc)", "HIV 1/2", "Mycoplasma", "Serología para ameba",
    ],
    "Secreción vaginal": [
        "Investigación de Trichomonas", "Investigación de Cándida", "Cultivo",
        "Identificación del germen", "Antibiograma",
    ],
    "Esputo": [
        "Tinción de Gram", "Tinción de Ziehl Neelsen", "Cultivo", "Antibiograma",
        "Investigación de Paragonimus", "Cultivo para Mycobacterium tuberculosis",
    ],
    "Cultivo de exudado faríngeo": [
        "Gram", "Hongos", "Cultivo", "Antibiograma",
    ],
    "Otros": [],
}

PROCEDIMIENTOS = [
    "Profilaxis", "Destartraje", "Aplicación de flúor", "Sellantes",
    "Restauración", "Exodoncia", "Endodoncia",
]

TIPOS_NEE = [
    ("Discapacidad física", True),
    ("Discapacidad auditiva", True),
    ("Discapacidad visual", True),
    ("Discapacidad intelectual", True),
    ("Discapacidad psicosocial", True),
    ("Trastorno del espectro autista", True),
    ("Trastorno por déficit de atención e hiperactividad (TDAH)", False),
    ("Dificultades específicas de aprendizaje", False),
    ("Altas capacidades", False),
]

SUPERFICIES = ["Vestibular", "Lingual / palatina", "Mesial", "Distal", "Oclusal / incisal"]

CONDICIONES = [
    ("Sano", "#2E7D32"),
    ("Caries", "#D32F2F"),
    ("Obturación", "#1565C0"),
    ("Ausente", "#616161"),
    ("Corona", "#F9A825"),
    ("Endodoncia", "#6A1B9A"),
    ("Extracción indicada", "#E65100"),
]

# Seis perfiles funcionales (los permisos de cada grupo se asignarán después)
GRUPOS = ["Administrador", "Estudiante", "Docente", "Medicina", "Odontología", "Psicología"]

_PERMANENTES = {1: "Incisivo central", 2: "Incisivo lateral", 3: "Canino", 4: "Primer premolar",
                5: "Segundo premolar", 6: "Primer molar", 7: "Segundo molar", 8: "Tercer molar"}
_TEMPORALES = {1: "Incisivo central", 2: "Incisivo lateral", 3: "Canino", 4: "Primer molar", 5: "Segundo molar"}
_CUADRANTES = {1: "superior derecho", 2: "superior izquierdo", 3: "inferior izquierdo", 4: "inferior derecho",
               5: "superior derecho", 6: "superior izquierdo", 7: "inferior izquierdo", 8: "inferior derecho"}


def piezas_fdi():
    """52 piezas según la notación FDI: 32 permanentes y 20 temporales."""
    for cuadrante in (1, 2, 3, 4):
        for numero, nombre in _PERMANENTES.items():
            yield f"{cuadrante}{numero}", f"{nombre} {_CUADRANTES[cuadrante]}", "PERMANENTE"
    for cuadrante in (5, 6, 7, 8):
        for numero, nombre in _TEMPORALES.items():
            yield f"{cuadrante}{numero}", f"{nombre} {_CUADRANTES[cuadrante]}", "TEMPORAL"


class Command(BaseCommand):
    help = "Carga los catálogos iniciales y crea los grupos de los seis perfiles."

    @transaction.atomic
    def handle(self, *args, **options):
        Area = apps.get_model("usuarios", "Area")
        EstadoCita = apps.get_model("citas", "EstadoCita")
        CategoriaExamen = apps.get_model("medicina", "CategoriaExamen")
        Examen = apps.get_model("medicina", "Examen")
        Procedimiento = apps.get_model("odontologia", "Procedimiento")
        PiezaDental = apps.get_model("odontologia", "PiezaDental")
        SuperficieDental = apps.get_model("odontologia", "SuperficieDental")
        CondicionDental = apps.get_model("odontologia", "CondicionDental")
        TipoNEE = apps.get_model("psicologia", "TipoNEE")

        for nombre in AREAS:
            Area.objects.get_or_create(nombre=nombre)

        for nombre, es_activo in ESTADOS_CITA:
            EstadoCita.objects.update_or_create(nombre=nombre, defaults={"es_activo": es_activo})

        total_examenes = 0
        for categoria_nombre, examenes in EXAMENES.items():
            categoria, _ = CategoriaExamen.objects.get_or_create(nombre=categoria_nombre)
            for nombre in examenes:
                Examen.objects.get_or_create(categoria=categoria, nombre=nombre)
                total_examenes += 1

        for nombre in PROCEDIMIENTOS:
            Procedimiento.objects.get_or_create(nombre=nombre)

        for nombre, discapacidad in TIPOS_NEE:
            TipoNEE.objects.get_or_create(nombre=nombre, defaults={"asociada_discapacidad": discapacidad})

        for nombre in SUPERFICIES:
            SuperficieDental.objects.get_or_create(nombre=nombre)

        for nombre, color in CONDICIONES:
            CondicionDental.objects.get_or_create(nombre=nombre, defaults={"color": color})

        total_piezas = 0
        for codigo, nombre, tipo in piezas_fdi():
            PiezaDental.objects.get_or_create(codigo_fdi=codigo, defaults={"nombre": nombre, "tipo": tipo})
            total_piezas += 1

        for nombre in GRUPOS:
            Group.objects.get_or_create(name=nombre)

        self.stdout.write(self.style.SUCCESS(
            f"Catálogos listos: {len(AREAS)} áreas, {len(ESTADOS_CITA)} estados de cita, "
            f"{len(EXAMENES)} categorías y {total_examenes} exámenes, {len(PROCEDIMIENTOS)} procedimientos, "
            f"{len(TIPOS_NEE)} tipos de NEE, {len(SUPERFICIES)} superficies, {len(CONDICIONES)} condiciones, "
            f"{total_piezas} piezas dentales y {len(GRUPOS)} grupos."
        ))