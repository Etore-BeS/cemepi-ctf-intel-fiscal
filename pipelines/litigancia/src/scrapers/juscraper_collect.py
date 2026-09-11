import os
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Literal
import sqlite3
import pandas as pd
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

from tqdm import tqdm

import juscraper as jus

CollectionMode = Literal["pesquisa_livre", "assunto_tree"]
MANIFEST_FILENAME = "collection_manifest.json"

# Thread-safe print
print_lock = threading.Lock()


def thread_safe_print(*args, **kwargs):
    """Thread-safe print function"""
    with print_lock:
        print(*args, **kwargs)


def ensure_dir_exists(filepath):
    """Create directory if it doesn't exist"""
    directory = os.path.dirname(filepath)
    if directory:
        os.makedirs(directory, exist_ok=True)


def safe_save_json(filepath, data):
    """Safely save JSON data to file, creating directories if needed and avoiding overwriting"""
    try:
        ensure_dir_exists(filepath)

        # Check if file exists, and if so, append a suffix
        original_filepath = filepath
        counter = 2
        while os.path.exists(filepath):
            # Split the filepath into base and extension
            base, ext = os.path.splitext(original_filepath)
            filepath = f"{base}_{counter}{ext}"
            counter += 1

        # Add print statement to show the actual filepath being used
        print(f"Saving file to: {filepath}")

        # Save with ensure_ascii=False to preserve UTF-8 characters
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Add print statement to confirm successful save
        print(f"Successfully saved file: {filepath}")

    except Exception as e:
        print(f"Error saving file {filepath}: {str(e)}")

    # return filepath


