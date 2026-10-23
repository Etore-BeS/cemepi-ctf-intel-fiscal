import os
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1]
_SRC = _PKG / "src" if (_PKG / "src" / "cnpj_pipeline").is_dir() else _PKG    # monorepo (src/) or tarball layout
sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["CNPJ_PIPELINE_NO_DOTENV"] = "1"          # tests never read the developer's .env (salt, HD paths)

from synth import make_world  # noqa: E402


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("world"))
