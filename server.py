"""Loopback-only, standard-library HTTP host for the synthetic retail demo."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from rra.engine import DemoSession, list_scenarios
from rra.version import development_metadata

MAX_BODY_BYTES = 16_384
MAX_TEXT_LENGTH = 4_000
MAX_SESSIONS = 256
MAX_REQUESTS_PER_SESSION = 1_024
WEB_ROOT = Path(__file__).resolve().parent / "web"
REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


@dataclass
class SessionRecord:
    session: DemoSession
    lock: threading.RLock = field(default_factory=threading.RLock)
    replies: dict = field(default_factory=dict)


class DemoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address=("127.0.0.1", 8765), web_root=WEB_ROOT):
        self.web_root = Path(web_root).resolve()
        self.sessions: dict[str, SessionRecord] = {}
        self.sessions_lock = threading.RLock()
        self.creation_replies: dict = {}
        super().__init__(server_address, DemoHandler)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError("Non-finite JSON number")


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "ReliableRetailDemo/1.0"
    # Close after each request; unread invalid bodies cannot become a next request.
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # Never log customer text or payloads.
        pass

    def send_error(self, code, message=None, explain=None):
        self._json(code, {"error": message or "Unsupported request", "code": "http_error"})

    def _headers(self, status, content_type, length):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()

    def _json(self, status, data):
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(raw))
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise APIError(400, "invalid_body", "Transfer-Encoding is not supported")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            raise APIError(400, "invalid_body", "Multiple Content-Length headers")
        raw_length = lengths[0] if lengths else "0"
        if not re.fullmatch(r"[0-9]+", raw_length):
            raise APIError(400, "invalid_body", "Invalid Content-Length")
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            raise APIError(413, "body_too_large", "Request body exceeds 16384 bytes")
        if not length:
            return {}
        if self.headers.get_content_type() != "application/json":
            raise APIError(415, "invalid_content_type", "Use application/json")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise APIError(400, "invalid_body", "Incomplete request body")
        try:
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object,
                              parse_constant=invalid_constant)
        except (UnicodeError, ValueError, RecursionError):
            raise APIError(400, "invalid_json", "Body must be strict JSON") from None
        if not isinstance(body, dict):
            raise APIError(400, "invalid_body", "Body must be a JSON object")
        return body

    def _validate(self, body, allowed):
        if set(body) - (set(allowed) | {"request_id"}):
            raise APIError(400, "unknown_field", "Request contains unknown fields")
        request_id = body.get("request_id")
        if "request_id" in body and (not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id)):
            raise APIError(400, "invalid_request_id", "request_id must be 1–128 letters, digits, dots, colons, underscores or hyphens")
        return request_id

    @staticmethod
    def _fingerprint(action, body):
        payload = {k: v for k, v in body.items() if k != "request_id"}
        raw = json.dumps([action, payload], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _replay(replies, request_id, fingerprint):
        if request_id is not None and request_id in replies:
            old_fingerprint, status, snapshot = replies[request_id]
            if fingerprint != old_fingerprint:
                raise APIError(409, "idempotency_conflict", "request_id was already used with a different action or payload")
            return status, copy.deepcopy(snapshot)
        if request_id is not None and len(replies) >= MAX_REQUESTS_PER_SESSION:
            raise APIError(429, "request_limit", "Request history is full; create a new session")
        return None

    def _path(self):
        path = urlsplit(self.path).path
        try:
            path = unquote(path, errors="strict")
        except UnicodeError:
            raise APIError(400, "invalid_path", "Invalid URL encoding") from None
        if "\x00" in path or "\\" in path or ".." in path.split("/"):
            raise APIError(404, "not_found", "Not found")
        return path

    def _get(self, path):
        if path == "/api/health":
            return self._json(200, {"status": "ok", "mode": "scripted-synthetic", "paid_calls": 0,
                                    "development": development_metadata()})
        if path == "/api/scenarios":
            return self._json(200, {"scenarios": list_scenarios()})
        match = re.fullmatch(r"/api/sessions/([a-f0-9]{32})", path)
        if match:
            record = self._record(match[1])
            with record.lock:
                return self._json(200, dict(record.session.snapshot(), session_id=match[1]))
        if path.startswith("/api/") or path == "/api":
            raise APIError(404, "not_found", "Unknown API route")
        relative = "index.html" if path == "/" else path.lstrip("/")
        file_path = (self.server.web_root / relative).resolve()
        if not file_path.is_relative_to(self.server.web_root) or not file_path.is_file():
            raise APIError(404, "not_found", "Not found")
        raw = file_path.read_bytes()
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if mime.startswith("text/") or mime == "application/javascript":
            mime += "; charset=utf-8"
        self._headers(200, mime, len(raw))
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _record(self, session_id):
        with self.server.sessions_lock:
            record = self.server.sessions.get(session_id)
        if record is None:
            raise APIError(404, "unknown_session", "Unknown session")
        return record

    def _post(self, path):
        body = self._body()
        if path == "/api/sessions":
            request_id = self._validate(body, {"scenario_id"})
            scenario_id = body.get("scenario_id")
            if not isinstance(scenario_id, str) or scenario_id not in {s["id"] for s in list_scenarios()}:
                raise APIError(400, "invalid_scenario", "Choose a valid scenario_id")
            fingerprint = self._fingerprint("create", body)
            with self.server.sessions_lock:
                replay = self._replay(self.server.creation_replies, request_id, fingerprint)
                if replay:
                    return self._json(*replay)
                if len(self.server.sessions) >= MAX_SESSIONS:
                    raise APIError(429, "session_limit", "Session capacity reached; restart the local demo")
                session_id = uuid4().hex
                session = DemoSession(scenario_id)
                snapshot = dict(session.snapshot(), session_id=session_id)
                self.server.sessions[session_id] = SessionRecord(session)
                if request_id is not None:
                    self.server.creation_replies[request_id] = (fingerprint, 201, copy.deepcopy(snapshot))
            return self._json(201, snapshot)
        match = re.fullmatch(r"/api/sessions/([a-f0-9]{32})/(step|run|respond|reset)", path)
        if not match:
            raise APIError(404, "not_found", "Unknown API route")
        session_id, action = match.groups()
        request_id = self._validate(body, {"text", "prompt_id"} if action == "respond" else set())
        if action == "respond":
            value = body.get("text")
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_LENGTH:
                raise APIError(400, "invalid_text", "text must contain 1–4000 characters")
            prompt_id = body.get("prompt_id")
            if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 256:
                raise APIError(400, "invalid_prompt_id", "prompt_id must identify the displayed confirmation prompt")
        record = self._record(session_id)
        fingerprint = self._fingerprint(action, body)
        with record.lock:
            replay = self._replay(record.replies, request_id, fingerprint)
            if replay:
                return self._json(*replay)
            if action == "respond" and record.session.step_index < 4:
                raise APIError(409, "script_in_progress", "Finish the four-step script before adding a fresh response")
            method = getattr(record.session, action)
            try:
                snapshot = method(body["text"], prompt_id=body["prompt_id"]) if action == "respond" else method()
                snapshot = dict(snapshot, session_id=session_id)
                status = 200
            except Exception:
                # An uncertain engine failure must not be repeated by the same key.
                status, snapshot = 500, {"error": "Session action failed; reset or create a new session", "code": "action_failed"}
            if request_id is not None:
                record.replies[request_id] = (fingerprint, status, copy.deepcopy(snapshot))
        return self._json(status, snapshot)

    def _handle(self):
        try:
            path = self._path()
            if self.command in {"GET", "HEAD"}:
                self._get(path)
            else:
                self._post(path)
        except APIError as error:
            self._json(error.status, {"error": error.message, "code": error.code})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except TimeoutError:
            self._json(408, {"error": "Request timed out", "code": "timeout"})
        except Exception:
            self._json(500, {"error": "Internal demo error", "code": "internal_error"})

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    with DemoHTTPServer((args.host, args.port)) as server:
        print(f"Synthetic demo: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
