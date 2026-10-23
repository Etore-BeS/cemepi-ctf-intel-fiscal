"""Stage `download`: fetch one monthly release of the RFB open CNPJ data with resume and verification.

* Remote listing by WebDAV PROPFIND (size, ETag, Last-Modified); HEAD as fallback.
* Resume: bytes already in <file>.part are kept and the rest is requested with `Range: bytes=N-` and `If-Range: <ETag>`;
  a 200 answer (server ignored the range or the file changed) restarts the file from zero.
* Retries with exponential backoff and jitter on network errors, 5xx and 429.
* Verification: final size == remote size, ZIP CRC check of every member (zipfile.testzip), SHA-256 recorded.
* manifest.json (atomic rewrite after each file) holds remote metadata, local size, SHA-256, members and status.
The RFB does not publish checksums, so the SHA-256 is ours: it pins the bytes that were enriched (reproducibility).

Source: Receita Federal, "Dados Abertos do CNPJ", public Nextcloud share at arquivos.receitafederal.gov.br. The share
tokens below are the public identifiers of those shares (they appear in the public links); they are not credentials.
* Monthly release (Empresas, Estabelecimentos, Simples, Socios, code tables): share RFB_SHARE, folder YYYY-MM.
* Regimes Tributários (ECF; entidades-*.zip): share REGIME_SHARE, files at the share root (annual, not per month).
  Verified on 2026-10-23 by PROPFIND; overridable with --regime-base-url / --regime-share (or env vars).
Socios*.zip and the regime zips are downloaded only on request (`--socios`, `--regime`). The manifest is merged under a
file lock, so two `download` processes on the same workdir never drop each other's entries.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
import zipfile
from pathlib import Path
from urllib.parse import unquote

import requests

from .config import Settings
from .layouts import REGIME_FILES, RELEASE_FILES, SOCIOS_FILES
from .state import _fault

log = logging.getLogger("cnpj_pipeline")

RFB_BASE = "https://arquivos.receitafederal.gov.br/public.php/webdav"
RFB_SHARE = "YggdBLfdninEJX9"
REGIME_BASE = os.environ.get("CNPJ_REGIME_BASE_URL", RFB_BASE)
REGIME_SHARE = os.environ.get("CNPJ_REGIME_SHARE", "MPPfFit7g7zdA8C")
# RFB table TOM (SIAFI) municipality code -> IBGE code; the Estabelecimentos file uses the TOM code.
IBGE_MUNICIPIOS_URL = "https://www.gov.br/receitafederal/dados/municipios.csv"
CHUNK = 1 << 20          # hashing block
NET_CHUNK = 1 << 16      # network block: at most 64 KiB are lost when a connection drops


class Downloader:
    def __init__(self, s: Settings, base_url: str = RFB_BASE, share: str | None = RFB_SHARE,
                 retries: int = 8, backoff: float = 2.0, max_backoff: float = 120.0, timeout: float = 60.0):
        self.s, self.base, self.retries, self.backoff, self.max_backoff = s, base_url.rstrip("/"), retries, backoff, max_backoff
        self.timeout = timeout
        self.http = requests.Session()
        if share:
            self.http.auth = (share, "")
        self.http.headers["User-Agent"] = "cemepi-cnpj-pipeline/0.1 (academic research)"
        self.dir = s.rfb_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest = json.loads(s.manifest.read_text()) if s.manifest.exists() else {"ref_rfb": s.ref_rfb, "files": {}}

    # ---------------------------------------------------------------------------------------------- manifest
    @contextmanager
    def _lock(self):
        lock = self.s.manifest.with_suffix(".lock")
        with lock.open("a") as fh:
            try:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX)
            except ImportError:          # pragma: no cover (Windows)
                pass
            yield

    def _save(self, name: str | None = None, entry: dict | None = None) -> None:
        """Read-merge-write under a lock: only this entry changes, entries written by other processes are kept."""
        with self._lock():
            disk = json.loads(self.s.manifest.read_text()) if self.s.manifest.exists() else {"ref_rfb": self.s.ref_rfb,
                                                                                              "files": {}}
            if name is not None:
                disk["files"][name] = entry
            for k, v in self.manifest["files"].items():
                disk["files"].setdefault(k, v)
            self.manifest = disk
            tmp = self.s.manifest.with_suffix(f".json.tmp{os.getpid()}")
            tmp.write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False, sort_keys=True))
            os.replace(tmp, self.s.manifest)

    def refresh(self) -> None:
        if self.s.manifest.exists():
            with self._lock():
                self.manifest = json.loads(self.s.manifest.read_text())

    # ----------------------------------------------------------------------------------------------- listing
    def listing(self, folder: str | None = None) -> dict[str, dict]:
        """PROPFIND of <base>/<folder>/ (default: the release folder YYYY-MM; '' = share root)."""
        folder = self.s.ref_rfb if folder is None else folder
        url = f"{self.base}/{folder}/" if folder else f"{self.base}/"
        try:
            r = self._request("PROPFIND", url, headers={"Depth": "1"}, stream=False)
            out = {}
            ns = {"d": "DAV:"}
            for resp in ET.fromstring(r.content).findall("d:response", ns):
                href = unquote(resp.findtext("d:href", default="", namespaces=ns)).rstrip("/").rsplit("/", 1)[-1]
                size = resp.findtext(".//d:getcontentlength", default=None, namespaces=ns)
                if size is None:
                    continue
                out[href] = {"size": int(size),
                             "etag": (resp.findtext(".//d:getetag", default="", namespaces=ns) or "").strip(),
                             "last_modified": resp.findtext(".//d:getlastmodified", default="", namespaces=ns)}
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("PROPFIND failed (%s); falling back to HEAD per file", e)
            return {}

    def _head(self, url: str) -> dict:
        r = self._request("HEAD", url, stream=False)
        return {"size": int(r.headers.get("Content-Length", -1)), "etag": r.headers.get("ETag", "").strip(),
                "last_modified": r.headers.get("Last-Modified", "")}

    # --------------------------------------------------------------------------------------------- requests
    def _sleep(self, attempt: int) -> None:
        t = min(self.max_backoff, self.backoff * 2 ** attempt) * (0.5 + random.random() / 2)
        log.info("retry in %.1fs", t)
        time.sleep(t)

    def _request(self, method: str, url: str, headers: dict | None = None, stream: bool = True) -> requests.Response:
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = self.http.request(method, url, headers=headers or {}, stream=stream, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                return r
            except (requests.RequestException, OSError) as e:
                last = e
                log.warning("%s %s failed (attempt %d/%d): %s", method, url.rsplit("/", 1)[-1], attempt + 1,
                            self.retries + 1, e)
                if attempt < self.retries:
                    self._sleep(attempt)
        raise RuntimeError(f"{method} {url} failed after {self.retries + 1} attempts: {last}")

    # ---------------------------------------------------------------------------------------------- one file
    def fetch(self, name: str, remote: dict, url: str | None = None, is_zip: bool = True) -> dict:
        url = url or f"{self.base}/{self.s.ref_rfb}/{name}"
        final, part = self.dir / name, self.dir / f"{name}.part"
        self.refresh()
        entry = self.manifest["files"].get(name, {})
        if (entry.get("status") == "ok" and final.exists() and final.stat().st_size == entry.get("local_size")
                and (remote.get("etag") in ("", None) or remote.get("etag") == entry.get("etag"))):
            log.info("download %s: ok in manifest, skip", name)
            return entry
        if final.exists() and entry.get("status") != "ok":
            os.replace(final, part)          # unverified file from an interrupted run: verify/resume it
        size = remote.get("size", -1)
        for attempt in range(self.retries + 1):
            have = part.stat().st_size if part.exists() else 0
            if size >= 0 and have > size:
                log.warning("%s: .part larger than remote (%d > %d), restarting", name, have, size)
                part.unlink(); have = 0
            if size >= 0 and have == size:
                break
            headers = {}
            if have:
                headers["Range"] = f"bytes={have}-"
                if remote.get("etag"):
                    headers["If-Range"] = remote["etag"]
            try:
                r = self._request("GET", url, headers=headers)
                if r.status_code == 416:
                    break
                r.raise_for_status()
                mode = "ab"
                if have and r.status_code == 200:
                    log.warning("%s: server sent the whole file (range ignored or file changed); restarting", name)
                    mode, have = "wb", 0
                log.info("download %s: %s at byte %d of %d", name, "resume" if have else "start", have, size)
                got = 0
                with part.open(mode) as fh:
                    for chunk in r.iter_content(NET_CHUNK):
                        fh.write(chunk)
                        got += len(chunk)
                        if got >= CHUNK:          # test hook, once per MiB
                            got = 0
                            fh.flush()
                            _fault("download:chunk")
                    fh.flush(); os.fsync(fh.fileno())
                if size < 0 or part.stat().st_size == size:
                    break
                raise requests.ConnectionError(f"short read: {part.stat().st_size} of {size} bytes")
            except (requests.RequestException, OSError, RuntimeError) as e:
                log.warning("download %s interrupted (attempt %d/%d): %s", name, attempt + 1, self.retries + 1, e)
                if attempt == self.retries:
                    raise
                self._sleep(attempt)
        local = part.stat().st_size
        if size >= 0 and local != size:
            raise RuntimeError(f"{name}: size {local} != remote {size}")
        sha, members = self._verify(part, is_zip)
        os.replace(part, final)
        entry = {"status": "ok", "url": url,
                 "remote_size": size, "etag": remote.get("etag"), "last_modified": remote.get("last_modified"),
                 "local_size": local, "sha256": sha, "members": members,
                 "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        self._save(name, entry)
        log.info("download %s: ok (%d bytes, sha256 %s…)", name, local, sha[:12])
        return entry

    @staticmethod
    def _verify(path: Path, is_zip: bool) -> tuple[str, list]:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK), b""):
                h.update(chunk)
        members = []
        if is_zip:
            with zipfile.ZipFile(path) as zf:
                bad = zf.testzip()
                if bad is not None:
                    path.unlink()
                    raise RuntimeError(f"{path.name}: CRC error in member {bad}; file removed, run again")
                members = [{"name": i.filename, "size": i.file_size, "crc": f"{i.CRC:08x}"} for i in zf.infolist()]
        return h.hexdigest(), members


def run(s: Settings, files: list[str] | None = None, base_url: str = RFB_BASE, share: str | None = RFB_SHARE,
        ibge_url: str | None = IBGE_MUNICIPIOS_URL, retries: int = 8, backoff: float = 2.0,
        socios: bool = False, regime: bool = False, regime_base_url: str | None = None,
        regime_share: str | None = REGIME_SHARE) -> dict:
    """Default (no files, no flags): the release without Socios + the IBGE table (unchanged behaviour).
    `socios` / `regime` alone download ONLY those extra files (the IBGE table is skipped); with `files` they are added.
    Files already ok in the manifest (same size and ETag) are never downloaded again."""
    d = Downloader(s, base_url, share, retries=retries, backoff=backoff)
    extras = socios or regime
    names = list(files or ([] if extras else RELEASE_FILES))
    if socios:
        names += [n for n in SOCIOS_FILES if n not in names]
    if names:
        listing = d.listing()
        missing = [n for n in names if listing and n not in listing]
        if missing:
            raise SystemExit(f"Files not found in release {s.ref_rfb}: {missing}")
        for n in names:
            remote = listing.get(n) or d._head(f"{d.base}/{s.ref_rfb}/{n}")
            d.fetch(n, remote)
    if regime:
        rb = (regime_base_url or REGIME_BASE).rstrip("/")
        dr = Downloader(s, rb, regime_share, retries=retries, backoff=backoff)
        rl = dr.listing("")
        missing = [n for n in REGIME_FILES if rl and n not in rl]
        if missing:
            raise SystemExit(f"Regime files not found at {rb} (share {regime_share}): {missing}. "
                             "Check the current public link of 'Regimes Tributários' and pass --regime-base-url/--regime-share.")
        for n in REGIME_FILES:
            dr.fetch(n, rl.get(n) or dr._head(f"{rb}/{n}"), url=f"{rb}/{n}")
        d.refresh()
    if ibge_url and not (extras and not files):
        ibge = requests.Session()
        ibge.headers["User-Agent"] = d.http.headers["User-Agent"]
        d2 = Downloader(s, base_url, None, retries=retries, backoff=backoff)
        d2.http = ibge
        d2.fetch("municipios_ibge.csv", d2._head(ibge_url), url=ibge_url, is_zip=False)
    d.refresh()
    total = sum(e["local_size"] for e in d.manifest["files"].values())
    log.info("download: %d files ok in the manifest, %.2f GB", len(d.manifest["files"]), total / 1e9)
    return d.manifest
