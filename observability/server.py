"""Loopback-only, GET-only dashboard host for the frozen synthetic fixture set.

There are no write routes and no handler that can approve, dispatch, retry or cancel anything. Static files
come from a fixed name map (no filesystem path is ever derived from a URL). The viewer principal is fixed
when the server starts; no request parameter can change it.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from .report import STATUSES, Dataset

WEB = Path(__file__).resolve().parent / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/styles.css": ("styles.css", "text/css; charset=utf-8")}
KEY = re.compile(r"/api/(requests|export)/([a-z0-9][a-z0-9-]{0,63})/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})")
TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?Z")
ROUTE = re.compile(r"[a-z][a-z0-9_]{0,63}")
QUERY_KEYS = {"status", "route", "from", "to", "page", "page_size", "warmup"}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                               "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin", "X-Frame-Options": "DENY",
}


class BadRequest(Exception):
    pass


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, dataset: Dataset, principal: str, port: int = 8770):
        self.dataset, self.principal = dataset, principal
        super().__init__(("127.0.0.1", port), Handler)
        self.allowed_hosts = {f"127.0.0.1:{self.server_address[1]}", f"localhost:{self.server_address[1]}"}
        self.static = {route: ((WEB / name).read_bytes(), mime) for route, (name, mime) in STATIC.items()}


def _query(raw: str) -> dict:
    try:
        parsed = parse_qs(raw, keep_blank_values=False, strict_parsing=bool(raw), max_num_fields=len(QUERY_KEYS))
    except ValueError:
        raise BadRequest("invalid_query") from None
    if set(parsed) - QUERY_KEYS or any(len(v) != 1 for v in parsed.values()):
        raise BadRequest("invalid_query")
    q = {k: v[0] for k, v in parsed.items()}
    if "status" in q and q["status"] not in STATUSES:
        raise BadRequest("invalid_status")
    if "route" in q and not ROUTE.fullmatch(q["route"]):
        raise BadRequest("invalid_route")
    for key in ("from", "to"):
        if key in q and not TIME.fullmatch(q[key]):
            raise BadRequest("invalid_time")
    for key, high in (("page", 100_000), ("page_size", 100), ("warmup", 1_000)):
        if key in q and not (q[key].isdigit() and 0 <= int(q[key]) <= high):
            raise BadRequest(f"invalid_{key}")
    return q


class Handler(BaseHTTPRequestHandler):
    server_version = "obs-dashboard"
    sys_version = ""

    def log_message(self, *_):  # no request logging: URLs could carry ids; nothing is persisted
        pass

    def _send(self, status: int, body: bytes, mime: str, extra: dict | None = None):
        self.send_response(status)
        for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data, extra=None):
        self._send(status, json.dumps(data, sort_keys=True).encode(), "application/json; charset=utf-8", extra)

    def _error(self, status: int, code: str):
        self._json(status, {"error": code})

    def do_GET(self):
        if self.headers.get("Host") not in self.server.allowed_hosts:
            return self._error(421, "unexpected_host")  # DNS-rebinding guard for a loopback service
        try:
            split = urlsplit(self.path)
            path = unquote(split.path, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return self._error(400, "invalid_path")
        if "\x00" in path or "\\" in path or ".." in path or "//" in path:
            return self._error(400, "invalid_path")
        if path in self.server.static:
            body, mime = self.server.static[path]
            return self._send(200, body, mime)
        try:
            q = _query(split.query)
        except BadRequest as error:
            return self._error(400, str(error))
        data, viewer = self.server.dataset, self.server.principal
        if path == "/api/meta":
            return self._json(200, data.meta(viewer))
        if path == "/api/requests":
            return self._json(200, data.page(viewer, status=q.get("status"), route=q.get("route"), start=q.get("from"),
                                             end=q.get("to"), page=int(q.get("page", 1)), page_size=int(q.get("page_size", 20))))
        if path == "/api/aggregates":
            return self._json(200, data.aggregates(viewer, start=q.get("from"), end=q.get("to"),
                                                   warmup_per_process=int(q.get("warmup", 0))))
        match = KEY.fullmatch(path)
        if match and not q:
            kind, key = match.group(1), f"{match.group(2)}/{match.group(3)}"
            if kind == "requests":
                found = data.get(viewer, key)
                return self._json(200, found) if found else self._error(404, "not_found")
            found = data.export(viewer, key)
            if found:
                name = key.replace("/", "_")
                return self._json(200, found, {"Content-Disposition": f'attachment; filename="{name}.redacted.json"'})
        return self._error(404, "not_found")  # unknown and not-yours look identical

    def do_HEAD(self):
        self.do_GET()

    def _no_writes(self):
        self.send_response(405)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _no_writes
