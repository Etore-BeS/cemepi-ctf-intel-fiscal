import os
from pathlib import Path

from dotenv import load_dotenv

PIPELINE_ROOT = Path(__file__).resolve().parents[2]  # .../pipelines/litigancia
REPO_ROOT = Path(__file__).resolve().parents[4]      # .../cemepi-ctf-intel-fiscal
load_dotenv(REPO_ROOT / ".env")

_DEFAULT_LAKE = "/Volumes/Meedi_Etore_HD1/CEMEPI/Coletas/gilson"
LAKE_ROOT = Path(os.getenv("LAKE_ROOT", _DEFAULT_LAKE))

_collect = os.getenv("COLLECT_ROOT", str(LAKE_ROOT))
COLLECT_ROOT = Path(_collect) if Path(_collect).is_absolute() else REPO_ROOT / _collect

BRONZE_COLETAS = LAKE_ROOT / "bronze_layer" / "coletas_delta"
BRONZE_FACE = LAKE_ROOT / "bronze_layer" / "face_processos_delta"
SILVER_PROCESSOS = LAKE_ROOT / "silver_layer" / "processos_delta"
SILVER_FACE_CLEAN = LAKE_ROOT / "silver_layer" / "face_processos_clean_delta"
SILVER_MOVIMENTACOES = LAKE_ROOT / "silver_layer" / "movimentacoes_delta"
AGGREGATED_DB = LAKE_ROOT / "aggregated_database.db"