assuntos_dict = {
    # Termos SUS
    "general": [
        "sus",
        "sistema único de saúde",
        "sistema unico de saude",
        "atenção básica",
        "rename",
        "relação nacional de medicamentos essenciais",
        "conitec",
        "hospital",
        "consulta",
        "exame",
        "cirurgia",
        "internação",
        "internacao",
        "diagnóstico",
        "diagnostico",
        "prescrição médica",
        "receita médica",
        "laudo médico",
        "atestado médico",
        "prontuário",
        "evolução clínica",
        "alta médica",
        "alta hospitalar",
        "plano de saúde",
        "convênio médico",
        "operadora de saúde",
        "seguradora de saúde",
        "unimed",
        "amil",
        "bradesco saúde",
        "sulamerica",
        "intermédica",
        "golden cross",
        "notre dame",
        "cobertura assistencial",
        "rol de procedimentos",
        "coparticipação",
        "medicamento",
        "remédio",
        "fármaco",
        "princípio ativo",
        "genérico",
        "similar",
        "referência",
        "posologia",
        "bula",
        "contraindicação",
        "medicamento de alto custo",
        "medicamento especializado",
        "medicamento judicial",
        "medicamento experimental",
        "órtese",
        "prótese",
        "doença",
        "enfermidade",
        "patologia",
        "condição de saúde",
        "urgência",
        "emergência",
        "risco de morte",
        "risco à vida",
        "risco de vida",
        "cid",
        "classificação internacional de doenças",
        "sintoma",
        "quadro clínico",
        "quimioterapia",
        "radioterapia",
        "oncológico",
        "oncologia",
        "câncer",
        "neoplasia",
        "tumor",
        "metástase",
    ],
    # Remédios Alto Custo RAY
    "ray": [
        "Abevmy",
        "Bevacizumabe",
        "Acetato de Abiraterona",
        "Abiraterona",
        "Aclasta",
        "Ácido Zoledrônico",
        "Actemra",
        "Tocilizumabe",
        "Actemra SC",
        "Actilyse",
        "Alteplase",
        "Adcetris",
        "Brentuximabe Vedotina",
        "Adempas",
        "Riociguate",
        "Afinitor",
        "Everolimo",
        "Agrastat",
        "Cloridrato de Tirofibana",
        "Agrylin",
        "Cloridrato de Anagrelida",
        "Ajovy",
        "Fremanezumabe",
        "Aldurazyme",
        "Laronidase",
        "Alecensa",
        "Cloridrato de Alectinibe",
        "Alimta",
        "Pemetrexede Dissódico",
        "AmBisome",
        "Anfotericina B",
        "Amgevita",
        "Adalimumabe",
        "Atred",
        "Aubagio",
        "Teriflunomida",
        "Austedo",
        "Deutetrabenazine",
        "Avastin",
        "Avonex",
        "Betainterferona 1A",
        "Avsola",
        "Infliximabe",
        "Azacitidina",
        "Balefio",
        "Bavencio",
        "Avelumabe",
        "Beleodaq",
        "Belinostate",
        "Benlysta",
        "Belimumabe",
        "Berinert",
        "Inibidor de C1 Esterase",
        "Besponsa",
        "Inotuzumab Ozogamicina",
        "Blincyto",
        "Blinatumomabe",
        "Bortezomibe",
        "Bosulif",
        "Bosutinibe",
        "Braftovi",
        "Encorafenibe",
        "Brukinsa",
        "Zanubrutinibe",
        "Bylvay",
        "Odevixibate",
        "Cabazitaxel",
        "Cabometyx",
        "Levomalato de Cabozantine",
        "Calquence",
        "Acalabrutinibe",
        "Camzyos",
        "Mavacanteno",
        "Caprelsa",
        "Vandetanibe",
        "Carbaglu",
        "Ácido Carglúmico",
        "Cardioxane",
        "Cloridrato de Dexrazoxano",
        "Certican",
        "Cibinqo",
        "Abrocitinibe",
        "Cimzia",
        "Certolizumabe Pegol",
        "Cloridrato de Valganciclovir",
        "Copaxone",
        "Acetato de Glatirâmer",
        "Cosentyx",
        "Secuquinumabe",
        "Cotellic",
        "Hemifumarato de Cobimetinibe",
        "Cuprimine",
        "Penicilamina",
        "Cyramza",
        "Ramucirumabe",
        "Dacogen",
        "Decitabina",
        "Dalinvi",
        "Daratumumabe",
        "Dalinvi SC",
        "Danyelza",
        "Naxitamabe",
        "Dasatinibe",
        "Dupixent",
        "Dupilumabe",
        "Egurinel",
        "Pirfenidona",
        "Eligard",
        "Acetato de Leuprorrelina",
        "Elonva",
        "Alfacorifolitropina",
        "Emgality",
        "Galcanezumabe",
        "Enbrel",
        "Etanercepte",
        "Enbrel PFS",
        "Enhertu",
        "Trastuzumabe Deruxtecana",
        "Enspryng",
        "Satralizumabe",
        "Entyvio",
        "Vedolizumabe",
        "Epclusa",
        "Velpatasvir",
        "Sofosbuvir",
        "Epkinly",
        "Epcoritamabe",
        "Erbitux",
        "Cetuximabe",
        "Erelzi",
        "Erfandel",
        "Erdafitinibe",
        "Erivedge",
        "Vismodegibe",
        "Erleada",
        "Apalutamida",
        "Esbriet",
        "Esilato de Nintedanibe",
        "Evenity",
        "Romosozumabe",
        "Evobrig",
        "Brigatinibe",
        "Evrysdi",
        "Risdiplam",
        "Eylia",
        "Aflibercepte",
        "Fabrazyme",
        "Betagalsidase",
        "Fasenra",
        "Benralizumabe",
        "Faslodex",
        "Fulvestranto",
        "Ferriprox",
        "Deferiprona",
        "Firazyr",
        "Acetato de Icatibanto",
        "Firmagon",
        "Acetato de Degarelix",
        "Fludalibbs",
        "Fosfato de Fludarabina",
        "Fortéo",
        "Teriparatida",
        "Gazyva",
        "Obinutuzumabe",
        "Gilenya",
        "Cloridrato de Fingolimode",
        "Giotrif",
        "Dimaleato de Afatinibe",
        "Givlaari",
        "Givosirana Sódica",
        "Glivec",
        "Mesilato de Imatinibe",
        "Halaven",
        "Mesilato de Eribulina",
        "Harvoni",
        "Ledipasvir",
        "Sofosbuvir",
        "Hemcibra",
        "Emicizumabe",
        "Herceptin",
        "Trastuzumabe",
        "Herceptin SC",
        "Herzuma",
        "Hizentra",
        "Imunoglobulina Humana",
        "Humira AC",
        "Hyrimoz",
        "Ibrance",
        "Palbociclibe",
        "Iclusig",
        "Cloridrato de Ponatinibe",
        "Idacio",
        "Ilaris",
        "Canaquinumabe",
        "Imbruvica",
        "Ibrutinibe",
        "Imfinzi",
        "Durvalumabe",
        "Imjudo",
        "Tremelimumabe",
        "Imunoglobulin",
        "Imunoglobulina",
        "Inlyta",
        "Axitinibe",
        "Invega Sustenna",
        "Paliperidona",
        "Iressa",
        "Gefitinibe",
        "Jakavi",
        "Ruxolitinibe",
        "Jaypirce",
        "Pirtobrutinibe",
        "Jemperli",
        "Dostarlimab",
        "Jevtana",
        "Kadcyla",
        "Trastuzumabe Entansina",
        "Kalydeco",
        "Ivacaftor",
        "Kalyme",
        "Tigeciclina",
        "Kanjinti",
        "Kesimpta",
        "Ofatumumabe",
        "Keytruda",
        "Pembrolizumabe",
        "Kiendra",
        "Ácido Fumárico Siponimide",
        "Kisqali",
        "Succinato de Ribociclibe",
        "Koselugo",
        "Sulfato de Selumetinibe",
        "Kuvan",
        "Dicloridrato de Sapropterina",
        "Kyprolis",
        "Carfilzomibe",
        "Lemtrada",
        "Alentuzumabe",
        "Lenangio",
        "Lenalidomida",
        "Lenvima",
        "Mesilato de Lenvatinibe",
        "Leustatin",
        "Cladribina",
        "Libtayo",
        "Cemiplimabe",
        "Lisodren",
        "Mitotano",
        "Litfulo",
        "Imunoglobin",
        "Tosilato de Ritlecitinibe",
        "Livtencity",
        "Maribavir",
        "Lokelma",
        "Ciclossilicato de Zircônio Sódico",
        "Lonsurf",
        "Cloridrato de Tipiracila",
        "Trifluridina",
        "Lorbrena",
        "Lorlatinibe",
        "Lucentis",
        "Ranibizumabe",
        "Lumakras",
        "Sotorasibe",
        "Lynparza",
        "Olaparibe",
        "MabThera",
        "Rituximabe",
        "MabThera SC",
        "Matiz",
        "Mavenclad",
        "Maviret",
        "Pibrentasvir + Glecaprevir",
        "Mekinist",
        "Dimetilsulfóxido de Trametinibe",
        "Mektovi",
        "Binimetinibe",
        "Metalyse",
        "Tenecteplase",
        "Mozobil",
        "Plerixafor",
        "Mvasi",
        "Myfortic",
        "Micofenolato de Sódio",
        "Mylotarg",
        "Gentuzumabe Ozogamicina",
        "Myozyme",
        "Alfaglicosidase",
        "Neo Decapeptyl LP",
        "Acetato de Triptorrelina",
        "Nepexto",
        "Nexavar",
        "Tosilato de Sorafenibe",
        "Nexviazyme",
        "Avalglucosidase Alfa",
        "Nidhi",
        "Ninlaro",
        "Citrato de Ixazomibe",
        "Noxafil",
        "Posaconazol",
        "Nplate",
        "Romiplostim",
        "Nubeqa",
        "Darolutamida",
        "Nucala",
        "Mepolizumabe",
        "Ocrevus",
        "Ocrelizumabe",
        "Ofev",
        "Olumiant",
        "Baricitinibe",
        "Ontruzant",
        "Opdivo",
        "Nivolumabe",
        "Orkambi",
        "Lumacaftor",
        "Ivacaftor",
        "Ozurdex",
        "Dexametasona",
        "Panhematin",
        "Hemina",
        "Pasurta",
        "Erenumabe",
        "Pegasys",
        "Alfapeginterferona",
        "Perjeta",
        "Pertuzumabe",
        "Piqray",
        "Alpelisibe",
        "Pomalyst",
        "Pomalidomida",
        "Praluent",
        "Alirocumabe",
        "Privymtra",
        "Letermovir",
        "Procysbi",
        "Bitartarato de Cisteamina",
        "Prograf",
        "Tacrolimo",
        "Qarziba",
        "Betadinutuximabe",
        "Rapamune",
        "Sirolimo",
        "Rarija",
        "Reblozyl",
        "Luspatercepte",
        "Remicade",
        "Remsima",
        "Renagel",
        "Cloridrato de Sevelâmer",
        "Repatha",
        "Evolocumabe",
        "Replagal",
        "Alfagalsidase",
        "Revolade",
        "Eltrombopague Olamina",
        "Ribomustin",
        "Bendamustina",
        "Rinvoq",
        "Upadacitinibe",
        "Riximyo",
        "RoPolivy",
        "Polatuzumabe Vedotina",
        "Ruxience",
        "Rybrevant",
        "Amivantamabe",
        "Rydapt",
        "Midostaurina",
        "Sandoglobulina Privigen",
        "Sandostatin",
        "Octreotida",
        "Sandostatin LAR",
        "Saphnelo",
        "Anifrolumabe",
        "Scemblix",
        "Cloridrato de Asciminibe",
        "Simponi",
        "Golimumabe",
        "Simulect",
        "Basiliximabe",
        "Skyrizi",
        "Risanquizumabe",
        "Somatuline Autogel",
        "Acetato de Lanreotida",
        "Somavert",
        "Pegvisomanto",
        "Sovaldi",
        "Sofosbuvir",
        "Spravato",
        "Cloridrato de Escetamina",
        "Sprycel",
        "Stelara",
        "Ustequinumabe",
        "Stivarga",
        "Regorafenibe",
        "Sutent",
        "Malato de Sunitinibe",
        "Suzopa",
        "Sybrava",
        "Inclisirana",
        "Sylvant",
        "Siltuximabe",
        "Synagis",
        "Palivizumabe",
        "Tabrecta",
        "Capmatinibe",
        "Tafinlar",
        "Mesilato de Dabrafenibe",
        "Tagrisso",
        "Mesilato de Osimertinibe",
        "Takhzyro",
        "Lanadelumabe",
        "Taltz",
        "Ixequizumabe",
        "Talvey",
        "Talquetamabe",
        "Tarceva",
        "Cloridrato de Erlotinibe",
        "Tasigna",
        "Nilotinibe",
        "Tecentriq",
        "Atezolizumabe",
        "Tecfidera",
        "Fumarato de Dimetila",
        "Tecvayli",
        "Teclistamabe",
        "Tegsedi",
        "Inotersena",
        "Temodal",
        "Temozolomida",
        "Tezspire",
        "Tezepelumabe",
        "Thyrogen",
        "Alfatirotropina",
        "Tobramicina",
        "Torgena",
        "Ceftazidima Pentahidratada",
        "Avibactam Sódico",
        "Torhanz",
        "Trazimera",
        "Tremfya",
        "Guselcumabe",
        "Trisenox",
        "Trióxido de Arsênio",
        "Trodelvy",
        "Sacituzumabe Govitecana",
        "Truqap",
        "Capivasertibe",
        "Truxima",
        "Tykerb",
        "Ditosilato de Lapatinibe",
        "Tysabri",
        "Natalizumabe",
        "Upelior",
        "Diaspartato de Pasireotida",
        "Uptravi",
        "Selexipague",
        "Vabysmo",
        "Faricimabe",
        "Valcyte",
        "Vectibix",
        "Panitumumabe",
        "Velcade",
        "Vemlidy",
        "Hemifumarato de Tenofovir Alafenamida",
        "Venclexta",
        "Venetoclax",
        "Verzenios",
        "Abemaciclibe",
        "Vfend",
        "Voriconazol",
        "Vidaza",
        "Vitrakvi",
        "Larotrectinibe",
        "Volibris",
        "Ambrisentana",
        "Votrient",
        "Cloridrato de Pazopanibe",
        "VOXZOGO",
        "Vosoritida",
        "Vyndaqel",
        "Tafamidis Meglumina",
        "Welireg",
        "Belzutifano",
        "Xalkori",
        "Crizotinibe",
        "Xeljanz",
        "Citrato de Tofacitinibe",
        "Xeloda",
        "Capecitabina",
        "Xenpozyme",
        "Alfaolipudase",
        "Xgeva",
        "Denosumabe",
        "Xolair",
        "Omalizumabe",
        "Xtandi",
        "Enzalutamida",
        "Yervoy",
        "Ipilimumabe",
        "Zavesca",
        "Miglustate",
        "Zedora",
        "Zejula",
        "Tosilato de Niraparibe",
        "Zelboraf",
        "Vemurafenibe",
        "Zinforo",
        "Ceftarolina Fosamila",
        "Zoladex LA",
        "Acetato de Gosserrelina",
        "Zostide",
        "Zytiga",
        "Peginterferona alfa-2a",
        "Tiotepa",
        "Amifampridina",
        "Nintendanibe",
        "Invanz",
        "Xospata",
        "Soliris",
        "brentuximab vedotin",
        "teriparatide",
        "canakinumab",
        "gilteritinib",
        "enzalutamide",
        "ustekinumabe",
        "ertapenem",
        "gilteritinibe",
        "tipiracilo",
        "dinutuximabe beta",
        "eculizumabe",
        "alfaepoetina",
        "acetato de glatiramer",
        "maleato de acalabrutinibe",
        "maleato de neratinibe",
        "somatropina",
        "palivizumab",
        "dasabuvir sodico ",
        "hemifumarato de gilteritinibe",
        "abatacepte",
        "ivosidenibe",
        "alfafolitropina",
        "entecavir",
        "lopinavir",
        "sulfato de isavuconazonio",
        "somatrogona",
        "encorafenib",
        "tepotinibe",
        "sirolimus",
        "dolutegravir",
        "nivolumab",
        "pazopanibe",
        "alpesilibe",
        "cinacalcete",
        "leuprorrelina",
        "asciminib",
        "canabidiol",
        "deltafolitropina",
        "capmatinibe ",
        "abiraterona biosimilar",
        "meglumina, tafamidis",
        "linezolida",
        "peginterferona alfa",
        "ciclosporina",
        "infliximab",
        "erdafinitibe",
        "fampridina",
        "tipiracila",
        "glecaprevir hidratado",
        "deferasirox",
        "ofatumumab",
        "diaspartato de pasireotida",
        "levomalato de cabozantinibe",
        "pirtobrutinib",
        "erlotinibe",
        "triptorrelina",
        "raltegravir",
        "ridisplam",
        "simeprevir",
        "daclatasvir",
        "valganciclovir",
        "menotropina",
        "ruxolitinibe",
        "ganciclovir",
        "teduglutida",
        "dabrafenib",
        "ruxolitinib",
        "filgrastim",
        "tosilato de niraparibe",
        "alfapeginterferona 2b",
        "ledispavir",
        "tretinoina",
        "lapatinibe",
        "ramucirumab",
        "palmitato de paliperidona",
        "eltrombopag",
        "bosentana",
        "veruprevir di-hidratado",
        "adefovir dipivoxila",
        "ritonavir",
        "upacacitinibe",
        "ombitasvir hidratado",
        "crizotinib",
        "alfaeptacogue ativado",
        "acetato de octreotida",
        "selpercatinibe",
        "Herzuma",
        "Remsima",
        "Remsima SC",
        "Truxima",
        "Yuflyma",
        "Vegzelma",
        "BRUKINSA (zanubrutinibe)",
        "zanubrutinibe",
        "BRUKINSA",
        "Berinert 500ui",
        "Berinert",
        "Spravato 28mg Spray Nasal (Escetamina)",
        "Spravato",
        "Escetamina",
    ],
    # Remédios RENAME
    "rename": [
        "ABACAVIR",
        "ABATACEPTE",
        "ABCIXIMABE",
        "ABEMACICLIBE",
        "ABIRATERONA",
        "ACALABRUTINIBE",
        "ACETAZOLAMIDA",
        "ACETILCEFUROXIMA",
        "ACICLOVIR",
        "ACIDO ACETILSALICILICO",
        "ACIDO FOLICO",
        "ACIDO FOLINICO",
        "ACIDO HIALURONICO",
        "ACIDO NICOTINICO",
        "ACIDO PARAMINOSSALICILICO",
        "ACIDO SALICILICO",
        "ACIDO TIOCTICO",
        "ACIDO TRANEXAMICO",
        "ACIDO URSODESOXICOLICO",
        "ACIDO VALPROICO",
        "ACIDO ZOLEDRONICO",
        "ACITRETINA",
        "ADALIMUMABE",
        "ADRENALINA",
        "AFATINIBE",
        "AFLIBERCEPTE",
        "AGUA PARA INJETAVEIS",
        "ALBENDAZOL",
        "ALBUMINA",
        "ALCACHOFRA",
        "ALDESLEUCINA",
        "ALECTINIBE",
        "ALENDRONATO",
        "ALENTUZUMABE",
        "ALFA-ALGLICOSIDASE",
        "ALFA-ASFOTASE",
        "ALFACALCIDOL",
        "ALFADORNASE",
        "ALFAELOSULFASE",
        "ALFAEPOETINA",
        "ALFAINTERFERONA",
        "ALFAPEGINTERFERONA 2A",
        "ALFAPORACTANTO",
        "ALFATALIGLICERASE",
        "ALIROCUMABE",
        "ALOPURINOL",
        "ALPRAZOLAM",
        "ALTEPLASE",
        "AMANTADINA",
        "AMBRISENTANA",
        "AMICACINA",
        "AMIODARONA",
        "AMISSULPRIDA",
        "AMITRIPTILINA",
        "AMOXICILINA",
        "AMOXICILINA + CLAVULANATO DE POTASSIO",
        "ANASTROZOL",
        "ANFOTERICINA B",
        "ANLODIPINO",
        "APIXABANA",
        "ARIPIPRAZOL",
        "ARTEMISINA",
        "ASPARAGINASE",
        "ATAZANAVIR",
        "ATENOLOL",
        "ATEZOLIZUMABE",
        "ATORVASTATINA",
        "ATROPINA",
        "AXITINIBE",
        "AZACITIDINA",
        "AZATIOPRINA",
        "AZITROMICINA",
        "BASILIXIMABE",
        "BCG",
        "BECLOMETASONA",
        "BELIMUMABE",
        "BENDAMUSTINA",
        "BENRALIZUMABE",
        "BENSERAZIDA",
        "BENZILPENICILINA",
        "BENZOILA",
        "BENZONIDAZOL",
        "BERACTANTO",
        "BETA INTERFERONA",
        "BETAGALSIDASE",
        "BETAINTERFERONA 1A",
        "BETAINTERFERONA 1B",
        "BETAMETASONA",
        "BEVACIZUMABE",
        "BEZAFIBRATO",
        "BICALUTAMIDA",
        "BICARBONATO",
        "BIMATOPROSTA",
        "BIPERIDENO",
        "BLEOMICINA",
        "BLINATUMOMABE",
        "BORTEZOMIBE",
        "BOSENTANA",
        "BRENTUXIMABE",
        "BRIMONIDINA",
        "BRINZOLAMIDA",
        "BROMOCRIPTINA",
        "BROMOPRIDA",
        "BUDESONIDA",
        "BUPIVACAINA",
        "BUPRENORFINA",
        "BUPROPIONA",
        "BUSPIRONA",
        "BUSSULFANO",
        "CABAZITAXEL",
        "CABERGOLINA",
        "CABOZANTINIBE",
        "CALCIPOTRIOL",
        "CALCITONINA",
        "CALCITRIOL",
        "CANABIDIOL",
        "CANAQUINUMABE",
        "CAPECITABINA",
        "CAPREOMICINA",
        "CAPTOPRIL",
        "CARBAMAZEPINA",
        "CARBIDOPA",
        "CARBONATO DE CALCIO",
        "CARBONATO DE CALCIO + COLECALCIFEROL",
        "CARBONATO DE LITIO",
        "CARBOPLATINA",
        "CARFILZOMIBE",
        "CARMUSTINA",
        "CARVEDILOL",
        "CASCARA SAGRADA",
        "CEFALEXINA",
        "CEFOTAXIMA",
        "CEFTRIAXONA",
        "CEMIPLIMABE",
        "CERTOLIZUMABE",
        "CETOCONAZOL",
        "CETUXIMABE",
        "CIANOCOBALAMINA",
        "CICLOFOSFAMIDA",
        "CICLOSERINA",
        "CICLOSPORINA",
        "CIDOFOVIR",
        "CILOSTAZOL",
        "CINACALCETE",
        "CIPROFIBRATO",
        "CIPROFLOXACINO",
        "CIPROTERONA",
        "CISPLATINA",
        "CITALOPRAM",
        "CITARABINA",
        "CITRATO DE POTASSIO",
        "CLADRIBINA",
        "CLARITROMICINA",
        "CLAVULANATO DE POTASSIO",
        "CLINDAMICINA",
        "CLOBAZAM",
        "CLOBETASOL",
        "CLOFAZIMINA",
        "CLOMIPRAMINA",
        "CLONAZEPAM",
        "CLOPIDOGREL",
        "CLORAMBUCILA",
        "CLORANFENICOL",
        "CLORETO DE POTASSIO",
        "CLORETO DE SODIO",
        "CLOREXIDINA",
        "CLOROQUINA",
        "CLORPROMAZINA",
        "CLORTALIDONA",
        "CLOZAPINA",
        "COBALAMINA",
        "COBIMETINIBE",
        "CODEINA",
        "COLCHICINA",
        "COLECALCIFEROL",
        "COMPLEXO PROTROMBINICO",
        "CONDROITINA",
        "CRIZOTINIBE",
        "CUMARINA",
        "DABIGATRANA",
        "DABRAFENIBE",
        "DACARBAZINA",
        "DACLATASVIR",
        "DACLIZUMABE",
        "DAGIGRATANA",
        "DALTEPARINA",
        "DANOCRINA",
        "DAPAGLIFLOZINA",
        "DAPSONA",
        "DAPTOMICINA",
        "DARATUMUMABE",
        "DARUNAVIR",
        "DASATINIBE",
        "DAUNORRUBICINA",
        "DECITABINA",
        "DEFERASIROX",
        "DEFERIPRONA",
        "DEGARELIX",
        "DENOSUMABE",
        "DESFERROXAMINA",
        "DESLORATADINA",
        "DESMOPRESSINA",
        "DESVENLAFAXINA",
        "DEXAMETASONA",
        "DEXCLORFENIRAMINA",
        "DEXRAZOSANO",
        "DIAZEPAM",
        "DIDANOSINA",
        "DIETILCARBAMAZINA",
        "DIETILESTILBESTROL",
        "DIGOXINA",
        "DILTIAZEN",
        "DIPIRONA",
        "DOBUTAMINA",
        "DOCETAXEL",
        "DOLUTEGRAVIR",
        "DONEPEZILA",
        "DOPAMINA",
        "DORZOLAMIDA",
        "DOXAZOSINA",
        "DOXICICLINA",
        "DOXORRUBICINA",
        "DULAGLUTIDA",
        "DULOXETINA",
        "DUPILUMABE",
        "DURVALUMABE",
        "DUTASTERIDA",
        "ECULIZUMABE",
        "EDOXABANA",
        "EFAVIRENZ",
        "ELTROMBOPAGUE",
        "EMPAGLIFLOZINA",
        "ENALAPRIL",
        "ENFUVIRTIDA",
        "ENOXAPARINA",
        "ENTACAPONA",
        "ENTECAVIR",
        "ENTRICITABINA",
        "ENZALUTAMIDA",
        "EPINEFRINA",
        "EPIRRUBICINA",
        "EPLERENONA",
        "ERENUMABE",
        "ERIBULINA",
        "ERITROMICINA",
        "ERITROPOETINA",
        "ERLOTINIBE",
        "ESCITALOPRAM",
        "ESOMEPRAZOL",
        "ESPIRAMICINA",
        "ESPIRONOLACTONA",
        "ESTAVUDINA",
        "ESTRADIOL",
        "ESTREPTOMICINA",
        "ESTREPTOQUINASE",
        "ESTRIOL",
        "ESTROGENIOS CONJUGADOS",
        "ETAMBUTOL",
        "ETANERCEPTE",
        "ETINILESTRADIOL",
        "ETINILESTRADIOL + LEVONORGESTREL",
        "ETIONAMIDA",
        "ETOFIBRATO",
        "ETOPOSIDEO",
        "ETOSSUXIMIDA",
        "ETRAVIRINA",
        "EVEROLIMO",
        "EVOLOCUMABE",
        "EXEMESTANO",
        "FAMPRIDINA",
        "FATOR COAGULACAO VII",
        "FATOR IX DE COAGULACAO",
        "FATOR VII DE COAGULACAO ATIVADO RECOMBINANTE",
        "FATOR VIII DE COAGULACAO",
        "FATOR VIII DE COAGULACAO CONTENDO FATOR VON WILLEBRAND",
        "FATOR XIII DE COAGULACAO",
        "FENITOINA",
        "FENOBARBITAL",
        "FENOFIBRATO",
        "FENOTEROL",
        "FENOXIMETILPENICILINA POTASSICA",
        "FENTANILA",
        "FIBRINOGENIO",
        "FILGASTRIM",
        "FILGRASTIM",
        "FINASTERIDA",
        "FINGOLIMODE",
        "FLUCONAZOL",
        "FLUDARABINA",
        "FLUDROCORTISONA",
        "FLUMAZENIL",
        "FLUOCINOLONA",
        "FLUORURACILA",
        "FLUOXETINA",
        "FLUTAMIDA",
        "FLUTICASONA",
        "FLUVASTATINA",
        "FLUVOXAMINA",
        "FOLINATO CALCIO",
        "FORMOTEROL",
        "FORMOTEROL + BUDESONIDA",
        "FOSAMPRENAVIR",
        "FOSFOETANOLAMINA",
        "FOTEMUSTINA",
        "FULVESTRANTO",
        "FUMARATO DE DIMETILA",
        "FUROSEMIDA",
        "GABAPENTINA",
        "GALANTAMINA",
        "GALSULFASE",
        "GEFITINIBE",
        "GENCITABINA",
        "GENFIBROZILA",
        "GENTAMICINA",
        "GLATIRAMER",
        "GLIBENCLAMIDA",
        "GLICEROL",
        "GLICINATO FERRICO",
        "GLICOPIRRONIO",
        "GLICOSAMINA",
        "GLICOSE",
        "GOLIMUMABE",
        "GONADOTROFINA",
        "GOSSERRELINA",
        "GUACO",
        "HALOPERIDOL",
        "HEMINA",
        "HEPARINA SODICA",
        "HIDRALAZINA",
        "HIDROCLOROTIAZIDA",
        "HIDROCORTISONA",
        "HIDROXICLOROQUINA",
        "HIDROXIDO DE ALUMINIO",
        "HIDROXIUREIA",
        "HIPROMELOSE",
        "IBRANDONATO",
        "IBRUTINIBE",
        "IBUPROFENO",
        "ICATIBANTO",
        "IDARRUBICINA",
        "IDEBENONA",
        "IFOSFAMIDA",
        "ILOPROSTA",
        "IMATINIBE",
        "IMIGLUCERASE",
        "IMIQUIMODE",
        "IMUNOGLOBULINA",
        "IMUNOGLOBULINA ANTI-RHO",
        "IMUNOGLOBULINA ANTITETANICA",
        "IMUNOGLOBULINA ANTITIMOCITOS",
        "IMUNOGLOBULINA ANTIVARICELA ZOSTER",
        "IMUNOGLOBULINA HUMANA ANTI-HEPATITE B",
        "IMUNOGLOBULINA HUMANA ANTIRRABICA",
        "INFLIXIMABE",
        "INSULINA",
        "INSULINA ANALOGA DE ACAO PROLONGADA",
        "INSULINA ANALOGA DE ACAO RAPIDA",
        "INSULINA HUMANA NPH",
        "INTERFERON",
        "INTERLEUCINA",
        "IPILIMUMABE",
        "IPRATROPIO",
        "IRINOTECANO",
        "ISOFLAVONA",
        "ISOLEUCINA",
        "ISONIAZIDA",
        "ISOSSORBIDA",
        "ISOTRETINOI",
        "ITRACONAZOL",
        "IVABRADINA",
        "IVERMECTINA",
        "IXABEPILONE",
        "IXAZOMIBE",
        "LACOSAMIDA",
        "LACTULOSE",
        "LAMIVUDINA",
        "LAMOTRIGINA",
        "LANREOTIDA",
        "LAPATINIBE",
        "LATANOPROSTA",
        "LEDISPASVIR",
        "LEFLUNOMIDA",
        "LENALIDOMIDA",
        "LENVATINIBE",
        "LERCANIDIPINO",
        "LETROZOL",
        "LEUPRORRELINA",
        "LEVANLODIPINO",
        "LEVETIRACETAM",
        "LEVODOPA",
        "LEVODOPA + BENSERAZIDA",
        "LEVODOPA + CARBIDOPA",
        "LEVODOPA + CARBIDOPA + ENTACAPONA",
        "LEVOFLOXACINO",
        "LEVONORGESTREL",
        "LEVOTIROXINA",
        "LIDOCAINA",
        "LINAGLIPTINA",
        "LINEZOLIDA",
        "LIPEGFILGRASTIM",
        "LIRAGLUTIDA",
        "LISDEXANFETAMINA",
        "LITIO",
        "LOMUSTINA",
        "LOPINAVIR",
        "LOPINAVIR + RITONAVIR",
        "LORATADINA",
        "LOSARTANA",
        "LOVASTATINA",
        "LUMEFANTRINA",
        "MAGNESIO",
        "MANITOL",
        "MEDICAMENTO NAO ESPECIFICADO",
        "MEDROXIPROGESTERONA",
        "MEFLOQUINA",
        "MEGESTROL",
        "MEGLUMINA",
        "MELFALANO",
        "MEMANTINA",
        "MEPOLIZUMABE",
        "MERCAPTOPURINA",
        "MEROPENEM",
        "MESALAZINA",
        "METADONA",
        "METFORMINA",
        "METILDOPA",
        "METILFENIDATO",
        "METILPREDNISOLONA",
        "METOCLOPRAMIDA",
        "METOPROLOL",
        "METOTREXATO",
        "METRONIDAZOL",
        "METRONIDAZOL + BENZOILMETRONIDAZOL",
        "METROPROLOL",
        "MEZALAZINA",
        "MICOFENOLATO DE MOFETILA",
        "MICOFENOLATO DE SÓDIO",
        "MICONAZOL",
        "MIDAZOLAM",
        "MIGLUSTATE",
        "MILTEFOSINA",
        "MINOCICLINA",
        "MIRABEGRONA",
        "MIRTAZAPINA",
        "MISOPROSTOL",
        "MITOMICINA",
        "MITOTANO",
        "MITOXANTRONA",
        "MORFINA",
        "MOXIFLOXACINO",
        "NALOXONA",
        "NALTREXONA",
        "NAPROXENO",
        "NATALIZUMABE",
        "NEOMICINA",
        "NEOMICINA + BACITRACINA",
        "NEVIRAPINA",
        "NICOTINA",
        "NIFEDIPINO",
        "NILOTINIBE",
        "NIMOTUZUMABE",
        "NINTEDANIBE",
        "NISTATINA",
        "NITROFURANTOINA",
        "NIVOLUMABE",
        "NOREPINEFRINA",
        "NORERISTERONA + ESTRADIOL",
        "NORETISTERONA",
        "NORITISTERONA + ESTRADIOL",
        "NORTRIPTILINA",
        "NUSINERSENA",
        "OBINUTUZUMABE",
        "OCRELIZUMABE",
        "OCTREOTIDA",
        "OFATUMUMABE",
        "OFLOXACINO",
        "OLAMINA",
        "OLANZAPINA",
        "OLAPARIBE",
        "OLARATUMABE",
        "OMALIZUMABE",
        "OMBISTAVIR",
        "OMEPRAZOL",
        "ONDANSETRONA",
        "ORLISTATE",
        "ORNITINA",
        "OSELTAMIVIR",
        "OSIMERTINIBE",
        "OXALIPLATINA",
        "OXAMNIQUINA",
        "OXCARBAZEPINA",
        "OXIBUTININA",
        "OXICODONA",
        "OXIDO FERRICO",
        "OXILIPLATINA",
        "PACLITAXEL",
        "PALBOCICLIBE",
        "PALIPERIDONA",
        "PALIVIZUMABE",
        "PAMIDRONATO",
        "PANCREATINA",
        "PANITUMUMABE",
        "PANTOPRAZOL",
        "PARACETAMOL",
        "PARICALCITOL",
        "PAROXETINA",
        "PAZOPANIBE",
        "PEGASPARGASE",
        "PEGFILGRASTIM",
        "PEGVISOMANTO",
        "PEMBROLIZUMABE",
        "PEMETREXEDE",
        "PENICILAMINA",
        "PENICILINA",
        "PENTAMIDINA",
        "PENTOXIFILINA",
        "PERICIAZINA",
        "PERINDOPRIL",
        "PERMETREXE",
        "PERMETRINA",
        "PEROXIDO DE BENZOILA",
        "PERTUZUMABE",
        "PIFERNIDONA",
        "PILOCARPINA",
        "PINUS PINASTER",
        "PIOGLITAZONA",
        "PIRAZINAMIDA",
        "PIRFENIDONA",
        "PIRIDOSTIGMINA",
        "PIRIDOXINA",
        "PIRIMETAMINA",
        "PIROXICAM",
        "PLANTAGO",
        "PLERIXAFOR",
        "PODOFILINA",
        "PODOFILOTOXINA",
        "POLIMIXINA B",
        "POLIMIXINA B + NEOMICINA + FLUOCINOLONA ACETONIDA + LIDOCAINA",
        "POTASSIO",
        "PRALIDOXIMA",
        "PRAMIPEXOL",
        "PRASUGREL",
        "PRAVASTATINA SODICA",
        "PRAZIQUANTEL",
        "PREDNISOLONA",
        "PREGABALINA",
        "PREMBOLIZUMABE",
        "PRIMAQUINA",
        "PRIMIDONA",
        "PROCARBAZINA",
        "PROMETAZINA",
        "PROPAFENONA",
        "PROPILTIOURACILA",
        "PROPRANOLOL",
        "PROTAMINA",
        "PRUCALOPRIDA",
        "QUETIAPINA",
        "QUININA",
        "RALOXIFENO",
        "RALTEGRAVIR POTASSICO",
        "RAMUCIRUMABE",
        "RANIBIZUMABE",
        "RANITIDINA",
        "RAPAMICINA",
        "RASAGILINA",
        "RASALIGINA",
        "REGORAFENIBE",
        "RESPIRIDONA",
        "RETINOL",
        "RIBAVIRINA",
        "RIBOCICLIBE",
        "RIFAMICINA",
        "RIFAMPICINA",
        "RIFAMPICINA + ISONIAZIDA",
        "RIFAMPICINA + ISONIAZIDA + PIRAZINAMIDA",
        "RIFAMPICINA + ISONIAZIDA + PIRAZINAMIDA +  CLORIDRATO DE ETAMBUTOL",
        "RILUZOL",
        "RIOCIGUATE",
        "RISANQUIZUMABE",
        "RISEDRONATO",
        "RISPERIDONA",
        "RITONAVIR",
        "RITUXIMABE",
        "RIVAROXABANA",
        "RIVASTIGMINA",
        "RUFINAMIDA",
        "RUXOLITINIBE",
        "SACUBITRIL VALSARTANA SODICA HIDRATADA",
        "SALBUTAMOL",
        "SALMETEROL",
        "SALMETEROL + FLUTICASONA",
        "SAPROPTERINA",
        "SAQUINAVIR",
        "SECUQUINUMABE",
        "SELEGILINA",
        "SERTRALINA",
        "SEVELAMER",
        "SILDENAFILA",
        "SILTUXIMABE",
        "SINITINIBE",
        "SINVASTATINA",
        "SIROLIMO",
        "SITAGLIPTINA",
        "SOFOSBUVIR",
        "SOMATROPINA",
        "SORAFENIBE",
        "SORO ANTICROTA",
        "SORO ANTIESCORPIANICO",
        "SORO ANTILONOMICO",
        "SORO ANTITETANICO",
        "SULFADIAZINA",
        "SULFAMETOXAZOL + TRIMETOPRIMA",
        "SULFASSALAZINA",
        "SULFATO FERROSO",
        "SULFATO MAGNESIO",
        "SULFONILUREIA",
        "SULPIRIDA",
        "SUNITINIBE",
        "SYSTANE",
        "T4",
        "TACROLIMO",
        "TADAFILA",
        "TAFAMIDIS",
        "TALIDOMIDA",
        "TAMOXIFENO",
        "TASONERMINA",
        "TECLOZANA",
        "TELMISARTANA",
        "TEMOZOLOMIDA",
        "TENECTEPLASE",
        "TENIPOSIDEO",
        "TENOFOVIR",
        "TENOFOVIR DESOPROXILA",
        "TENOFOVIR DESOPROXILA + LAMIVUDINA",
        "TENOFOVIR DESOPROXILA + LAMIVUDINA + EFAVIRENZ",
        "TENSIROLIMO",
        "TERIFLUNOMIDA",
        "TERIPARATIDA",
        "TESTOSTERONA",
        "TETRACICLINA",
        "TIAMINA",
        "TICAGRELOR",
        "TIMOGLOBULINA",
        "TIMOLOL",
        "TIMOMODULINA",
        "TIOGUANINA",
        "TIORIDAZINA",
        "TIOTROPIO",
        "TIOTROPIO + OLODATEROL",
        "TIPRANAVIR",
        "TIREOTROFINA",
        "TIROFIBANA",
        "TOBRAMICINA",
        "TOCILIZUMABE",
        "TOFACITINIBE",
        "TOLCAPONA",
        "TOPIRAMATO",
        "TOPOTECANO",
        "TOXINA BOTULINICA A",
        "TRABECTEDINA",
        "TRAMADOL",
        "TRAMETINIBE",
        "TRASTUZUMABE",
        "TRAVOPROSTA",
        "TRAZODONA",
        "TRETINOINA",
        "TRIEXIFENIDIL",
        "TRIMETAZIDINA",
        "TRIMETOPRIMA",
        "TRIOXIDO DE ARSENIO",
        "TRIPTORRELINA",
        "TROMBOPOIETINA",
        "USTEQUINUMABE",
        "VACINA SARAMPO, CAXUMBA, RUBEOLA",
        "VALACICLOVIR",
        "VALGANCICLOVIR",
        "VALPROATO",
        "VALSARTANA",
        "VANDETANIBE",
        "VARFARINA",
        "VEDOLIZUMABE",
        "VEMURAFENIBE",
        "VENETOCLAX",
        "VENLAFAXINA",
        "VENURAFENIBE",
        "VERAPAMIL",
        "VERMURAGENIBE",
        "VIGABATRINA",
        "VILDAGLIPTINA",
        "VIMBLASTINA",
        "VINCRISTINA",
        "VINFLUNINA",
        "VINORELBINA",
        "VISMODEGIBE",
        "VORICONAZOL",
        "VORTIOXETINA",
        "ZANAMIVIR",
        "ZIDOVUDINA",
        "ZINCO",
        "ZIPRASIDONA",
        "ZOLPIDEM",
    ],
    # Termos Execução Fiscal
    "exec_fiscal": [
        "execução fiscal",
        "IPTU",
        "ISS",
        "taxas municipais",
        "cobrança judicial",
        "dívida ativa",
        "tributos municipais",
        "crédito tributário",
        "devedor",
        "execução fiscal administrativa",
        "imposto",
    ],
    # Termos Trânsito
    "transito": [
        "multa de trânsito",
        "IPVA",
        "taxas de licenciamento",
        "taxas de trânsito",
        "cobrança de multas",
        "infrações de trânsito",
        "tributos estaduais",
        "cobrança administrativa",
        "débitos veiculares",
        "regularização veicular",
        "cobrança judicial de multas",
        "dívida ativa de trânsito",
        "penalidades de trânsito",
        "taxas obrigatórias",
        "tributos sobre veículos",
        "cobrança de IPVA",
        "licenciamento anual",
        "autuação de trânsito",
        "recursos de multas",
        "cobrança de taxas veiculares",
    ],
    # Termos TEA
    "tea": [
        "autismo",
        "TEA",
        "Asperger",
        "transtorno do espectro autista",
        "autista",
        "síndrome de Asperger",
        "diagnóstico autismo",
        "diagnóstico TEA",
        "diagnóstico Asperger",
        "sintomas autismo",
        "sintomas TEA",
        "sintomas Asperger",
        "tratamento autismo",
        "tratamento TEA",
        "tratamento Asperger",
        "terapia autismo",
        "intervenção autismo",
        "intervenção TEA",
        "intervenção Asperger",
        "avaliação autismo",
        "avaliação TEA",
        "avaliação Asperger",
        "crianças autistas",
        "adultos autistas",
        "autismo infantil",
        "desenvolvimento autista",
        "sinais autismo",
        "comportamento autista",
        "inclusão autismo",
        "educação especial autismo",
        "apoio familiar autismo",
        "terapia ocupacional autismo",
        "fonoaudiologia autismo",
        "psicoterapia autismo",
        "autismo leve",
        "autismo severo",
        "autismo clássico",
        "autismo atípico",
        "transtorno global do desenvolvimento",
        "TGD",
        "habilidades sociais autismo",
        "comunicação autismo",
        "sensibilidade sensorial autismo",
        "interesses restritos autismo",
        "rotinas autismo",
        "estereotipias autismo",
    ],
    # Termos PGE
    "pge": ['"Fazenda Publica do Estado de Sao Paulo"'],
    # Termo Sem Assunto
    "sem_assunto": None,
    # Custom
    "custom": [
        "saude caixa",
        "unimed batatais",
        "unimed santos",
        "unimed sertaozinho",
        "unimed franca",
    ],
}


