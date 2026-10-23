import hashlib
import json

import check_freeze as cf


def mk(project, rel, text, freeze_hash=None):
    src = project / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")
    if freeze_hash is not None:
        fj = project / "_freeze" / src.relative_to(project).with_suffix("") / "execute-results" / "html.json"
        fj.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.md5(src.read_bytes()).hexdigest() if freeze_hash == "ok" else freeze_hash
        fj.write_text(json.dumps({"hash": h, "result": {}}))
    return src


CODE = "---\ntitle: x\n---\n```{python}\n1+1\n```\n"


def test_em_dia(tmp_path):
    mk(tmp_path, "index.qmd", "# sem código")
    mk(tmp_path, "eda/a.qmd", CODE, "ok")
    mk(tmp_path, "_modelo.qmd", CODE)          # ignorado (começa com _)
    assert cf.check(tmp_path, ["suplementos"]) == []
    assert cf.main(["--project", str(tmp_path)]) == 0


def test_sem_freeze_e_desatualizado(tmp_path):
    mk(tmp_path, "eda/a.qmd", CODE)
    mk(tmp_path, "eda/b.qmd", CODE, "deadbeef")
    probs = cf.check(tmp_path, [])
    assert any("eda/a.qmd: sem _freeze" in p for p in probs)
    assert any("eda/b.qmd: _freeze desatualizado" in p for p in probs)
    assert cf.main(["--project", str(tmp_path)]) == 1


def test_suplemento_congelado_so_exige_existencia(tmp_path):
    mk(tmp_path, "suplementos/paper1/index.qmd", CODE, "hash-antigo")
    assert cf.check(tmp_path, ["suplementos"]) == []


def test_ipynb_com_codigo(tmp_path):
    nb = {"cells": [{"cell_type": "code", "source": ["print(1)"]}], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    mk(tmp_path, "eda/n.ipynb", json.dumps(nb))
    assert cf.check(tmp_path, []) and "eda/n.ipynb" in cf.check(tmp_path, [])[0]
