"""Configuration from the monorepo `.env` and the git-tracked data manifest written by `build` (ESTRUTURA D6)."""
import json
import os
import subprocess
import sys

import pytest

from cnpj_pipeline import config
from helpers import SRC, cli
from test_pipeline import run_all

MARK = "SALT-MARKER-never-in-logs-0123456789"


@pytest.fixture
def clean_env(monkeypatch):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CNPJ_", "CEMEPI_")) and k != "DATA_ROOT"}
    monkeypatch.setattr(os, "environ", env)          # load_env writes os.environ: keep it test-local
    monkeypatch.setattr(config, "_ENV_FILE", None)
    return env


def test_env_file_relative_paths_and_shell_wins(tmp_path, clean_env):
    (tmp_path / ".env").write_text(
        "# comment\nDATA_ROOT=hd/CEMEPI\nCNPJ_REF_RFB=2026-03\nCNPJ_HASH_SALT='" + MARK + "'\n")
    os.environ["CNPJ_ENV_FILE"] = str(tmp_path / ".env")
    os.environ["CNPJ_REF_RFB"] = "2025-12"                       # already in the shell: .env must not override
    assert config.load_env() == (tmp_path / ".env").resolve()
    assert os.environ["CNPJ_REF_RFB"] == "2025-12"
    assert os.environ["CNPJ_HASH_SALT"] == MARK
    assert config.default_workdir() == str(tmp_path / "hd" / "CEMEPI" / "cnpj_enrich")
    assert config.default_duckdb() == str(tmp_path / "hd" / "CEMEPI" / "api_ctf" / "cemepi.duckdb")
    os.environ["CNPJ_PIPELINE_WORKDIR"] = "outro/wd"
    assert config.default_workdir() == str(tmp_path / "outro" / "wd")


def test_fallback_parser_without_dotenv(tmp_path):
    (tmp_path / ".env").write_text('export A=1\nB="x y"\nC=z # c\n#D=4\n')
    assert config._parse_env(tmp_path / ".env") == {"A": "1", "B": "x y", "C": "z"}


def test_no_dotenv_switch(tmp_path, clean_env):
    (tmp_path / ".env").write_text("DATA_ROOT=/nao/usar\n")
    os.environ.update(CNPJ_ENV_FILE=str(tmp_path / ".env"), CNPJ_PIPELINE_NO_DOTENV="1")
    assert config.load_env() is None and "DATA_ROOT" not in os.environ and config.manifest_dir() is None


def test_cli_reads_env_file_and_never_logs_salt(tmp_path):
    wd = tmp_path / "hd" / "cnpj_enrich"
    (tmp_path / ".env").write_text(f"DATA_ROOT={tmp_path / 'hd'}\nCNPJ_HASH_SALT={MARK}\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CNPJ_", "CEMEPI_")) and k != "DATA_ROOT"}
    env.update(CNPJ_ENV_FILE=str(tmp_path / ".env"), PYTHONPATH=str(SRC))
    p = subprocess.run([sys.executable, "-m", "cnpj_pipeline", "status", "-v"], env=env, capture_output=True,
                       text=True, cwd=tmp_path)
    assert p.returncode == 0, p.stderr
    assert (wd / "logs" / "pipeline.log").exists()               # workdir = $DATA_ROOT/cnpj_enrich
    assert ".env loaded" in p.stderr
    for f in [*wd.rglob("*")]:
        if f.is_file():
            assert MARK.encode() not in f.read_bytes(), f
    assert MARK not in p.stderr + p.stdout


@pytest.fixture(scope="module")
def built(world, tmp_path_factory):
    return run_all(world, tmp_path_factory.mktemp("wdm"))


def test_build_writes_git_manifest_without_pii(world, built, tmp_path):
    mdir = tmp_path / "data_manifests" / "cnpj_enrich"
    cli(built, "build", env={"CNPJ_MANIFEST_DIR": str(mdir)})
    path = mdir / "cnpj_enriquecido_2026-03.json"
    text = path.read_text()
    man = json.loads(text)
    assert man["nome"] == "cnpj_enriquecido" and man["versao"] == "rfb-2026-03"
    assert man["caminho"] == "$CNPJ_PIPELINE_WORKDIR/build/2026-03/cnpj_enriquecido/"
    assert man["linhas"] == len(world["truth"]) == sum(f["linhas"] for f in man["arquivos"])
    assert all(len(f["sha256"]) == 64 for f in man["arquivos"]) and man["fonte"]["rfb_zips"]
    assert str(built) not in text and "/tmp" not in text
    for c in world["truth"]:
        assert c not in text and c[:8] not in text.replace(man["build_commit"] or "", "")
    cli(built, "build", env={"CNPJ_MANIFEST_DIR": str(mdir), "DATA_ROOT": str(built.parent)})
    assert json.loads(path.read_text())["caminho"] == f"$DATA_ROOT/{built.name}/build/2026-03/cnpj_enriquecido/"
    assert sorted(p.name for p in mdir.iterdir()) == ["cnpj_enriquecido_2026-03.json"]     # no .tmp left behind