def process_single_assunto(
    assunto,
    output_dir,
    tribunal,
    dataDisponibilizacaoInicio,
    dataDisponibilizacaoFim,
    classes=None,
    varas=None,
    consulta_type="cjpg",
):
    """
    Process a single assunto - designed to be run in parallel

    Returns:
        tuple: (assunto_name, processes_extracted, error_info)
    """
    assunto_low = assunto.lower() if assunto else "sem_assunto"
    thread_safe_print(f"Processing {assunto}...")

    # Create a separate scraper instance for this thread
    jus_scraper = jus.scraper(tribunal)

    if assunto_low == "sem_assunto":
        assunto = None

    try:
        result = jus_scraper.cjpg(
            classes=classes,
            pesquisa=f'"{assunto}"' if assunto else None,
            varas=varas,
            data_inicio=dataDisponibilizacaoInicio,
            data_fim=dataDisponibilizacaoFim,
        )

        # Handle the fact that result is a DataFrame, not a dict
        processes_extracted = 0
        if isinstance(result, pd.DataFrame) and not result.empty:
            processes_extracted = len(result)
            thread_safe_print(f'Found {processes_extracted} processes for "{assunto}"')

            # Only create directory and save JSON if we have actual results
            safe_assunto = (
                (assunto if assunto else "sem_assunto")
                .replace("/", "_")
                .replace(" ", "_")
            )
            assunto_dir = os.path.join(output_dir, safe_assunto)
            os.makedirs(assunto_dir, exist_ok=True)

            # Save the DataFrame as JSON with proper UTF-8 encoding and assunto-based filename
            json_filename = f"{safe_assunto}.json"
            json_filepath = os.path.join(assunto_dir, json_filename)

            data_dict = {
                "status": "success",
                "message": f"Dados para {assunto if assunto else 'sem_assunto'}",
                "count": len(result),
                "items": result.to_dict(orient="records"),
            }

            # Save with ensure_ascii=False to preserve UTF-8 characters
            with open(json_filepath, "w", encoding="utf-8") as f:
                json.dump(data_dict, f, ensure_ascii=False, indent=2)

            thread_safe_print(f"Saved data to: {json_filepath}")
        else:
            thread_safe_print(
                f'No results found for "{assunto}" - skipping file creation'
            )
            # Don't create any files when there are no results

        thread_safe_print(
            f"Finished processing {assunto if assunto else 'sem_assunto'}"
        )
        thread_safe_print(
            f"Processes extracted for {assunto if assunto else 'sem_assunto'}: {processes_extracted}"
        )

        return (assunto if assunto else "sem_assunto", processes_extracted, None)

    except Exception as e:
        error_msg = str(e)

        # Check if it's the "no results" error - don't treat as error, just skip
        if (
            "número de páginas" in error_msg
            or "seletor de número de páginas" in error_msg
        ):
            thread_safe_print(
                f'No results found for "{assunto}" (no pagination found) - skipping'
            )
            return (assunto if assunto else "sem_assunto", 0, None)
        else:
            # Only save error files for actual errors (not "no results")
            thread_safe_print(f"Error processing {assunto_low}: {error_msg}")

            error_log_dir = os.path.join(output_dir, "metadata", "errors")
            os.makedirs(error_log_dir, exist_ok=True)

            safe_assunto = assunto_low.replace("/", "_").replace(" ", "_")
            error_log_path = os.path.join(
                error_log_dir, f"{safe_assunto}_critical_error.json"
            )

            with open(error_log_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "assunto": assunto,
                        "error": error_msg,
                        "timestamp": datetime.now().isoformat(),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

            return (assunto if assunto else "sem_assunto", 0, error_msg)


def format_assunto_tree_ids(ids: list[int]) -> str:
    """Comma-separated SAJ assunto IDs (must not be URL-encoded)."""
    return ",".join(str(i) for i in ids)


def _cjpg_download_resolved_trees(
    *,
    session,
    u_base: str,
    download_path: str,
    pesquisa: str,
    classe_values: str | None,
    assunto_values: str | None,
    data_inicio: str,
    data_fim: str,
    sleep_time: float,
    get_n_pags_callback,
) -> str:
    """CJPG download with pre-resolved tree values (bypasses get_tree_values)."""
    query = {
        "conversationId": "",
        "dadosConsulta.pesquisaLivre": pesquisa,
        "tipoNumero": "UNIFICADO",
        "numeroDigitoAnoUnificado": "",
        "foroNumeroUnificado": "",
        "dadosConsulta.nuProcesso": "",
        "classeTreeSelection.values": classe_values,
        "assuntoTreeSelection.values": assunto_values,
        "dadosConsulta.dtInicio": data_inicio,
        "dadosConsulta.dtFim": data_fim,
        "varasTreeSelection.values": None,
        "dadosConsulta.ordenacao": "DESC",
    }

    r0 = session.get(f"{u_base}cjpg/pesquisar.do", params=query)
    try:
        n_pags = get_n_pags_callback(r0)
    except Exception as e:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        debug_dir = os.path.join(download_path, "cjpg_debug")
        os.makedirs(debug_dir, exist_ok=True)
        debug_file = os.path.join(debug_dir, f"cjpg_primeira_pagina_{timestamp}.html")
        with open(debug_file, "w", encoding="utf-8") as f:
            f.write(r0.text)
        raise ValueError(
            f"Erro ao extrair número de páginas: {e}. HTML salvo em: {debug_file}"
        ) from e

    paginas = range(1, n_pags + 1)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"{download_path}/cjpg/{timestamp}"
    os.makedirs(path, exist_ok=True)

    for pag in tqdm(range(1, n_pags + 1), desc="Baixando documentos"):
        time.sleep(sleep_time)
        u = f"{u_base}cjpg/trocarDePagina.do?pagina={pag + 1}&conversationId="
        r = session.get(u)
        file_name = f"{path}/cjpg_{pag + 1:05d}.html"
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(r.text)

    return path


def cjpg_collect_with_tree_ids(
    tribunal: str,
    *,
    assunto_tree_ids: list[int],
    classes: list[str] | None,
    data_inicio: str,
    data_fim: str,
    download_path: str,
) -> pd.DataFrame:
    """Collect CJPG results using raw assunto tree IDs (avoids juscraper URL-encoding bug)."""
    from juscraper.courts.tjsp.cjpg_download import get_tree_values
    from juscraper.courts.tjsp.cjpg_parse import cjpg_n_pags

    scraper = jus.scraper(tribunal)
    classe_vals = (
        get_tree_values(classes, scraper.session, "classe") if classes else None
    )
    assunto_vals = format_assunto_tree_ids(assunto_tree_ids)

    def get_n_pags_callback(r0):
        html = r0.content if hasattr(r0, "content") else r0
        return cjpg_n_pags(html)

    path = _cjpg_download_resolved_trees(
        session=scraper.session,
        u_base=scraper.u_base,
        download_path=download_path,
        pesquisa="",
        classe_values=classe_vals,
        assunto_values=assunto_vals,
        data_inicio=data_inicio,
        data_fim=data_fim,
        sleep_time=scraper.sleep_time,
        get_n_pags_callback=get_n_pags_callback,
    )

    result = scraper.cjpg_parse(path)
    shutil.rmtree(path, ignore_errors=True)

    return result


def _is_no_results_error(error_msg: str) -> bool:
    return (
        "número de páginas" in error_msg or "seletor de número de páginas" in error_msg
    )


def _save_cjpg_result(
    result: pd.DataFrame,
    output_dir: str,
    subdir_name: str,
    json_basename: str,
    message: str,
) -> int:
    """Persist a CJPG DataFrame under output_dir/subdir_name/. Returns row count."""
    if not isinstance(result, pd.DataFrame) or result.empty:
        return 0

    safe_name = subdir_name.replace("/", "_").replace(" ", "_")
    assunto_dir = os.path.join(output_dir, safe_name)
    os.makedirs(assunto_dir, exist_ok=True)
    json_filepath = os.path.join(assunto_dir, f"{json_basename}.json")

    data_dict = {
        "status": "success",
        "message": message,
        "count": len(result),
        "items": result.to_dict(orient="records"),
    }
    with open(json_filepath, "w", encoding="utf-8") as f:
        json.dump(data_dict, f, ensure_ascii=False, indent=2)
    thread_safe_print(f"Saved data to: {json_filepath}")
    return len(result)


def process_assunto_tree(
    BASE_OUTPUT_DIR: str,
    *,
    tribunal: str,
    date_range: tuple[str, str] | None,
    classes: list[str] | None,
    varas: list[str] | None,
    assuntos_juridicos: list[str],
    assunto_tree_ids: list[int] | None = None,
) -> int:
    """Single CJPG query with assuntoTreeSelection (one unified SAJ payload per month)."""
    current_date = datetime.now().strftime("%Y%m%d")
    output_dir = os.path.join(BASE_OUTPUT_DIR, current_date)
    os.makedirs(output_dir, exist_ok=True)

    if not date_range:
        data_inicio = "01-01-2025"
        data_fim = "14-08-2025"
    else:
        data_inicio = date_range[0]
        data_fim = date_range[1]

    if not assunto_tree_ids:
        raise ValueError(
            "assunto_tree mode requires assunto_tree_ids (raw SAJ node IDs). "
            "Labels via juscraper get_tree_values are URL-encoded and break "
            "classe+assunto combined queries."
        )

    thread_safe_print(
        f"Assunto tree query: {len(assunto_tree_ids)} juridical subject ID(s), "
        f"{data_inicio} to {data_fim}"
    )

    try:
        result = cjpg_collect_with_tree_ids(
            tribunal,
            assunto_tree_ids=assunto_tree_ids,
            classes=classes,
            data_inicio=data_inicio,
            data_fim=data_fim,
            download_path=output_dir,
        )
    except Exception as e:
        error_msg = str(e)
        if _is_no_results_error(error_msg):
            thread_safe_print("No results found for assunto tree query - skipping")
            return 0
        raise

    processes_extracted = _save_cjpg_result(
        result,
        output_dir,
        "assunto_tree",
        "assunto_tree",
        f"Dados para assunto tree ({len(assuntos_juridicos)} nós)",
    )
    thread_safe_print(f"Processes extracted (assunto_tree): {processes_extracted}")

    dedup_file_path = deduplicate_and_save_json(output_dir)
    if dedup_file_path:
        convert_json_to_csv(dedup_file_path)
        convert_json_to_db(dedup_file_path)

    print(f"All processing completed. Output saved to: {output_dir}")
    print(f"Date Range: {data_inicio} to {data_fim}")
    return processes_extracted


def write_collection_manifest(
    base_output_dir: str,
    config_meta: dict | None,
    date_range: tuple[str, str] | None,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    if not config_meta:
        return

    manifest_path = os.path.join(base_output_dir, MANIFEST_FILENAME)
    os.makedirs(base_output_dir, exist_ok=True)

    payload = dict(config_meta)
    if date_range is not None:
        payload["date_range"] = list(date_range)
    if started_at is not None:
        payload["started_at"] = started_at
    if finished_at is not None:
        payload["finished_at"] = finished_at

    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            existing = json.load(f)
        if started_at and existing.get("started_at"):
            payload["started_at"] = existing["started_at"]

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def process(
    assuntos,
    BASE_OUTPUT_DIR,
    tribunal="tjsp",
    data_range: tuple = None,
    classes=None,
    varas=None,
    max_workers=4,
):
    """
    Process multiple assuntos in parallel

    Args:
        max_workers (int): Maximum number of parallel workers. Default is 4.
                          Adjust based on your system and API rate limits.
    """
    # Create output directory with current date
    current_date = datetime.now().strftime("%Y%m%d")
    output_dir = os.path.join(BASE_OUTPUT_DIR, current_date)
    os.makedirs(output_dir, exist_ok=True)

    progress_json = os.path.join(output_dir, "progress.json")

    if not data_range:
        dataDisponibilizacaoInicio = "01-01-2025"
        dataDisponibilizacaoFim = "14-08-2025"
    else:
        dataDisponibilizacaoInicio = data_range[0]
        dataDisponibilizacaoFim = data_range[1]

    if assuntos is None:
        assuntos = ["sem_assunto"]

    if os.path.exists(progress_json):
        # Load existing progress
        with open(progress_json, "r", encoding="utf-8") as f:
            progress_data = json.load(f)
        total_processes_extracted = progress_data.get("total_processes_extracted", 0)
        assunto_processes = progress_data.get("assunto_processes", {})

        print("Assuntos already processed: ", list(assunto_processes.keys()))

    else:
        total_processes_extracted = 0
        assunto_processes = {}

    print(f"Starting parallel processing with {max_workers} workers...")
    print(f"Processing {len(assuntos)} assuntos")

    assuntos_left = [a for a in assuntos if a not in assunto_processes.keys()]

    # Use ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_assunto = {
            executor.submit(
                process_single_assunto,
                assunto,
                output_dir,
                tribunal,
                dataDisponibilizacaoInicio,
                dataDisponibilizacaoFim,
                classes,
                varas,
            ): assunto
            for assunto in assuntos_left
        }

        # Collect results as they complete
        for future in as_completed(future_to_assunto):
            assunto_name, processes_extracted, error = future.result()
            assunto_processes[assunto_name] = processes_extracted
            total_processes_extracted += processes_extracted

            if error:
                thread_safe_print(
                    f"Task for {assunto_name} completed with error: {error}"
                )
            else:
                thread_safe_print(f"Task for {assunto_name} completed successfully")

            progress_data = {
                "total_processes_extracted": total_processes_extracted,
                "assunto_processes": assunto_processes,
            }

            # Save progress after each task
            with open(progress_json, "w", encoding="utf-8") as f:
                json.dump(progress_data, f, ensure_ascii=False, indent=2)
            print(
                f"Progress saved. Total processes extracted: {total_processes_extracted}"
            )

    print("\nAll parallel tasks completed. Starting post-processing...")

    # Post-processing (deduplication, conversion) - done sequentially
    dedup_file_path = deduplicate_and_save_json(output_dir)

    if dedup_file_path:
        convert_json_to_csv(dedup_file_path)
        convert_json_to_db(dedup_file_path)

    # Print summary
    print("\n--- Extraction Summary ---")
    for assunto, processes in assunto_processes.items():
        print(f"Processes extracted for {assunto}: {processes}")
    print(f"Total processes extracted: {total_processes_extracted}")
    print(f"All processing completed. Output saved to: {output_dir}")
    print(f"Date Range: {dataDisponibilizacaoInicio} to {dataDisponibilizacaoFim}")


def deduplicate_and_save_json(output_dir):
    """
    Deduplicate JSON data from all assunto collections by grouping by numero_processo and save the result.

    Args:
        output_dir (str): The output directory containing subdirectories with JSON files from all assuntos

    Returns:
        str: Path to the saved deduplicated JSON file
    """
    json_dir_path = Path(output_dir)
    data = []

    print("Starting deduplication process...")
    print(f"Scanning directory: {json_dir_path}")

    # Walk through each subdirectory, skipping 'metadata'
    files_processed = 0
    for subdir in json_dir_path.iterdir():
        if subdir.is_dir() and subdir.name != "metadata":
            print(f"Processing subdirectory: {subdir.name}")
            for json_file in subdir.glob("*.json"):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        json_data = json.load(f)
                        items = json_data.get("items", [])
                        data.extend(items)
                        files_processed += 1
                        print(f"  Loaded {len(items)} items from {json_file.name}")
                except (json.JSONDecodeError, PermissionError) as e:
                    print(f"Error processing {json_file}: {e}")

    print(f"Total files processed: {files_processed}")
    print(f"Total items loaded before deduplication: {len(data)}")

    # Create DataFrame
    if not data:
        print("No data was loaded. Check the directory path and file contents.")
        return None

    df = pd.DataFrame(data)
    print(f"DataFrame created with {len(df)} rows")

    # Group by numero_processo and deduplicate
    print("Starting deduplication by numero_processo...")
    proc_df_list = []
    # Group by cd_processo and deduplicate
    print("Starting deduplication by cd_processo...")
    unique_processes = df["cd_processo"].unique()
    print(f"Found {len(unique_processes)} unique processes")

    for i, cd_processo in enumerate(unique_processes):
        if i % 100 == 0:  # Progress indicator
            print(f"Processing process {i + 1}/{len(unique_processes)}")

        df_processo = df[df["cd_processo"] == cd_processo]

        # Combine all decisao entries with separators
        decisao_combined = " | ".join(
            [
                f"----- {j} ----- | " + str(text)
                for j, text in enumerate(df_processo["decisao"])
                if pd.notna(text) and str(text).strip()
            ]
        )

        proc_df_list.append(
            {
                "cd_processo": cd_processo,
                "id_processo": df_processo["id_processo"].iloc[0],
                "classe": df_processo["classe"].iloc[0],
                "assunto": df_processo["assunto"].iloc[0],
                "magistrado": df_processo["magistrado"].iloc[0],
                "comarca": df_processo["comarca"].iloc[0],
                "foro": df_processo["foro"].iloc[0],
                "vara": df_processo["vara"].iloc[0],
                "data_disponibilizacao": df_processo["data_disponibilizacao"].iloc[-1],
                "decisao": decisao_combined,
            }
        )
    proc_df = pd.DataFrame(proc_df_list)
    print(f"Deduplication completed. Reduced from {len(df)} to {len(proc_df)} records")

    # Create the final JSON structure
    json_data = {
        "status": "success",
        "message": "Sucesso - Dados consolidados de todos os assuntos",
        "count": len(proc_df),
        "original_count": len(df),
        "assuntos_processed": files_processed,
        "items": proc_df.to_dict(orient="records"),
    }

    def decode_html_entities(text):
        if isinstance(text, str):
            return BeautifulSoup(text, "html.parser").get_text()
        return text

    # Apply HTML decoding to the data
    print("Decoding HTML entities...")
    if isinstance(json_data, dict):
        for key, value in json_data.items():
            if key == "items" and isinstance(value, list):
                for item in value:
                    for k, v in item.items():
                        if isinstance(v, str):
                            item[k] = decode_html_entities(v)
            elif isinstance(value, str):
                json_data[key] = decode_html_entities(value)

    # Save the deduplicated JSON file
    dedup_file_path = os.path.join(json_dir_path, "process_grouped_all_assuntos.json")
    with open(dedup_file_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    print(f"Deduplicated data saved to: {dedup_file_path}")
    print("Summary:")
    print(f"  - Original items from all assuntos: {len(df)}")
    print(f"  - Deduplicated items: {len(proc_df)}")
    print(f"  - Files processed: {files_processed}")
    print(f"  - Reduction: {len(df) - len(proc_df)} duplicate communications removed")

    return dedup_file_path


def convert_json_to_csv(json_file_path):
    """
    Convert the deduplicated JSON file to CSV format.

    Args:
        json_file_path (str): Path to the JSON file to convert

    Returns:
        str: Path to the saved CSV file
    """
    if not json_file_path:
        print("No JSON file path provided")
        return None

    try:
        print("Converting JSON to CSV...")

        # Load the JSON data
        with open(json_file_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)

        # Extract items from the JSON structure
        items = json_data.get("items", [])

        if not items:
            print("No items found in JSON file")
            return None

        # Create DataFrame from the items directly
        df = pd.DataFrame(items)
        print(f"DataFrame created with shape: {df.shape}")
        print(f"Columns: {list(df.columns)}")

        # Handle list columns (convert to string representation for CSV)
        for col in df.columns:
            if df[col].dtype == "object":
                # Check if any value in the column is a list
                sample_values = df[col].dropna().head(10)
                if any(isinstance(val, list) for val in sample_values):
                    print(f"Converting list column '{col}' to string representation")
                    df[col] = df[col].apply(
                        lambda x: str(x) if isinstance(x, list) else x
                    )

        # Create CSV file path
        csv_file_path = json_file_path.replace(".json", ".csv")

        # Save to CSV with proper handling of special characters
        df.to_csv(csv_file_path, index=False, encoding="utf-8", sep=",", quoting=1)

        print(f"CSV file saved to: {csv_file_path}")
        print(f"CSV contains {len(df)} rows and {len(df.columns)} columns")

        return csv_file_path

    except Exception as e:
        print(f"Error converting JSON to CSV: {str(e)}")
        import traceback

        traceback.print_exc()
        return None


def convert_json_to_db(json_file_path, db_name=None):
    """
    Convert the deduplicated JSON file to SQLite database.

    Args:
        json_file_path (str): Path to the JSON file to convert
        db_name (str, optional): Name of the database file. If None, uses same name as JSON file

    Returns:
        str: Path to the saved database file
    """
    if not json_file_path:
        print("No JSON file path provided")
        return None

    try:
        print("Converting JSON to SQLite database...")

        # Load the JSON data
        with open(json_file_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)

        # Extract items from the JSON structure
        items = json_data.get("items", [])

        if not items:
            print("No items found in JSON file")
            return None

        # Create DataFrame from the items directly
        df = pd.DataFrame(items)
        print(f"DataFrame created with shape: {df.shape}")

        # Handle list columns (convert to JSON string for database storage)
        for col in df.columns:
            if df[col].dtype == "object":
                # Check if any value in the column is a list
                sample_values = df[col].dropna().head(10)
                if any(isinstance(val, list) for val in sample_values):
                    print(f"Converting list column '{col}' to JSON string for database")
                    df[col] = df[col].apply(
                        lambda x: (
                            json.dumps(x, ensure_ascii=False)
                            if isinstance(x, list)
                            else x
                        )
                    )

        # Create database file path
        if db_name is None:
            db_file_path = json_file_path.replace(".json", ".db")
        else:
            json_dir = os.path.dirname(json_file_path)
            db_file_path = os.path.join(json_dir, db_name)

        # Connect to SQLite database
        conn = sqlite3.connect(db_file_path)

        # Save DataFrame to database
        table_name = "processos_comunicacoes_consolidado"
        df.to_sql(table_name, conn, if_exists="replace", index=False)

        # Create indexes for better query performance
        cursor = conn.cursor()

        print("Creating database indexes...")
        # Index on cd_processo (primary key-like) - UPDATED
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS idx_cd_processo ON {table_name} (cd_processo)"
        )

        # Index on data_disponibilizacao for date queries
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS idx_data_disponibilizacao ON {table_name} (data_disponibilizacao)"
        )

        conn.commit()

        # Get table info
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        row_count = cursor.fetchone()[0]

        cursor.execute(f"PRAGMA table_info({table_name})")
        columns_info = cursor.fetchall()

        conn.close()

        print(f"Database saved to: {db_file_path}")
        print(
            f"Table '{table_name}' created with {row_count} rows and {len(columns_info)} columns"
        )
        print("Indexes created on: cd_processo, data_disponibilizacao")

        return db_file_path

    except Exception as e:
        print(f"Error converting JSON to database: {str(e)}")
        import traceback

        traceback.print_exc()
        return None


