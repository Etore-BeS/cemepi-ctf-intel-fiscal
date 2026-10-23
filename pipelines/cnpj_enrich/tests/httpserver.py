"""Local WebDAV-ish file server for tests: PROPFIND, HEAD, GET with Range/If-Range, and fault injection."""
from __future__ import annotations

import hashlib
import threading
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Faults:
    def __init__(self):
        self.drop_after: dict[str, list[int]] = {}   # file -> list of byte counts; each GET pops one and cuts there
        self.status: dict[str, list[int]] = {}       # file -> list of status codes to answer first (e.g. 503)
        self.ignore_range = False
        self.log: list[tuple] = []


def serve(root: Path, faults: Faults):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _file(self):
            rel = self.path.split("/webdav/", 1)[-1]
            return root / rel

        def _etag(self, p: Path) -> str:
            return '"' + hashlib.md5(f"{p.stat().st_size}-{p.stat().st_mtime_ns}".encode()).hexdigest() + '"'

        def do_PROPFIND(self):
            d = self._file()
            items = []
            for p in sorted(d.iterdir()):
                if p.is_file():
                    items.append(f"<d:response><d:href>/public.php/webdav/{d.name}/{p.name}</d:href><d:propstat><d:prop>"
                                 f"<d:getcontentlength>{p.stat().st_size}</d:getcontentlength>"
                                 f"<d:getetag>{self._etag(p)}</d:getetag><d:getlastmodified>{formatdate(p.stat().st_mtime, usegmt=True)}"
                                 "</d:getlastmodified></d:prop></d:propstat></d:response>")
            body = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(items) + "</d:multistatus>").encode()
            self.send_response(207); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self):
            p = self._file()
            if not p.exists():
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Length", str(p.stat().st_size))
            self.send_header("ETag", self._etag(p)); self.end_headers()

        def do_GET(self):
            p = self._file()
            faults.log.append(("GET", p.name, self.headers.get("Range")))
            st = faults.status.get(p.name)
            if st:
                code = st.pop(0)
                self.send_response(code); self.send_header("Content-Length", "0"); self.end_headers(); return
            if not p.exists():
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            data = p.read_bytes()
            start = 0
            rng = self.headers.get("Range")
            if rng and not faults.ignore_range and (self.headers.get("If-Range") in (None, self._etag(p))):
                start = int(rng.split("=")[1].split("-")[0])
                if start >= len(data):
                    self.send_response(416); self.send_header("Content-Length", "0"); self.end_headers(); return
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            else:
                self.send_response(200)
            body = data[start:]
            self.send_header("Content-Length", str(len(body))); self.send_header("ETag", self._etag(p)); self.end_headers()
            cut = faults.drop_after.get(p.name)
            if cut:
                n = cut.pop(0)
                self.wfile.write(body[:n]); self.wfile.flush()
                self.close_connection = True
                try:
                    self.connection.shutdown(2)
                except OSError:
                    pass
                return
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/public.php/webdav"
