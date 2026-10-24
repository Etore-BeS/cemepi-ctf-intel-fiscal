import json

import pytest

from cnpj_pipeline import download
from cnpj_pipeline.config import Settings
from httpserver import Faults, serve


@pytest.fixture()
def server(world):
    f = Faults()
    srv, url = serve(world["release"], f)
    yield f, url
    srv.shutdown()


def _settings(tmp_path):
    return Settings(tmp_path / "wd")


def test_download_resume_retry_and_manifest(world, server, tmp_path):
    faults, url = server
    name = "Estabelecimentos0.zip"
    size = (world["release"] / "2026-03" / name).stat().st_size
    faults.drop_after[name] = [size // 3, size // 3]       # two cut connections
    faults.status["Empresas1.zip"] = [503, 502]              # two server errors
    s = _settings(tmp_path)
    m = download.run(s, base_url=url, ibge_url=None, retries=5, backoff=0.01)
    assert all(v["status"] == "ok" for v in m["files"].values())
    assert len(m["files"]) == 27
    src = world["release"] / "2026-03"
    for n in m["files"]:
        assert (s.rfb_dir / n).read_bytes() == (src / n).read_bytes()
    assert len([1 for (_, n, _) in faults.log if n == name]) == 3     # 2 cut connections + 1 good
    # idempotent: second run downloads nothing
    n_get = len(faults.log)
    download.run(s, base_url=url, ibge_url=None, retries=1, backoff=0.01)
    assert len(faults.log) == n_get
    assert json.loads(s.manifest.read_text())["files"][name]["sha256"]


def test_resume_from_partial_file_and_server_ignoring_range(world, server, tmp_path):
    faults, url = server
    s = _settings(tmp_path)
    s.rfb_dir.mkdir(parents=True)
    name = "Simples.zip"
    src = (world["release"] / "2026-03" / name).read_bytes()
    (s.rfb_dir / f"{name}.part").write_bytes(src[: len(src) // 2])      # left by a killed run
    faults.ignore_range = True                                          # server answers 200: must restart, not append
    download.run(s, files=[name], base_url=url, ibge_url=None, retries=2, backoff=0.01)
    assert (s.rfb_dir / name).read_bytes() == src


def test_corrupted_partial_is_detected(world, server, tmp_path):
    faults, url = server
    s = _settings(tmp_path)
    s.rfb_dir.mkdir(parents=True)
    name = "Empresas2.zip"
    src = (world["release"] / "2026-03" / name).read_bytes()
    bad = bytearray(src[: len(src) // 2]); bad[40] ^= 0xFF                # bit flip in the bytes already on disk
    (s.rfb_dir / f"{name}.part").write_bytes(bytes(bad))
    with pytest.raises(Exception):
        download.run(s, files=[name], base_url=url, ibge_url=None, retries=0, backoff=0.01)
    assert not (s.rfb_dir / f"{name}.part").exists()                    # removed so the next run starts clean
    download.run(s, files=[name], base_url=url, ibge_url=None, retries=0, backoff=0.01)
    assert (s.rfb_dir / name).read_bytes() == src


def test_download_cli_kill_and_resume(world, server, tmp_path):
    """Real process kill in the middle of a file (fault hook after the first MiB chunk), then resume."""
    from helpers import cli
    faults, url = server
    big = tmp_path / "bigrelease" / "2026-03"; big.mkdir(parents=True)
    import os
    (big / "Cnaes.zip").write_bytes(b"")
    import zipfile
    with zipfile.ZipFile(big / "Cnaes.zip", "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("F.CNAECSV", os.urandom(3 << 20))
    from httpserver import Faults as F, serve as sv
    f2 = F(); srv, url2 = sv(big.parent, f2)
    try:
        wd = tmp_path / "wd2"
        p = cli(wd, "download", "--files", "Cnaes.zip", "--base-url", url2, "--no-ibge", "--retries", "0",
                env={"CNPJ_PIPELINE_FAULT": "download:chunk:2"}, check=False)
        assert p.returncode == 137
        part = wd / "rfb" / "2026-03" / "Cnaes.zip.part"
        assert part.exists() and 0 < part.stat().st_size < (big / "Cnaes.zip").stat().st_size
        cli(wd, "download", "--files", "Cnaes.zip", "--base-url", url2, "--no-ibge", "--retries", "0")
        assert (wd / "rfb" / "2026-03" / "Cnaes.zip").read_bytes() == (big / "Cnaes.zip").read_bytes()
        assert any(r and r.startswith("bytes=") for _, _, r in f2.log)
    finally:
        srv.shutdown()


def test_cut_connection_resumes_with_range(tmp_path):
    import os, zipfile
    rel = tmp_path / "rel" / "2026-03"; rel.mkdir(parents=True)
    with zipfile.ZipFile(rel / "Cnaes.zip", "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("F.CNAECSV", os.urandom(2 << 20))
    size = (rel / "Cnaes.zip").stat().st_size
    f = Faults(); f.drop_after["Cnaes.zip"] = [700_000, 900_000]
    srv, url = serve(rel.parent, f)
    try:
        s = _settings(tmp_path)
        download.run(s, files=["Cnaes.zip"], base_url=url, ibge_url=None, retries=3, backoff=0.01)
    finally:
        srv.shutdown()
    assert (s.rfb_dir / "Cnaes.zip").read_bytes() == (rel / "Cnaes.zip").read_bytes()
    starts = [int(r.split("=")[1].rstrip("-")) for (_, _, r) in f.log if r]
    assert len(starts) == 2 and 0 < starts[0] <= 700_000 and starts[0] < starts[1] <= 1_600_000 < size