def zip_files(BASE_OUTPUT_DIR):
    """
    Zip the BASE_OUTPUT_DIR and save the zip file to the same directory.

    Args:
        BASE_OUTPUT_DIR (str): Path to the directory to be zipped.

    Returns:
        str: Path to the saved zip file.
    """
    zip_file_path = f"{BASE_OUTPUT_DIR}.zip"
    with zipfile.ZipFile(zip_file_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(BASE_OUTPUT_DIR):
            for file in files:
                zip_file.write(
                    os.path.join(root, file),
                    os.path.relpath(os.path.join(root, file), BASE_OUTPUT_DIR),
                )
    print(f"BASE_OUTPUT_DIR zipped to {zip_file_path}")
    return zip_file_path


def main(
    *,
    BASE_OUTPUT_DIR: str,
    collection_mode: CollectionMode,
    tribunal: str = "tjsp",
    date_range: tuple[str, str] | None = None,
    classes: list[str] | None = None,
    varas: list[str] | None = None,
    max_workers: int = 4,
    pesquisa_terms: list[str] | None = None,
    campaign_subdir: str | None = None,
    assuntos_juridicos: list[str] | None = None,
    assunto_tree_ids: list[int] | None = None,
    config_meta: dict | None = None,
) -> None:
    """Run one monthly collection using pesquisa_livre or assunto_tree mode."""
    started_at = datetime.now().isoformat()
    write_collection_manifest(
        BASE_OUTPUT_DIR,
        config_meta,
        date_range,
        started_at=started_at,
    )

    if collection_mode == "pesquisa_livre":
        terms = pesquisa_terms or ["sem_assunto"]
        subdir = campaign_subdir or "default"
        base_output_dir = os.path.join(BASE_OUTPUT_DIR, subdir)
        process(
            terms,
            base_output_dir,
            tribunal,
            date_range,
            classes=classes,
            varas=varas,
            max_workers=max_workers,
        )
    elif collection_mode == "assunto_tree":
        process_assunto_tree(
            BASE_OUTPUT_DIR,
            tribunal=tribunal,
            date_range=date_range,
            classes=classes,
            varas=varas,
            assuntos_juridicos=assuntos_juridicos or [],
            assunto_tree_ids=assunto_tree_ids,
        )
    else:
        raise ValueError(f"Unknown collection_mode: {collection_mode}")

    write_collection_manifest(
        BASE_OUTPUT_DIR,
        config_meta,
        date_range,
        started_at=started_at,
        finished_at=datetime.now().isoformat(),
    )
    zip_files(BASE_OUTPUT_DIR)


def main_legacy(
    BASE_OUTPUT_DIR,
    assuntos_keys,
    assuntos_dict,
    tribunal="tjsp",
    date_range=None,
    classes=None,
    varas=None,
    max_workers=4,
):
    """Legacy entrypoint: pesquisa_livre via assuntos_keys / assuntos_dict."""
    for assuntos_key in assuntos_keys:
        assuntos_list = assuntos_dict[assuntos_key]
        base_output_dir = os.path.join(BASE_OUTPUT_DIR, assuntos_key)
        process(
            assuntos_list,
            base_output_dir,
            tribunal,
            date_range,
            classes=classes,
            varas=varas,
            max_workers=max_workers,
        )
    zip_files(BASE_OUTPUT_DIR)


def config_meta_from_recollect(config) -> dict:
    """Build manifest payload from a RecollectConfig instance."""
    meta = {
        "config_id": config.id,
        "execution": config.execution,
        "mode": config.mode,
        "description": config.description,
        "classes": config.classes,
    }
    if config.assuntos_juridicos:
        meta["assuntos_juridicos"] = config.assuntos_juridicos
    if config.assunto_tree_ids_ref:
        meta["assunto_tree_ids_ref"] = config.assunto_tree_ids_ref
    if config.pesquisa_terms:
        meta["pesquisa_terms"] = config.pesquisa_terms
    return meta


if __name__ == "__main__":
    from config.paths import COLLECT_ROOT
    from scrapers.recollect_config import build_folder_name, load_recollect_config

    config = load_recollect_config(
        Path(__file__).resolve().parents[2]
        / "scripts/data/recollect_configs/round1_pesquisa_livre_pge.json"
    )
    date_range = ("01/07/2025", "31/12/2025")
    folder_name = build_folder_name(config.folder_prefix, date_range)
    base_output_dir = str(COLLECT_ROOT / folder_name)

    print("Config:", config.id)
    print("Folder:", base_output_dir)
    print("Started at", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
    main(
        BASE_OUTPUT_DIR=base_output_dir,
        collection_mode=config.mode,
        tribunal=config.tribunal,
        date_range=date_range,
        classes=config.classes,
        max_workers=config.max_workers,
        pesquisa_terms=config.pesquisa_terms,
        campaign_subdir=config.campaign_subdir,
        assuntos_juridicos=config.assuntos_juridicos,
        config_meta=config_meta_from_recollect(config),
    )
    print("Finished at", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
