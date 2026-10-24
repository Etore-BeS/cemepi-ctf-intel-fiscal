"""A CSV several times larger than DuckDB's memory_limit is processed with bounded RSS (spill to temp_dir)."""
import resource
import subprocess
import sys

from helpers import PKG, cli, cli_env
from synth import big_estab_zip


def test_low_memory_limit(world, tmp_path):
    wd = tmp_path / "wd"
    cli(wd, "collect", "--duckdb", str(world["db"]))
    rfb = wd / "rfb" / "2026-03"; rfb.mkdir(parents=True, exist_ok=True)
    csv_bytes = big_estab_zip(rfb / "Estabelecimentos0.zip", 900_000, debtor_roots=world["roots"])
    assert csv_bytes > 250e6                                 # > 250 MB of CSV vs a 128 MB memory_limit
    cmd = [sys.executable, "-m", "cnpj_pipeline", "enrich", "--workdir", str(wd), "--files", "Estabelecimentos0.zip",
           "--memory-limit", "128MB", "--threads", "1"]
    p = subprocess.run(cmd, cwd=PKG, capture_output=True, text=True, env=cli_env())
    assert p.returncode == 0, p.stderr[-3000:]
    peak_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)   # bytes on macOS, KiB on Linux
    print(f"peak RSS of the enrich process: {peak_mb:.0f} MB for {csv_bytes / 1e6:.0f} MB of CSV")
    assert peak_mb < 400, peak_mb
    import duckdb
    kept = duckdb.connect().execute(
        f"SELECT COUNT(*) FROM read_parquet('{wd}/enrich/2026-03/estabelecimentos/*.parquet')").fetchone()[0]
    assert kept > 350_000                                    # ~half of the rows belong to debtor roots
    assert not (wd / "tmp" / "extract" / "2026-03" / "Estabelecimentos0").exists()   # extracted CSV removed
