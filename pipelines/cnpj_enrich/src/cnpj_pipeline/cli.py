"""Command line: python -m cnpj_pipeline <stage> [options]. Run `python -m cnpj_pipeline -h`."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .config import Settings, default_duckdb, default_workdir, load_env
from .state import State


def _setup_logging(s: Settings, verbose: bool) -> None:
    s.ensure()
    fmt = "%(asctime)s %(levelname)s %(message)s"
    handlers = [logging.StreamHandler(sys.stderr), logging.FileHandler(s.log_dir / "pipeline.log")]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt, handlers=handlers, force=True)


def _default_duckdb() -> str | None:
    return default_duckdb()


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workdir", default=default_workdir(),
                        help="work directory on the external drive (env CNPJ_PIPELINE_WORKDIR, else $DATA_ROOT/cnpj_enrich)")
    common.add_argument("--ref-rfb", default=os.environ.get("CNPJ_REF_RFB", "2026-03"), help="RFB release YYYY-MM")
    common.add_argument("--memory-limit", default=os.environ.get("CNPJ_MEMORY_LIMIT", "2GB"), help="DuckDB memory_limit")
    common.add_argument("--threads", type=int, default=int(os.environ.get("CNPJ_THREADS", "2")))
    common.add_argument("--temp-dir", default=os.environ.get("CNPJ_TEMP_DIR"),
                        help="DuckDB spill + CSV extraction directory (default <workdir>/tmp; keep it on the HD)")
    common.add_argument("--key-buckets", type=int, default=16, help="key partitions (fixed per workdir)")
    common.add_argument("--force", action="store_true", help="recompute units already done")
    common.add_argument("-v", "--verbose", action="store_true")

    p = argparse.ArgumentParser(prog="cnpj_pipeline", description=__doc__)
    sub = p.add_subparsers(dest="stage", required=True)
    c = sub.add_parser("collect", parents=[common], help="distinct debtor CNPJs from DEBITO")
    c.add_argument("--duckdb", default=_default_duckdb(), help="CEMEPI DuckDB file (opened READ_ONLY)")
    c.add_argument("--debito-parquet", help="alternative source: Parquet glob of the DEBITO dump")
    c.add_argument("--table", default="DEBITO")
    c.add_argument("--years", type=int, nargs="*", help="only these extraction years")
    d = sub.add_parser("download", parents=[common], help="RFB zips with resume + manifest")
    d.add_argument("--files", nargs="*", help="subset of zip names (default: whole release without Socios)")
    d.add_argument("--socios", action="store_true",
                   help="download Socios0..9.zip (only these unless --files is also given)")
    d.add_argument("--regime", action="store_true",
                   help="download the 4 'Regimes Tributários' zips (entidades-*.zip) from the RFB regime share")
    d.add_argument("--regime-base-url", default=None, help="WebDAV base of the regime share (env CNPJ_REGIME_BASE_URL)")
    d.add_argument("--regime-share", default=None, help="public share id of the regime files (env CNPJ_REGIME_SHARE)")
    d.add_argument("--base-url", default=None, help="WebDAV base (default: RFB public share)")
    d.add_argument("--no-share-auth", action="store_true", help="do not send the public share id as user")
    d.add_argument("--no-ibge", action="store_true", help="skip the TOM->IBGE municipality table")
    d.add_argument("--retries", type=int, default=8)
    d.add_argument("--backoff", type=float, default=2.0)
    e = sub.add_parser("enrich", parents=[common], help="semi-join RFB files with the debtor keys")
    e.add_argument("--files", nargs="*")
    e.add_argument("--groups", type=int, default=1, help="key groups per file (lower RAM, more passes)")
    so = sub.add_parser("socios", parents=[common],
                        help="partners of the debtor firms (LOCAL ONLY: names + masked CPF) + network edge list")
    so.add_argument("--files", nargs="*", help="subset of Socios zips")
    so.add_argument("--hash-salt-env", default=None, metavar="VAR",
                    help="also write socios_hashed.parquet (HMAC-SHA256 of name+document, salt read from env VAR)")
    sub.add_parser("regime", parents=[common], help="tax regime per CNPJ and year (RFB Regimes Tributários / ECF)")
    sub.add_parser("build", parents=[common], help="consolidate cnpj_enriquecido + coverage report")
    x = sub.add_parser("export", parents=[common], help="package for the API server")
    x.add_argument("--pf-policy", default="suprimir", choices=["anonimizar", "suprimir", "remover", "manter"],
                   help="natural-person-linked rows; 'anonimizar' (recommended, needs the salt) or suprimir (default)")
    x.add_argument("--with-socios", action="store_true",
                   help="also export the anonymised socios_vinculos (needs --hash-salt-env; default: no partners)")
    x.add_argument("--hash-salt-env", default="CNPJ_HASH_SALT", metavar="VAR",
                   help="env variable holding the HMAC salt (default CNPJ_HASH_SALT); required by --with-socios and "
                        "--pf-policy anonimizar")
    sub.add_parser("status", parents=[common], help="checkpoint summary")
    a = sub.add_parser("all", parents=[common],
                       help="collect -> download -> enrich -> [socios] -> [regime] -> build -> export")
    a.add_argument("--duckdb", default=_default_duckdb())
    a.add_argument("--debito-parquet")
    a.add_argument("--groups", type=int, default=1)
    a.add_argument("--pf-policy", default="suprimir", choices=["anonimizar", "suprimir", "remover", "manter"])
    a.add_argument("--socios", action="store_true", help="also download Socios and run the socios stage")
    a.add_argument("--regime", action="store_true", help="also download the regime files and run the regime stage")
    a.add_argument("--hash-salt-env", default=None, metavar="VAR")
    a.add_argument("--with-socios", action="store_true")
    a.add_argument("--regime-base-url", default=None)
    a.add_argument("--regime-share", default=None)
    a.add_argument("--base-url", default=None, help="WebDAV base (default: RFB public share)")
    a.add_argument("--no-ibge", action="store_true")
    a.add_argument("--retries", type=int, default=8)
    a.add_argument("--backoff", type=float, default=2.0)
    return p


def main(argv: list[str] | None = None) -> int:
    env_file = load_env()                        # repo .env first, so the defaults below see it
    args = parser().parse_args(argv)
    if not args.workdir:
        raise SystemExit("--workdir (or CNPJ_PIPELINE_WORKDIR / DATA_ROOT in .env) is required")
    s = Settings(Path(args.workdir), ref_rfb=args.ref_rfb, memory_limit=args.memory_limit, threads=args.threads,
                 temp_dir=Path(args.temp_dir) if args.temp_dir else None, key_buckets=args.key_buckets,
                 enrich_groups=getattr(args, "groups", 1))
    _setup_logging(s, args.verbose)
    log = logging.getLogger("cnpj_pipeline")
    log.info("config: .env %s (values are not logged)", "loaded" if env_file else "not found / disabled")
    log.info("stage=%s workdir=%s ref_rfb=%s memory_limit=%s threads=%s temp_dir=%s", args.stage, s.workdir,
             s.ref_rfb, s.memory_limit, s.threads, s.temp_dir)
    state = State(s.state_db)
    from . import build, collect, download, enrich, export, regime, socios
    if args.stage in ("collect", "all"):
        collect.run(s, state, Path(args.duckdb) if args.duckdb else None, args.debito_parquet,
                    table=getattr(args, "table", "DEBITO"), years=getattr(args, "years", None), force=args.force)
    if args.stage in ("download", "all"):
        kw = {}
        if getattr(args, "base_url", None):
            kw["base_url"] = args.base_url
        if getattr(args, "no_share_auth", False):
            kw["share"] = None
        if getattr(args, "no_ibge", False):
            kw["ibge_url"] = None
        if getattr(args, "regime_base_url", None):
            kw["regime_base_url"] = args.regime_base_url
        if getattr(args, "regime_share", None):
            kw["regime_share"] = args.regime_share
        rk = {"retries": getattr(args, "retries", 8), "backoff": getattr(args, "backoff", 2.0)}
        if args.stage == "download":
            download.run(s, files=args.files, socios=args.socios, regime=args.regime, **rk, **kw)
        else:                                    # all: the default release, then the requested extras
            download.run(s, **rk, **{k: v for k, v in kw.items() if not k.startswith("regime")})
            if args.socios or args.regime:
                download.run(s, socios=args.socios, regime=args.regime, **rk,
                             **{k: v for k, v in kw.items() if k != "ibge_url"})
    if args.stage in ("enrich", "all"):
        enrich.run(s, state, files=getattr(args, "files", None) if args.stage == "enrich" else None, force=args.force)
    if args.stage == "socios" or (args.stage == "all" and args.socios):
        socios.run(s, state, files=getattr(args, "files", None) if args.stage == "socios" else None, force=args.force,
                   hash_salt_env=args.hash_salt_env)
    if args.stage == "regime" or (args.stage == "all" and args.regime):
        regime.run(s, state, force=args.force)
    if args.stage in ("build", "all"):
        build.run(s, state, force=args.force)
    if args.stage in ("export", "all"):
        export.run(s, state, pf_policy=args.pf_policy, with_socios=args.with_socios,
                   hash_salt_env=args.hash_salt_env or "CNPJ_HASH_SALT")
    if args.stage == "status":
        for row in state.summary():
            print("\t".join("" if v is None else str(v) for v in row))
    return 0
