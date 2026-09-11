import os
import json
import logging
from datetime import datetime
from pathlib import Path
import sqlite3
import pandas as pd
from bs4 import BeautifulSoup
import zipfile

from scrapers.comunica_pje import ComunicaPJE
from utils.settings import Settings
from utils.messenger import Messenger
from utils.db import S3
    
class ScraperComunicaFull:
    def __init__(self):
        self.settings = Settings()
        self.logger = logging.getLogger(f"{__name__}.ScraperComunicaFull")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.assuntos_dict = { 
            # Termos SUS
            'general' : [
                "sus", "sistema único de saúde", "sistema unico de saude",
                "atenção básica", "rename", "relação nacional de medicamentos essenciais",
                "conitec",  "hospital",
                "consulta", "exame", "cirurgia", "internação", "internacao",
                "diagnóstico", "diagnostico",
                "prescrição médica", "receita médica",
                "laudo médico", "atestado médico", "prontuário",
                "evolução clínica",
                "alta médica", "alta hospitalar",
                "plano de saúde", "convênio médico",
                "operadora de saúde", "seguradora de saúde",
                "unimed", "amil", "bradesco saúde", "sulamerica",
                "intermédica", "golden cross", "notre dame",
                "cobertura assistencial", "rol de procedimentos",
                "coparticipação","medicamento", "remédio", "fármaco",
                "princípio ativo",
                "genérico", "similar", "referência",
                "posologia", "bula", "contraindicação",
                "medicamento de alto custo", "medicamento especializado",
                "medicamento judicial", "medicamento experimental",
                "órtese", "prótese","doença", "enfermidade", "patologia",
                "condição de saúde",
                "urgência", "emergência",
                "risco de morte", "risco à vida", "risco de vida",
                "cid", "classificação internacional de doenças",
                "sintoma", "quadro clínico", "quimioterapia", "radioterapia", "oncológico", "oncologia",
                "câncer", "neoplasia", "tumor", "metástase"
                ],
            # Remédios Alto Custo RAY
            'ray' : [
                "Abevmy", "Bevacizumabe", "Acetato de Abiraterona", "Abiraterona", "Aclasta", 
                "Ácido Zoledrônico", "Actemra", "Tocilizumabe", "Actemra SC", 
                "Actilyse", "Alteplase", "Adcetris", "Brentuximabe Vedotina", 
                "Adempas", "Riociguate", "Afinitor", "Everolimo", "Agrastat", 
                "Cloridrato de Tirofibana", "Agrylin", "Cloridrato de Anagrelida",
                "Ajovy", "Fremanezumabe", "Aldurazyme", "Laronidase", "Alecensa",
                "Cloridrato de Alectinibe", "Alimta", "Pemetrexede Dissódico", 
                "AmBisome", "Anfotericina B", "Amgevita", "Adalimumabe", "Atred", 
                "Aubagio", "Teriflunomida", "Austedo", "Deutetrabenazine", "Avastin",
                "Avonex", "Betainterferona 1A", "Avsola", "Infliximabe", "Azacitidina",
                "Balefio", "Bavencio", "Avelumabe", "Beleodaq", "Belinostate",
                "Benlysta", "Belimumabe", "Berinert", "Inibidor de C1 Esterase",
                "Besponsa", "Inotuzumab Ozogamicina", "Blincyto", "Blinatumomabe", 
                "Bortezomibe", "Bosulif", "Bosutinibe", "Braftovi", "Encorafenibe",
                "Brukinsa", "Zanubrutinibe", "Bylvay", "Odevixibate", "Cabazitaxel",
                "Cabometyx", "Levomalato de Cabozantine", "Calquence", 
                "Acalabrutinibe", "Camzyos", "Mavacanteno", "Caprelsa", "Vandetanibe",
                "Carbaglu", "Ácido Carglúmico", "Cardioxane", 
                "Cloridrato de Dexrazoxano", "Certican", "Cibinqo", "Abrocitinibe", 
                "Cimzia", "Certolizumabe Pegol", "Cloridrato de Valganciclovir",
                "Copaxone", "Acetato de Glatirâmer", "Cosentyx", "Secuquinumabe",
                "Cotellic", "Hemifumarato de Cobimetinibe", "Cuprimine", 
                "Penicilamina", "Cyramza", "Ramucirumabe", "Dacogen", "Decitabina",
                "Dalinvi", "Daratumumabe", "Dalinvi SC", "Danyelza", "Naxitamabe",
                "Dasatinibe", "Dupixent", "Dupilumabe", "Egurinel", "Pirfenidona",
                "Eligard", "Acetato de Leuprorrelina", "Elonva", "Alfacorifolitropina",
                "Emgality", "Galcanezumabe", "Enbrel", "Etanercepte", "Enbrel PFS",
                "Enhertu", "Trastuzumabe Deruxtecana", "Enspryng", "Satralizumabe", 
                "Entyvio", "Vedolizumabe", "Epclusa", "Velpatasvir", "Sofosbuvir", 
                "Epkinly", "Epcoritamabe", "Erbitux", "Cetuximabe", "Erelzi", 
                "Erfandel", "Erdafitinibe", "Erivedge", "Vismodegibe", "Erleada",
                "Apalutamida", "Esbriet", "Esilato de Nintedanibe", "Evenity",
                "Romosozumabe", "Evobrig", "Brigatinibe", "Evrysdi", "Risdiplam",
                "Eylia", "Aflibercepte", "Fabrazyme", "Betagalsidase", "Fasenra",
                "Benralizumabe", "Faslodex", "Fulvestranto", "Ferriprox", 
                "Deferiprona", "Firazyr", "Acetato de Icatibanto", "Firmagon",
                "Acetato de Degarelix", "Fludalibbs", "Fosfato de Fludarabina",
                "Fortéo", "Teriparatida", "Gazyva", "Obinutuzumabe", "Gilenya", 
                "Cloridrato de Fingolimode", "Giotrif", "Dimaleato de Afatinibe",
                "Givlaari", "Givosirana Sódica", "Glivec", "Mesilato de Imatinibe",
                "Halaven", "Mesilato de Eribulina", "Harvoni", "Ledipasvir", 
                "Sofosbuvir", "Hemcibra", "Emicizumabe", "Herceptin", "Trastuzumabe",
                "Herceptin SC", "Herzuma", "Hizentra", "Imunoglobulina Humana", 
                "Humira AC", "Hyrimoz", "Ibrance", "Palbociclibe", "Iclusig",
                "Cloridrato de Ponatinibe", "Idacio", "Ilaris", "Canaquinumabe",
                "Imbruvica", "Ibrutinibe", "Imfinzi", "Durvalumabe", "Imjudo", 
                "Tremelimumabe", "Imunoglobulin", "Imunoglobulina", "Inlyta",
                "Axitinibe", "Invega Sustenna", "Paliperidona", "Iressa", "Gefitinibe",
                "Jakavi", "Ruxolitinibe", "Jaypirce", "Pirtobrutinibe", "Jemperli", 
                "Dostarlimab", "Jevtana", "Kadcyla", "Trastuzumabe Entansina",
                "Kalydeco", "Ivacaftor", "Kalyme", "Tigeciclina", "Kanjinti", 
                "Kesimpta", "Ofatumumabe", "Keytruda", "Pembrolizumabe", "Kiendra",
                "Ácido Fumárico Siponimide", "Kisqali", "Succinato de Ribociclibe",
                "Koselugo", "Sulfato de Selumetinibe", "Kuvan",
                "Dicloridrato de Sapropterina", "Kyprolis", "Carfilzomibe", "Lemtrada",
                "Alentuzumabe", "Lenangio", "Lenalidomida", "Lenvima",
                "Mesilato de Lenvatinibe", "Leustatin", "Cladribina", "Libtayo",
                "Cemiplimabe", "Lisodren", "Mitotano", "Litfulo","Imunoglobin",
                "Tosilato de Ritlecitinibe", "Livtencity", "Maribavir", "Lokelma", 
                "Ciclossilicato de Zircônio Sódico", "Lonsurf", 
                "Cloridrato de Tipiracila", "Trifluridina", "Lorbrena", "Lorlatinibe",
                "Lucentis", "Ranibizumabe", "Lumakras", "Sotorasibe", "Lynparza",
                "Olaparibe", "MabThera", "Rituximabe", "MabThera SC", "Matiz", 
                "Mavenclad", "Maviret", "Pibrentasvir + Glecaprevir", "Mekinist", 
                "Dimetilsulfóxido de Trametinibe", "Mektovi", "Binimetinibe", 
                "Metalyse", "Tenecteplase", "Mozobil", "Plerixafor", "Mvasi",
                "Myfortic", "Micofenolato de Sódio", "Mylotarg", 
                "Gentuzumabe Ozogamicina", "Myozyme", "Alfaglicosidase", 
                "Neo Decapeptyl LP", "Acetato de Triptorrelina", "Nepexto", "Nexavar",
                "Tosilato de Sorafenibe", "Nexviazyme", "Avalglucosidase Alfa", 
                "Nidhi", "Ninlaro", "Citrato de Ixazomibe", "Noxafil", "Posaconazol",
                "Nplate", "Romiplostim", "Nubeqa", "Darolutamida", "Nucala",
                "Mepolizumabe", "Ocrevus", "Ocrelizumabe", "Ofev", "Olumiant", 
                "Baricitinibe", "Ontruzant", "Opdivo", "Nivolumabe", "Orkambi", 
                "Lumacaftor", "Ivacaftor", "Ozurdex", "Dexametasona", "Panhematin",
                "Hemina", "Pasurta", "Erenumabe", "Pegasys", "Alfapeginterferona", 
                "Perjeta", "Pertuzumabe", "Piqray", "Alpelisibe", "Pomalyst",
                "Pomalidomida", "Praluent", "Alirocumabe", "Privymtra", "Letermovir",
                "Procysbi", "Bitartarato de Cisteamina", "Prograf", "Tacrolimo",
                "Qarziba", "Betadinutuximabe", "Rapamune", "Sirolimo", "Rarija",
                "Reblozyl", "Luspatercepte", "Remicade", "Remsima", "Renagel",
                "Cloridrato de Sevelâmer", "Repatha", "Evolocumabe", "Replagal",
                "Alfagalsidase", "Revolade", "Eltrombopague Olamina", "Ribomustin",
                "Bendamustina", "Rinvoq", "Upadacitinibe", "Riximyo", "RoPolivy",
                "Polatuzumabe Vedotina", "Ruxience", "Rybrevant", "Amivantamabe",
                "Rydapt", "Midostaurina", "Sandoglobulina Privigen", "Sandostatin",
                "Octreotida", "Sandostatin LAR", "Saphnelo", "Anifrolumabe", 
                "Scemblix", "Cloridrato de Asciminibe", "Simponi", "Golimumabe",
                "Simulect", "Basiliximabe", "Skyrizi", "Risanquizumabe",
                "Somatuline Autogel", "Acetato de Lanreotida", "Somavert",
                "Pegvisomanto", "Sovaldi", "Sofosbuvir", "Spravato",
                "Cloridrato de Escetamina", "Sprycel", "Stelara", "Ustequinumabe", 
                "Stivarga", "Regorafenibe", "Sutent", "Malato de Sunitinibe", "Suzopa",
                "Sybrava", "Inclisirana", "Sylvant", "Siltuximabe", "Synagis",
                "Palivizumabe", "Tabrecta", "Capmatinibe", "Tafinlar",
                "Mesilato de Dabrafenibe", "Tagrisso", "Mesilato de Osimertinibe",
                "Takhzyro", "Lanadelumabe", "Taltz", "Ixequizumabe", "Talvey",
                "Talquetamabe", "Tarceva", "Cloridrato de Erlotinibe", "Tasigna",
                "Nilotinibe", "Tecentriq", "Atezolizumabe", "Tecfidera",
                "Fumarato de Dimetila", "Tecvayli", "Teclistamabe", "Tegsedi",
                "Inotersena", "Temodal", "Temozolomida", "Tezspire", "Tezepelumabe",
                "Thyrogen", "Alfatirotropina", "Tobramicina", "Torgena",
                "Ceftazidima Pentahidratada", "Avibactam Sódico", "Torhanz",
                "Trazimera", "Tremfya", "Guselcumabe", "Trisenox", 
                "Trióxido de Arsênio", "Trodelvy", "Sacituzumabe Govitecana", "Truqap",
                "Capivasertibe", "Truxima", "Tykerb", "Ditosilato de Lapatinibe", 
                "Tysabri", "Natalizumabe", "Upelior", "Diaspartato de Pasireotida", 
                "Uptravi", "Selexipague", "Vabysmo", "Faricimabe", "Valcyte", 
                "Vectibix", "Panitumumabe", "Velcade", "Vemlidy", 
                "Hemifumarato de Tenofovir Alafenamida", "Venclexta", "Venetoclax", 
                "Verzenios", "Abemaciclibe", "Vfend", "Voriconazol", "Vidaza",
                "Vitrakvi", "Larotrectinibe", "Volibris", "Ambrisentana", "Votrient",
                "Cloridrato de Pazopanibe", "VOXZOGO", "Vosoritida", "Vyndaqel",
                "Tafamidis Meglumina", "Welireg", "Belzutifano", "Xalkori",
                "Crizotinibe", "Xeljanz", "Citrato de Tofacitinibe", "Xeloda",
                "Capecitabina", "Xenpozyme", "Alfaolipudase", "Xgeva", "Denosumabe",
                "Xolair", "Omalizumabe", "Xtandi", "Enzalutamida", "Yervoy", 
                "Ipilimumabe", "Zavesca", "Miglustate", "Zedora", "Zejula",
                "Tosilato de Niraparibe", "Zelboraf", "Vemurafenibe", "Zinforo",
                "Ceftarolina Fosamila", "Zoladex LA", "Acetato de Gosserrelina", 
                "Zostide", "Zytiga", "Peginterferona alfa-2a", "Tiotepa", "Amifampridina",
                "Nintendanibe", "Invanz", "Xospata", "Soliris", "brentuximab vedotin",
                "teriparatide", "canakinumab", "gilteritinib", "enzalutamide",
                "ustekinumabe", "ertapenem", "gilteritinibe", "tipiracilo",
                "dinutuximabe beta", "eculizumabe", "alfaepoetina", "acetato de glatiramer",
                "maleato de acalabrutinibe", "maleato de neratinibe", 
                "somatropina", "palivizumab", "dasabuvir sodico ", 
                "hemifumarato de gilteritinibe", "abatacepte", "ivosidenibe", 
                "alfafolitropina", "entecavir", "lopinavir", "sulfato de isavuconazonio",
                "somatrogona", "encorafenib", "tepotinibe", 
                "sirolimus", "dolutegravir", "nivolumab", "pazopanibe", "alpesilibe", 
                "cinacalcete", "leuprorrelina", "asciminib", "canabidiol", 
                "deltafolitropina", "capmatinibe ", 
                "abiraterona biosimilar", "meglumina, tafamidis", "linezolida", 
                "peginterferona alfa", "ciclosporina", "infliximab", "erdafinitibe", 
                "fampridina", "tipiracila", "glecaprevir hidratado", "deferasirox", 
                "ofatumumab", "diaspartato de pasireotida", "levomalato de cabozantinibe", 
                "pirtobrutinib", "erlotinibe", "triptorrelina", "raltegravir", 
                "ridisplam", "simeprevir", "daclatasvir", "valganciclovir", "menotropina", 
                "ruxolitinibe", "ganciclovir", "teduglutida", "dabrafenib", 
                "ruxolitinib", "filgrastim", "tosilato de niraparibe", 
                "alfapeginterferona 2b", "ledispavir", "tretinoina", 
                "lapatinibe", "ramucirumab", "palmitato de paliperidona", "eltrombopag", 
                "bosentana", "veruprevir di-hidratado", "adefovir dipivoxila", "ritonavir",
                "upacacitinibe", "ombitasvir hidratado", "crizotinib", 
                "alfaeptacogue ativado", "acetato de octreotida", "selpercatinibe",
                "Herzuma", "Remsima", "Remsima SC", "Truxima", "Yuflyma", "Vegzelma",
                "BRUKINSA (zanubrutinibe)", "zanubrutinibe", "BRUKINSA", "Berinert 500ui",
                "Berinert", "Spravato 28mg Spray Nasal (Escetamina)", "Spravato", "Escetamina"
                ],
            # Remédios RENAME
            'rename' : [
                "ABACAVIR", "ABATACEPTE", "ABCIXIMABE", "ABEMACICLIBE", "ABIRATERONA", 
                "ACALABRUTINIBE", "ACETAZOLAMIDA", "ACETILCEFUROXIMA", "ACICLOVIR",
                "ACIDO ACETILSALICILICO", "ACIDO FOLICO", "ACIDO FOLINICO", "ACIDO HIALURONICO",
                "ACIDO NICOTINICO", "ACIDO PARAMINOSSALICILICO", "ACIDO SALICILICO", "ACIDO TIOCTICO",
                "ACIDO TRANEXAMICO", "ACIDO URSODESOXICOLICO", "ACIDO VALPROICO", "ACIDO ZOLEDRONICO",
                "ACITRETINA", "ADALIMUMABE", "ADRENALINA", "AFATINIBE", "AFLIBERCEPTE", "AGUA PARA INJETAVEIS",
                "ALBENDAZOL", "ALBUMINA", "ALCACHOFRA", "ALDESLEUCINA", "ALECTINIBE", "ALENDRONATO",
                "ALENTUZUMABE", "ALFA-ALGLICOSIDASE", "ALFA-ASFOTASE", "ALFACALCIDOL", "ALFADORNASE",
                "ALFAELOSULFASE", "ALFAEPOETINA", "ALFAINTERFERONA", "ALFAPEGINTERFERONA 2A",
                "ALFAPORACTANTO", "ALFATALIGLICERASE", "ALIROCUMABE", "ALOPURINOL", "ALPRAZOLAM",
                "ALTEPLASE", "AMANTADINA", "AMBRISENTANA", "AMICACINA", "AMIODARONA", "AMISSULPRIDA",
                "AMITRIPTILINA", "AMOXICILINA", "AMOXICILINA + CLAVULANATO DE POTASSIO", "ANASTROZOL",
                "ANFOTERICINA B", "ANLODIPINO", "APIXABANA", "ARIPIPRAZOL", "ARTEMISINA", "ASPARAGINASE",
                "ATAZANAVIR", "ATENOLOL", "ATEZOLIZUMABE", "ATORVASTATINA", "ATROPINA", "AXITINIBE",
                "AZACITIDINA", "AZATIOPRINA", "AZITROMICINA", "BASILIXIMABE", "BCG", "BECLOMETASONA",
                "BELIMUMABE", "BENDAMUSTINA", "BENRALIZUMABE", "BENSERAZIDA", "BENZILPENICILINA",
                "BENZOILA", "BENZONIDAZOL", "BERACTANTO", "BETA INTERFERONA", "BETAGALSIDASE",
                "BETAINTERFERONA 1A", "BETAINTERFERONA 1B", "BETAMETASONA", "BEVACIZUMABE", "BEZAFIBRATO",
                "BICALUTAMIDA", "BICARBONATO", "BIMATOPROSTA", "BIPERIDENO", "BLEOMICINA", "BLINATUMOMABE",
                "BORTEZOMIBE", "BOSENTANA", "BRENTUXIMABE", "BRIMONIDINA", "BRINZOLAMIDA", "BROMOCRIPTINA",
                "BROMOPRIDA", "BUDESONIDA", "BUPIVACAINA", "BUPRENORFINA", "BUPROPIONA", "BUSPIRONA",
                "BUSSULFANO", "CABAZITAXEL", "CABERGOLINA", "CABOZANTINIBE", "CALCIPOTRIOL", "CALCITONINA",
                "CALCITRIOL", "CANABIDIOL", "CANAQUINUMABE", "CAPECITABINA", "CAPREOMICINA", "CAPTOPRIL",
                "CARBAMAZEPINA", "CARBIDOPA", "CARBONATO DE CALCIO", "CARBONATO DE CALCIO + COLECALCIFEROL",
                "CARBONATO DE LITIO", "CARBOPLATINA", "CARFILZOMIBE", "CARMUSTINA", "CARVEDILOL",
                "CASCARA SAGRADA", "CEFALEXINA", "CEFOTAXIMA", "CEFTRIAXONA", "CEMIPLIMABE", "CERTOLIZUMABE",
                "CETOCONAZOL", "CETUXIMABE", "CIANOCOBALAMINA", "CICLOFOSFAMIDA", "CICLOSERINA",
                "CICLOSPORINA", "CIDOFOVIR", "CILOSTAZOL", "CINACALCETE", "CIPROFIBRATO", "CIPROFLOXACINO",
                "CIPROTERONA", "CISPLATINA", "CITALOPRAM", "CITARABINA", "CITRATO DE POTASSIO", "CLADRIBINA",
                "CLARITROMICINA", "CLAVULANATO DE POTASSIO", "CLINDAMICINA", "CLOBAZAM", "CLOBETASOL", "CLOFAZIMINA",
                "CLOMIPRAMINA", "CLONAZEPAM", "CLOPIDOGREL", "CLORAMBUCILA", "CLORANFENICOL", "CLORETO DE POTASSIO",
                "CLORETO DE SODIO", "CLOREXIDINA", "CLOROQUINA", "CLORPROMAZINA", "CLORTALIDONA", "CLOZAPINA",
                "COBALAMINA", "COBIMETINIBE", "CODEINA", "COLCHICINA", "COLECALCIFEROL", "COMPLEXO PROTROMBINICO",
                "CONDROITINA", "CRIZOTINIBE", "CUMARINA", "DABIGATRANA", "DABRAFENIBE", "DACARBAZINA", "DACLATASVIR",
                "DACLIZUMABE", "DAGIGRATANA", "DALTEPARINA", "DANOCRINA", "DAPAGLIFLOZINA", "DAPSONA", "DAPTOMICINA",
                "DARATUMUMABE", "DARUNAVIR", "DASATINIBE", "DAUNORRUBICINA", "DECITABINA", "DEFERASIROX", "DEFERIPRONA",
                "DEGARELIX", "DENOSUMABE", "DESFERROXAMINA", "DESLORATADINA", "DESMOPRESSINA", "DESVENLAFAXINA",
                "DEXAMETASONA", "DEXCLORFENIRAMINA", "DEXRAZOSANO", "DIAZEPAM", "DIDANOSINA",
                "DIETILCARBAMAZINA", "DIETILESTILBESTROL", "DIGOXINA", "DILTIAZEN", "DIPIRONA", "DOBUTAMINA",
                "DOCETAXEL", "DOLUTEGRAVIR", "DONEPEZILA", "DOPAMINA", "DORZOLAMIDA", "DOXAZOSINA", "DOXICICLINA",
                "DOXORRUBICINA", "DULAGLUTIDA", "DULOXETINA", "DUPILUMABE", "DURVALUMABE", "DUTASTERIDA",
                "ECULIZUMABE", "EDOXABANA", "EFAVIRENZ", "ELTROMBOPAGUE", "EMPAGLIFLOZINA", "ENALAPRIL", "ENFUVIRTIDA",
                "ENOXAPARINA", "ENTACAPONA", "ENTECAVIR", "ENTRICITABINA", "ENZALUTAMIDA", "EPINEFRINA", "EPIRRUBICINA",
                "EPLERENONA", "ERENUMABE", "ERIBULINA", "ERITROMICINA", "ERITROPOETINA", "ERLOTINIBE", "ESCITALOPRAM",
                "ESOMEPRAZOL", "ESPIRAMICINA", "ESPIRONOLACTONA", "ESTAVUDINA", "ESTRADIOL", "ESTREPTOMICINA",
                "ESTREPTOQUINASE", "ESTRIOL", "ESTROGENIOS CONJUGADOS", "ETAMBUTOL", "ETANERCEPTE", "ETINILESTRADIOL",
                "ETINILESTRADIOL + LEVONORGESTREL", "ETIONAMIDA", "ETOFIBRATO", "ETOPOSIDEO", "ETOSSUXIMIDA", "ETRAVIRINA",
                "EVEROLIMO", "EVOLOCUMABE", "EXEMESTANO", "FAMPRIDINA", "FATOR COAGULACAO VII", "FATOR IX DE COAGULACAO",
                "FATOR VII DE COAGULACAO ATIVADO RECOMBINANTE", "FATOR VIII DE COAGULACAO", "FATOR VIII DE COAGULACAO CONTENDO FATOR VON WILLEBRAND",
                "FATOR XIII DE COAGULACAO", "FENITOINA", "FENOBARBITAL", "FENOFIBRATO", "FENOTEROL", "FENOXIMETILPENICILINA POTASSICA",
                "FENTANILA", "FIBRINOGENIO", "FILGASTRIM", "FILGRASTIM", "FINASTERIDA", "FINGOLIMODE", "FLUCONAZOL", "FLUDARABINA",
                "FLUDROCORTISONA", "FLUMAZENIL", "FLUOCINOLONA", "FLUORURACILA", "FLUOXETINA", "FLUTAMIDA", "FLUTICASONA", "FLUVASTATINA", "FLUVOXAMINA", "FOLINATO CALCIO",
                "FORMOTEROL", "FORMOTEROL + BUDESONIDA", "FOSAMPRENAVIR", "FOSFOETANOLAMINA", "FOTEMUSTINA", "FULVESTRANTO", "FUMARATO DE DIMETILA", "FUROSEMIDA",
                "GABAPENTINA", "GALANTAMINA", "GALSULFASE", "GEFITINIBE", "GENCITABINA", "GENFIBROZILA", "GENTAMICINA", "GLATIRAMER", "GLIBENCLAMIDA", "GLICEROL", "GLICINATO FERRICO", "GLICOPIRRONIO", "GLICOSAMINA",
                "GLICOSE", "GOLIMUMABE", "GONADOTROFINA", "GOSSERRELINA", "GUACO", "HALOPERIDOL", "HEMINA", "HEPARINA SODICA", "HIDRALAZINA", "HIDROCLOROTIAZIDA", "HIDROCORTISONA", "HIDROXICLOROQUINA", "HIDROXIDO DE ALUMINIO", "HIDROXIUREIA", "HIPROMELOSE", "IBRANDONATO",
                "IBRUTINIBE", "IBUPROFENO", "ICATIBANTO", "IDARRUBICINA", "IDEBENONA", "IFOSFAMIDA", "ILOPROSTA", "IMATINIBE", "IMIGLUCERASE", "IMIQUIMODE", "IMUNOGLOBULINA", "IMUNOGLOBULINA ANTI-RHO", "IMUNOGLOBULINA ANTITETANICA", "IMUNOGLOBULINA ANTITIMOCITOS", "IMUNOGLOBULINA ANTIVARICELA ZOSTER", "IMUNOGLOBULINA HUMANA ANTI-HEPATITE B", "IMUNOGLOBULINA HUMANA ANTIRRABICA", "INFLIXIMABE", "INSULINA", "INSULINA ANALOGA DE ACAO PROLONGADA", "INSULINA ANALOGA DE ACAO RAPIDA", "INSULINA HUMANA NPH", "INTERFERON", "INTERLEUCINA", "IPILIMUMABE", "IPRATROPIO", "IRINOTECANO", "ISOFLAVONA", "ISOLEUCINA", "ISONIAZIDA", "ISOSSORBIDA", "ISOTRETINOI", "ITRACONAZOL", "IVABRADINA", "IVERMECTINA", "IXABEPILONE", "IXAZOMIBE", "LACOSAMIDA", "LACTULOSE", "LAMIVUDINA", "LAMOTRIGINA", "LANREOTIDA", "LAPATINIBE", "LATANOPROSTA", "LEDISPASVIR", "LEFLUNOMIDA", "LENALIDOMIDA", "LENVATINIBE", "LERCANIDIPINO", "LETROZOL", "LEUPRORRELINA", "LEVANLODIPINO", "LEVETIRACETAM", "LEVODOPA", "LEVODOPA + BENSERAZIDA", "LEVODOPA + CARBIDOPA", "LEVODOPA + CARBIDOPA + ENTACAPONA", "LEVOFLOXACINO", "LEVONORGESTREL", "LEVOTIROXINA", "LIDOCAINA", "LINAGLIPTINA", "LINEZOLIDA", "LIPEGFILGRASTIM", "LIRAGLUTIDA", "LISDEXANFETAMINA", "LITIO", "LOMUSTINA", "LOPINAVIR", "LOPINAVIR + RITONAVIR", "LORATADINA", "LOSARTANA", "LOVASTATINA", "LUMEFANTRINA", "MAGNESIO", "MANITOL", "MEDICAMENTO NAO ESPECIFICADO", "MEDROXIPROGESTERONA", "MEFLOQUINA",
                "MEGESTROL", "MEGLUMINA", "MELFALANO", "MEMANTINA", "MEPOLIZUMABE", "MERCAPTOPURINA", "MEROPENEM", "MESALAZINA", "METADONA", "METFORMINA", "METILDOPA", "METILFENIDATO", "METILPREDNISOLONA", "METOCLOPRAMIDA", "METOPROLOL", "METOTREXATO", "METRONIDAZOL", "METRONIDAZOL + BENZOILMETRONIDAZOL", "METROPROLOL", "MEZALAZINA", "MICOFENOLATO DE MOFETILA", "MICOFENOLATO DE SÓDIO", "MICONAZOL", "MIDAZOLAM", "MIGLUSTATE", "MILTEFOSINA", "MINOCICLINA", "MIRABEGRONA", "MIRTAZAPINA", "MISOPROSTOL", "MITOMICINA", "MITOTANO", "MITOXANTRONA", "MORFINA", "MOXIFLOXACINO",
                "NALOXONA", "NALTREXONA", "NAPROXENO", "NATALIZUMABE", "NEOMICINA", "NEOMICINA + BACITRACINA", "NEVIRAPINA", "NICOTINA", "NIFEDIPINO", "NILOTINIBE", "NIMOTUZUMABE", "NINTEDANIBE", "NISTATINA", "NITROFURANTOINA", "NIVOLUMABE", "NOREPINEFRINA", "NORERISTERONA + ESTRADIOL", "NORETISTERONA", "NORITISTERONA + ESTRADIOL", "NORTRIPTILINA", "NUSINERSENA", "OBINUTUZUMABE", "OCRELIZUMABE", "OCTREOTIDA", "OFATUMUMABE", "OFLOXACINO", "OLAMINA", "OLANZAPINA", "OLAPARIBE", "OLARATUMABE",
                "OMALIZUMABE", "OMBISTAVIR", "OMEPRAZOL", "ONDANSETRONA", "ORLISTATE", "ORNITINA",
                "OSELTAMIVIR", "OSIMERTINIBE", "OXALIPLATINA", "OXAMNIQUINA", "OXCARBAZEPINA", "OXIBUTININA", "OXICODONA", "OXIDO FERRICO", "OXILIPLATINA", "PACLITAXEL", "PALBOCICLIBE", "PALIPERIDONA", "PALIVIZUMABE", "PAMIDRONATO", "PANCREATINA", "PANITUMUMABE", "PANTOPRAZOL", "PARACETAMOL",
                "PARICALCITOL", "PAROXETINA", "PAZOPANIBE", "PEGASPARGASE", "PEGFILGRASTIM", "PEGVISOMANTO",
                "PEMBROLIZUMABE", "PEMETREXEDE", "PENICILAMINA", "PENICILINA", "PENTAMIDINA", "PENTOXIFILINA",
                "PERICIAZINA", "PERINDOPRIL", "PERMETREXE", "PERMETRINA", "PEROXIDO DE BENZOILA", "PERTUZUMABE",
                "PIFERNIDONA", "PILOCARPINA", "PINUS PINASTER", "PIOGLITAZONA", "PIRAZINAMIDA", "PIRFENIDONA",
                "PIRIDOSTIGMINA", "PIRIDOXINA", "PIRIMETAMINA", "PIROXICAM", "PLANTAGO", "PLERIXAFOR",
                "PODOFILINA", "PODOFILOTOXINA", "POLIMIXINA B", "POLIMIXINA B + NEOMICINA + FLUOCINOLONA ACETONIDA + LIDOCAINA",
                "POTASSIO", "PRALIDOXIMA", "PRAMIPEXOL", "PRASUGREL", "PRAVASTATINA SODICA", "PRAZIQUANTEL", "PREDNISOLONA",
                "PREGABALINA", "PREMBOLIZUMABE", "PRIMAQUINA", "PRIMIDONA", "PROCARBAZINA", "PROMETAZINA", "PROPAFENONA", "PROPILTIOURACILA", "PROPRANOLOL", "PROTAMINA", "PRUCALOPRIDA", "QUETIAPINA",
                "QUININA", "RALOXIFENO", "RALTEGRAVIR POTASSICO", "RAMUCIRUMABE", "RANIBIZUMABE", "RANITIDINA", "RAPAMICINA", "RASAGILINA", "RASALIGINA", "REGORAFENIBE", "RESPIRIDONA", "RETINOL",
                "RIBAVIRINA", "RIBOCICLIBE", "RIFAMICINA", "RIFAMPICINA", "RIFAMPICINA + ISONIAZIDA", "RIFAMPICINA + ISONIAZIDA + PIRAZINAMIDA", "RIFAMPICINA + ISONIAZIDA + PIRAZINAMIDA +  CLORIDRATO DE ETAMBUTOL",
                "RILUZOL", "RIOCIGUATE", "RISANQUIZUMABE", "RISEDRONATO", "RISPERIDONA", "RITONAVIR", "RITUXIMABE", "RIVAROXABANA", "RIVASTIGMINA", "RUFINAMIDA", "RUXOLITINIBE", "SACUBITRIL VALSARTANA SODICA HIDRATADA", "SALBUTAMOL", "SALMETEROL", "SALMETEROL + FLUTICASONA", "SAPROPTERINA", "SAQUINAVIR", "SECUQUINUMABE", "SELEGILINA", "SERTRALINA", "SEVELAMER", "SILDENAFILA", "SILTUXIMABE", "SINITINIBE", "SINVASTATINA", "SIROLIMO", "SITAGLIPTINA", "SOFOSBUVIR", "SOMATROPINA", "SORAFENIBE", "SORO ANTICROTA", "SORO ANTIESCORPIANICO", "SORO ANTILONOMICO", "SORO ANTITETANICO", "SULFADIAZINA", "SULFAMETOXAZOL + TRIMETOPRIMA", "SULFASSALAZINA", "SULFATO FERROSO", "SULFATO MAGNESIO", "SULFONILUREIA", "SULPIRIDA", "SUNITINIBE", "SYSTANE", "T4", "TACROLIMO", "TADAFILA",
                "TAFAMIDIS", "TALIDOMIDA", "TAMOXIFENO", "TASONERMINA", "TECLOZANA", "TELMISARTANA", "TEMOZOLOMIDA", "TENECTEPLASE", "TENIPOSIDEO", "TENOFOVIR", "TENOFOVIR DESOPROXILA", "TENOFOVIR DESOPROXILA + LAMIVUDINA", "TENOFOVIR DESOPROXILA + LAMIVUDINA + EFAVIRENZ", "TENSIROLIMO", "TERIFLUNOMIDA", "TERIPARATIDA", "TESTOSTERONA", "TETRACICLINA", "TIAMINA", "TICAGRELOR", "TIMOGLOBULINA", "TIMOLOL", "TIMOMODULINA", "TIOGUANINA",
                "TIORIDAZINA", "TIOTROPIO", "TIOTROPIO + OLODATEROL", "TIPRANAVIR", "TIREOTROFINA", "TIROFIBANA", "TOBRAMICINA", "TOCILIZUMABE", "TOFACITINIBE", "TOLCAPONA", "TOPIRAMATO", "TOPOTECANO",
                "TOXINA BOTULINICA A",  "TRABECTEDINA", "TRAMADOL", "TRAMETINIBE", "TRASTUZUMABE", "TRAVOPROSTA", "TRAZODONA", "TRETINOINA", "TRIEXIFENIDIL", "TRIMETAZIDINA", "TRIMETOPRIMA", "TRIOXIDO DE ARSENIO",
                "TRIPTORRELINA", "TROMBOPOIETINA", "USTEQUINUMABE", "VACINA SARAMPO, CAXUMBA, RUBEOLA", "VALACICLOVIR", "VALGANCICLOVIR", "VALPROATO", "VALSARTANA", "VANDETANIBE", "VARFARINA", "VEDOLIZUMABE", "VEMURAFENIBE", "VENETOCLAX", "VENLAFAXINA", "VENURAFENIBE", "VERAPAMIL", "VERMURAGENIBE", "VIGABATRINA", "VILDAGLIPTINA", "VIMBLASTINA", "VINCRISTINA", "VINFLUNINA", "VINORELBINA", "VISMODEGIBE", "VORICONAZOL", "VORTIOXETINA", "ZANAMIVIR", "ZIDOVUDINA", "ZINCO", "ZIPRASIDONA",
                "ZOLPIDEM"
                ],
            # Termos Execução Fiscal
            'exec_fiscal' : ['execução fiscal', 'IPTU', 'ISS', 'taxas municipais',
                        'cobrança judicial', 'dívida ativa', 'tributos municipais',
                        'crédito tributário', 'devedor', 'execução fiscal administrativa',
                        'imposto'
                        ],
            # Termos Trânsito
            'transito' : ['multa de trânsito', 'IPVA', 'taxas de licenciamento', 'taxas de trânsito',
                        'cobrança de multas', 'infrações de trânsito', 'tributos estaduais',
                        'cobrança administrativa', 'débitos veiculares', 'regularização veicular',
                        'cobrança judicial de multas', 'dívida ativa de trânsito', 'penalidades de trânsito',
                        'taxas obrigatórias', 'tributos sobre veículos', 'cobrança de IPVA',
                        'licenciamento anual', 'autuação de trânsito', 'recursos de multas',
                        'cobrança de taxas veiculares'
                    ],
            # Termos TEA
            'tea' : [
                        "autismo",
                        "TEA",
                        "Asperger",
                        "transtorno do espectro autista",
                        'autista',
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
                        "estereotipias autismo"
                    ],
            # Termo Sem Assunto
            'sem_assunto' : None,
            # Custom
            'custom': ['caixa'],
            }
        
        self.messenger = Messenger()

    def configure_logging(self, output_dir):
        """Configure logging to console and file inside output directory."""
        log_dir = os.path.join(output_dir, "metadata", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file_path = os.path.join(log_dir, "scraper.log")

        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

        # Avoid duplicate logs when this method is called more than once.
        if self.logger.handlers:
            self.logger.handlers.clear()

        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(stream_handler)
        self.logger.info(f"Logging initialized. Log file: {log_file_path}")


    def ensure_dir_exists(self, filepath):
        """Create directory if it doesn't exist"""
        directory = os.path.dirname(filepath)
        if directory:
            os.makedirs(directory, exist_ok=True)

    def safe_save_json(self, filepath, data):
        """Safely save JSON data to file, creating directories if needed and avoiding overwriting"""
        try:
            self.ensure_dir_exists(filepath)
            
            # Check if file exists, and if so, append a suffix
            original_filepath = filepath
            counter = 2
            while os.path.exists(filepath):
                # Split the filepath into base and extension
                base, ext = os.path.splitext(original_filepath)
                filepath = f"{base}_{counter}{ext}"
                counter += 1
            
            self.logger.info(f"Saving file to: {filepath}")
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
            
            self.logger.info(f"Successfully saved file: {filepath}")
            
        except Exception as e:
            self.logger.error(f"Error saving file {filepath}: {str(e)}")
        
        # return filepath


    def process(self, assuntos, BASE_OUTPUT_DIR, parte=None, frequency='years', tribunal=None, data_range: tuple = None):
        # Create output directory with current date
        current_date = datetime.now().strftime('%Y%m%d')
        output_dir = os.path.join(BASE_OUTPUT_DIR, current_date)
        self.ensure_dir_exists(output_dir)
        self.configure_logging(output_dir)
        self.messenger.info(f'Processing {assuntos}...')

        # Initialize ComunicaPJE with the output directory
        comunica = ComunicaPJE(output_directory=output_dir)
        if not data_range:
            comunica.dataDisponibilizacaoInicio = '01-01-2025'
            comunica.dataDisponibilizacaoFim = '14-08-2025'
        else:
            comunica.dataDisponibilizacaoInicio = data_range[0]
            comunica.dataDisponibilizacaoFim = data_range[1]

        if assuntos == None:
            assuntos = ['sem_assunto']


        # Track total pages extracted
        total_pages_extracted = 0
        assunto_pages = {}

        for assunto in assuntos:
            assunto_low = assunto.lower()
            self.logger.info(f'Processing {assunto}...')

            if assunto_low == 'sem_assunto':
                assunto = None
            
            try:
                problematic_collection, pages_extracted = comunica.get_tribunal(
                    assunto=assunto,
                    frequency=frequency,
                    nomeParte=parte,
                    sigla_tribunal=tribunal,
                    # num_processo=assunto, 
                    nome_arquivo=assunto_low
                    )


                # Track pages for this assunto
                assunto_pages[assunto] = pages_extracted
                total_pages_extracted += pages_extracted

                self.logger.info(f'Finished processing {assunto}')
                self.logger.info(f'Pages extracted for {assunto}: {pages_extracted}')
                if problematic_collection:
                    self.logger.warning('Errors encountered:')
                    for problem in problematic_collection:
                        self.logger.warning(f"Range {problem.get('data_inicio')} to {problem.get('data_fim')}: {problem.get('error')}")

                    # Save errors in the output directory structure
                    problematic_dir = os.path.join(output_dir, "metadata", "problematic")
                    self.ensure_dir_exists(problematic_dir)

                    # Create a safe filename from the assunto
                    safe_assunto = assunto_low.replace('/', '_').replace(' ', '_')
                    error_filepath = os.path.join(problematic_dir, f'{safe_assunto}_errors.json')
                    self.safe_save_json(error_filepath, problematic_collection)
                    self.logger.info(f'Error log saved to: {error_filepath}')
                    

            except Exception as e:
                self.logger.error(f'Error processing {assunto_low}: {str(e)}')
                # Log the error in the output directory
                error_log_dir = os.path.join(output_dir, "metadata", "errors")
                self.ensure_dir_exists(error_log_dir)

                # Create a safe filename for the error log
                safe_assunto = assunto_low.replace('/', '_').replace(' ', '_')
                error_log_path = os.path.join(error_log_dir, f'{safe_assunto}_critical_error.json')
                self.safe_save_json(error_log_path, {
                    'assunto': assunto,
                    'error': str(e),
                    'timestamp': datetime.now().isoformat()
                })
        try:       
            dedup_file_path = self.deduplicate_and_save_json(output_dir)
            self.messenger.info(f'Deduplicated JSON saved to: {dedup_file_path}')
        except Exception as e:
            self.messenger.error(f'Error deduplicating JSON: {str(e)}')
        
        try:       
            self.convert_json_to_csv(dedup_file_path)
            self.messenger.info(f'CSV saved to: {dedup_file_path}')
        except Exception as e:
            self.messenger.error(f'Error converting JSON to CSV: {str(e)}')
        
        try:       
            self.convert_json_to_db(dedup_file_path)
            self.messenger.info(f'Database saved to: {dedup_file_path}')
        except Exception as e:
            self.messenger.error(f'Error converting JSON to database: {str(e)}')

        # Print summary of pages extracted
        self.logger.info('--- Extraction Summary ---')
        for assunto, pages in assunto_pages.items():
            self.logger.info(f'Pages extracted for {assunto}: {pages}')
        self.logger.info(f'Total pages extracted: {total_pages_extracted}')
        self.logger.info(f'All processing completed. Output saved to: {output_dir}')
        self.logger.info(f'Date Range: {comunica.dataDisponibilizacaoInicio} to {comunica.dataDisponibilizacaoFim}')


    def deduplicate_and_save_json(self, output_dir):
        """
        Deduplicate JSON data from all assunto collections by grouping by numero_processo and save the result.
        
        Args:
            output_dir (str): The output directory containing subdirectories with JSON files from all assuntos
            
        Returns:
            str: Path to the saved deduplicated JSON file
        """
        json_dir_path = Path(output_dir)
        data = []

        self.logger.info("Starting deduplication process...")
        self.logger.info(f"Scanning directory: {json_dir_path}")

        # Walk through each subdirectory, skipping 'metadata'
        files_processed = 0
        for subdir in json_dir_path.iterdir():
            if subdir.is_dir() and subdir.name != 'metadata':
                self.logger.info(f"Processing subdirectory: {subdir.name}")
                for json_file in subdir.glob('*.json'):
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            json_data = json.load(f)
                            items = json_data.get('items', [])
                            data.extend(items)
                            files_processed += 1
                            self.logger.info(f"Loaded {len(items)} items from {json_file.name}")
                    except (json.JSONDecodeError, PermissionError) as e:
                        self.logger.error(f"Error processing {json_file}: {e}")

        self.logger.info(f"Total files processed: {files_processed}")
        self.logger.info(f"Total items loaded before deduplication: {len(data)}")

        # Create DataFrame
        if not data:
            self.logger.warning("No data was loaded. Check the directory path and file contents.")
            return None

        df = pd.DataFrame(data)
        self.logger.info(f"DataFrame created with {len(df)} rows")

        # Group by numero_processo and deduplicate
        self.logger.info("Starting deduplication by numero_processo...")
        proc_df_list = []
        unique_processes = df['numero_processo'].unique()
        self.logger.info(f"Found {len(unique_processes)} unique processes")

        for i, numero_processo in enumerate(unique_processes):
            if i % 100 == 0:  # Progress indicator
                self.logger.info(f"Processing process {i+1}/{len(unique_processes)}")
                
            df_processo = df[df['numero_processo'] == numero_processo]
            
            # Combine all texto entries with separators
            texto_combined = ' | '.join([f"----- {j} ----- | " + str(text) for j, text in enumerate(df_processo['texto']) if pd.notna(text) and str(text).strip()])
            
            proc_df_list.append({
                'numero_processo': numero_processo,
                'texto': texto_combined,
                'siglaTribunal': df_processo['siglaTribunal'].iloc[0],
                'tipoComunicacao': df_processo['tipoComunicacao'].iloc[0],
                'nomeOrgao': df_processo['nomeOrgao'].iloc[0],
                'meio': df_processo['meio'].iloc[0],
                'link': df_processo['link'].tolist(),
                'status': df_processo['status'].iloc[-1],
                'motivo_cancelamento': df_processo['motivo_cancelamento'].iloc[-1],
                'data_cancelamento': df_processo['data_cancelamento'].iloc[-1],
                'data_disponibilizacao': df_processo['data_disponibilizacao'].iloc[-1],
                'tipoDocumento': df_processo['tipoDocumento'].iloc[-1],
                'nomeClasse': df_processo['nomeClasse'].iloc[-1],
                'codigoClasse': df_processo['codigoClasse'].iloc[-1],
                'numeroComunicacao': df_processo['numeroComunicacao'].iloc[-1],
                'ativo': df_processo['ativo'].iloc[-1],
                'hash': df_processo['hash'].iloc[-1],
                'destinatarios': df_processo['destinatarios'].iloc[-1],
                'destinatarioadvogados': df_processo['destinatarioadvogados'].iloc[-1]
            })

        proc_df = pd.DataFrame(proc_df_list)
        self.logger.info(f"Deduplication completed. Reduced from {len(df)} to {len(proc_df)} records")

        # Create the final JSON structure
        json_data = {
            "status": "success",
            "message": "Sucesso - Dados consolidados de todos os assuntos",
            "count": len(proc_df),
            "original_count": len(df),
            "assuntos_processed": files_processed,
            "items": proc_df.to_dict(orient='records')
        }

        def decode_html_entities(text):
            if isinstance(text, str):
                return BeautifulSoup(text, 'html.parser').get_text()
            return text

        # Apply HTML decoding to the data
        self.logger.info("Decoding HTML entities...")
        if isinstance(json_data, dict):
            for key, value in json_data.items():
                if key == 'items' and isinstance(value, list):
                    for item in value:
                        for k, v in item.items():
                            if isinstance(v, str):
                                item[k] = decode_html_entities(v)
                elif isinstance(value, str):
                    json_data[key] = decode_html_entities(value)

        # Save the deduplicated JSON file
        dedup_file_path = os.path.join(json_dir_path, 'process_grouped_all_assuntos.json')
        with open(dedup_file_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        self.logger.info(f"Deduplicated data saved to: {dedup_file_path}")
        self.logger.info("Summary:")
        self.logger.info(f"  - Original items from all assuntos: {len(df)}")
        self.logger.info(f"  - Deduplicated items: {len(proc_df)}")
        self.logger.info(f"  - Files processed: {files_processed}")
        self.logger.info(f"  - Reduction: {len(df) - len(proc_df)} duplicate communications removed")
        
        return dedup_file_path


    def convert_json_to_csv(self, json_file_path):
        """
        Convert the deduplicated JSON file to CSV format.
        
        Args:
            json_file_path (str): Path to the JSON file to convert
            
        Returns:
            str: Path to the saved CSV file
        """
        if not json_file_path:
            self.logger.warning("No JSON file path provided")
            return None
            
        try:
            self.logger.info("Converting JSON to CSV...")
            
            # Load the JSON data
            with open(json_file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            # Extract items from the JSON structure
            items = json_data.get('items', [])
            
            if not items:
                self.logger.warning("No items found in JSON file")
                return None
            
            # Create DataFrame from the items directly
            df = pd.DataFrame(items)
            self.logger.info(f"DataFrame created with shape: {df.shape}")
            self.logger.info(f"Columns: {list(df.columns)}")
            
            # Handle list columns (convert to string representation for CSV)
            for col in df.columns:
                if df[col].dtype == 'object':
                    # Check if any value in the column is a list
                    sample_values = df[col].dropna().head(10)
                    if any(isinstance(val, list) for val in sample_values):
                        self.logger.info(f"Converting list column '{col}' to string representation")
                        df[col] = df[col].apply(lambda x: str(x) if isinstance(x, list) else x)
            
            # Create CSV file path
            csv_file_path = json_file_path.replace('.json', '.csv')
            
            # Save to CSV with proper handling of special characters
            df.to_csv(csv_file_path, index=False, encoding='utf-8', sep=',', quoting=1)
            
            self.logger.info(f"CSV file saved to: {csv_file_path}")
            self.logger.info(f"CSV contains {len(df)} rows and {len(df.columns)} columns")
            
            return csv_file_path
            
        except Exception as e:
            self.logger.error(f"Error converting JSON to CSV: {str(e)}")
            import traceback
            traceback.print_exc()
            return None


    def convert_json_to_db(self, json_file_path, db_name=None):
        """
        Convert the deduplicated JSON file to SQLite database.
        
        Args:
            json_file_path (str): Path to the JSON file to convert
            db_name (str, optional): Name of the database file. If None, uses same name as JSON file
            
        Returns:
            str: Path to the saved database file
        """
        if not json_file_path:
            self.logger.warning("No JSON file path provided")
            return None
        
        try:
            self.logger.info("Converting JSON to SQLite database...")
            
            # Load the JSON data
            with open(json_file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            # Extract items from the JSON structure
            items = json_data.get('items', [])
            
            if not items:
                self.logger.warning("No items found in JSON file")
                return None
            
            # Create DataFrame from the items directly
            df = pd.DataFrame(items)
            self.logger.info(f"DataFrame created with shape: {df.shape}")
            
            # Handle list columns (convert to JSON string for database storage)
            for col in df.columns:
                if df[col].dtype == 'object':
                    # Check if any value in the column is a list
                    sample_values = df[col].dropna().head(10)
                    if any(isinstance(val, list) for val in sample_values):
                        self.logger.info(f"Converting list column '{col}' to JSON string for database")
                        df[col] = df[col].apply(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, list) else x)
            
            # Create database file path
            if db_name is None:
                db_file_path = json_file_path.replace('.json', '.db')
            else:
                json_dir = os.path.dirname(json_file_path)
                db_file_path = os.path.join(json_dir, db_name)
            
            # Connect to SQLite database
            conn = sqlite3.connect(db_file_path)
            
            # Save DataFrame to database
            table_name = 'processos_comunicacoes_consolidado'
            df.to_sql(table_name, conn, if_exists='replace', index=False)
            
            # Create indexes for better query performance
            cursor = conn.cursor()
            
            self.logger.info("Creating database indexes...")
            # Index on numero_processo (primary key-like)
            cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_numero_processo ON {table_name} (numero_processo)')
            
            # Index on data_disponibilizacao for date queries
            cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_data_disponibilizacao ON {table_name} (data_disponibilizacao)')
            
            # Index on siglaTribunal for tribunal filtering
            cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_sigla_tribunal ON {table_name} (siglaTribunal)')
            
            # Index on status for status filtering
            cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_status ON {table_name} (status)')
            
            # Index on tipoComunicacao for communication type filtering
            cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_tipo_comunicacao ON {table_name} (tipoComunicacao)')
            
            conn.commit()
            
            # Get table info
            cursor.execute(f'SELECT COUNT(*) FROM {table_name}')
            row_count = cursor.fetchone()[0]
            
            cursor.execute(f'PRAGMA table_info({table_name})')
            columns_info = cursor.fetchall()
            
            conn.close()
            
            self.logger.info(f"Database saved to: {db_file_path}")
            self.logger.info(f"Table '{table_name}' created with {row_count} rows and {len(columns_info)} columns")
            self.logger.info("Indexes created on: numero_processo, data_disponibilizacao, siglaTribunal, status, tipoComunicacao")
            
            return db_file_path
            
        except Exception as e:
            self.logger.error(f"Error converting JSON to database: {str(e)}")
            import traceback
            traceback.print_exc()
            return None


    def zip_all(self, BASE_OUTPUT_DIR):
        """
        Create a zip file containing all contents of BASE_OUTPUT_DIR.
        
        Args:
            BASE_OUTPUT_DIR (str): The base output directory to zip
            
        Returns:
            str: Path to the created zip file
        """
        try:
            base_path = Path(BASE_OUTPUT_DIR)
            
            if not base_path.exists():
                self.logger.error(f"Directory does not exist: {BASE_OUTPUT_DIR}")
                return None
            
            # Create zip filename based on directory name
            zip_filename = f"{base_path.name}.zip"
            zip_path = base_path.parent / zip_filename
            
            self.logger.info(f"Creating zip file: {zip_path}")
            
            file_count = 0
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in base_path.rglob('*'):
                    if file_path.is_file():
                        # Store with relative path from BASE_OUTPUT_DIR
                        arcname = file_path.relative_to(base_path.parent)
                        zipf.write(file_path, arcname)
                        file_count += 1
                        
                        if file_count % 100 == 0:
                            self.logger.info(f"Added {file_count} files...")
            
            zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
            self.logger.info(f"Zip file created: {zip_path}")
            self.logger.info(f"  - Files added: {file_count}")
            self.logger.info(f"  - Size: {zip_size_mb:.2f} MB")
            
            return str(zip_path)
            
        except Exception as e:
            self.logger.error(f"Error creating zip file: {str(e)}")
            import traceback
            traceback.print_exc()
            return None
        

    def run(self, BASE_OUTPUT_DIR, assuntos_keys, assuntos_dict, parte=None, tribunal=None, frequency='years', date_range=None):
            for assuntos_key in assuntos_keys:
                assuntos_list = assuntos_dict[assuntos_key]
                
                base_output_dir = os.path.join(BASE_OUTPUT_DIR, assuntos_key)
                
                self.process(assuntos_list, base_output_dir, parte, frequency, tribunal, date_range)
                self.messenger.info(f'Process completed for {assuntos_key}')
                
            # 1. Create the Zip file
            zip_path = None
            try:
                zip_path = self.zip_all(BASE_OUTPUT_DIR)
                self.messenger.info(f'Zip file created: {zip_path}')
            except Exception as e:
                self.messenger.error(f'Error creating zip file: {str(e)}')
                return # Stop if zip creation fails
            
            # 2. Upload to AWS S3
            if zip_path:
                try:
                    s3 = S3()
                    file_name = os.path.basename(zip_path)
                    
                    # Upload with public-read permission
                    s3.upload_file_path(zip_path, s3_key=file_name)
                    self.messenger.info(f'Zip file uploaded to S3 bucket: {s3.bucket_name}')
                    
                    # Get the permanent public link
                    download_link = s3.get_public_url(file_name)
                    
                    # Send the success message
                    self.messenger.success(f'Download Link: {download_link}')
                        
                except Exception as e:
                    self.messenger.error(f'Error interacting with S3: {str(e)}')




if __name__ == '__main__':
    from config.paths import COLLECT_ROOT

    # assuntos_keys = ['general', 'tea', 'ray', 'rename']
    parte = ['agibank']
    tribunal = 'TJRS'
    if not tribunal:
        tribunal = None
    frequency = 'weeks'
    assuntos_keys = ['sem_assunto']
    date_range = ('01-01-2025', '01-05-2026')
    
    folder_name = f'coleta_agibank_{date_range[0].replace("/", "_")}_{date_range[1].replace("/", "_")}' 
    assuntos_dict = ScraperComunicaFull().assuntos_dict
    
    BASE_OUTPUT_DIR = str(COLLECT_ROOT / folder_name)
    if len(parte) > 1:
        for p in parte:
            PARTE_BASE_OUTPUT_DIR = f'{BASE_OUTPUT_DIR}/{p}'
            ScraperComunicaFull().run(PARTE_BASE_OUTPUT_DIR, assuntos_keys=assuntos_keys, assuntos_dict=assuntos_dict, parte=p, frequency=frequency, tribunal=tribunal, date_range=date_range)
    else:
        ScraperComunicaFull().run(BASE_OUTPUT_DIR, assuntos_keys=assuntos_keys, assuntos_dict=assuntos_dict, parte=parte[0], frequency=frequency, tribunal=tribunal, date_range=date_range)