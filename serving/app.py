"""App factory: lifespan-managed worker, strict request guard, typed errors, safe logs."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from server import invalid_constant, strict_object  # Reuse the demo's strict JSON hooks.

from . import storage
from .api import RateLimiter, error_response, router
from .config import ROOT, Settings, load_settings
from .provider import ProviderClient
from .service import Service, ServiceError
from .streaming import Notifier, StreamSlots
from .telemetry import Telemetry
from .worker import Worker

LOCAL_HOSTS = ("127.0.0.1", "localhost")
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                                  b"img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
]


class GuardMiddleware:
    """Pure ASGI guard ahead of FastAPI: request IDs, origin check, strict bounded JSON, access log."""

    def __init__(self, app, settings: Settings, telemetry: Telemetry):
        self.app, self.settings, self.telemetry = app, settings, telemetry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.monotonic()
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
        # Always server-generated: a client-chosen ID could carry a secret into logs and responses.
        request_id = "req_" + uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        status_holder = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message["headers"] = list(message.get("headers", [])) + SECURITY_HEADERS + [
                    (b"x-request-id", request_id.encode())]
                if scope["path"].startswith("/api/") and not any(k == b"cache-control" for k, _ in message["headers"]):
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        def fail(status, code, message):
            return JSONResponse({"code": code, "message": message, "request_id": request_id, "retryable": False},
                                status_code=status)

        try:
            if scope["method"] not in {"GET", "HEAD"} and scope["path"].startswith("/api/"):
                origin = headers.get("origin")
                if origin is not None and origin not in self._allowed_origins(scope):
                    return await fail(403, "origin_rejected", "Cross-origin commands are not allowed.")(scope, receive, send_wrapper)
                body, problem = await self._read_json(headers, receive)
                if problem:
                    return await fail(*problem)(scope, receive, send_wrapper)
                receive = self._replay(body)
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            self.telemetry.log("http.request", request_id=request_id, method=scope["method"],
                               route=getattr(route, "path", "unmatched"), status=status_holder.get("status"),
                               duration_ms=round((time.monotonic() - started) * 1000, 2),
                               actor_id=scope["state"].get("actor_id"), tenant_id=scope["state"].get("tenant_id"))

    def _allowed_origins(self, scope) -> set[str]:
        port = scope.get("server", (None, None))[1]
        return {f"http://{host}:{port}" for host in LOCAL_HOSTS}

    async def _read_json(self, headers, receive):
        limit = self.settings.max_body_bytes
        if headers.get("transfer-encoding"):
            return None, (400, "invalid_body", "Transfer-Encoding bodies are not accepted.")
        length = headers.get("content-length", "0")
        if not length.isdigit():
            return None, (400, "invalid_body", "Invalid Content-Length.")
        if int(length) > limit:
            return None, (413, "body_too_large", f"Request body exceeds {limit} bytes.")
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None, (400, "invalid_body", "Client disconnected.")
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                return None, (413, "body_too_large", f"Request body exceeds {limit} bytes.")
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        raw = b"".join(chunks)
        if not raw:
            raw = b"{}"
        elif headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return None, (415, "invalid_content_type", "Use application/json.")
        try:
            parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object, parse_constant=invalid_constant)
        except (UnicodeError, ValueError, RecursionError):
            return None, (400, "invalid_json", "Body must be strict JSON (no duplicate keys or non-finite numbers).")
        if not isinstance(parsed, dict):
            return None, (400, "invalid_body", "Body must be a JSON object.")
        return raw, None

    @staticmethod
    def _replay(body: bytes):
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return receive


def create_app(settings: Settings | None = None, telemetry: Telemetry | None = None) -> FastAPI:
    settings = settings or load_settings()
    telemetry = telemetry or Telemetry()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        storage.check_schema(settings.db_path)
        service = Service(settings)
        provider = ProviderClient(settings.provider_url, settings.provider_key(), settings.provider_timeout_seconds)
        notifier = Notifier()
        worker = Worker(service, provider, settings, telemetry, notifier.notify)
        app.state.service, app.state.provider, app.state.notifier, app.state.worker = service, provider, notifier, worker
        await worker.start()
        telemetry.log("service.started", state="accepting")
        try:
            yield
        finally:
            telemetry.log("service.stopping", state="draining")
            await worker.stop()
            await provider.aclose()
            telemetry.log("service.stopped", state="stopped")

    app = FastAPI(title="Reliable Retail Agent - local serving pilot", version="1.0.0",
                  docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json", lifespan=lifespan)
    app.state.settings, app.state.telemetry = settings, telemetry
    app.state.command_limiter = RateLimiter(settings.command_rate_per_minute, settings.command_burst)
    app.state.unauth_limiter = RateLimiter(settings.unauthenticated_rate_per_minute, settings.unauthenticated_rate_per_minute)
    app.state.stream_slots = StreamSlots(settings)
    app.include_router(router)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError):
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Report locations and types only; never echo submitted values or unexpected key names.
        details = []
        for item in error.errors()[:20]:
            loc = [str(part) for part in item.get("loc", ())]
            if item.get("type") == "extra_forbidden" and loc:
                loc[-1] = "<unexpected field>"
            details.append({"loc": loc, "type": item.get("type")})
        return JSONResponse({"code": "validation_error", "message": "Request failed validation.",
                             "request_id": request.state.request_id, "retryable": False, "errors": details}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(error.status_code, "http_error")
        return JSONResponse({"code": code, "message": "Not found." if error.status_code == 404 else "Request not supported.",
                             "request_id": request.state.request_id, "retryable": False}, status_code=error.status_code)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception):
        telemetry.log("http.unhandled", request_id=request.state.request_id, reason=type(error).__name__)
        return JSONResponse({"code": "internal_error", "message": "Internal error. Commands are idempotent: retry with "
                             "the same Idempotency-Key and inspect the run snapshot.",
                             "request_id": request.state.request_id, "retryable": False}, status_code=500)

    @app.get("/health/live", include_in_schema=False)
    async def live():
        return {"status": "live"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready(request: Request):
        from anyio import to_thread
        worker = getattr(request.app.state, "worker", None)
        if worker is not None and worker.degraded:
            return JSONResponse({"status": "degraded", "outcomes_unrecorded": len(worker.parked)}, status_code=503)
        try:
            ok = await to_thread.run_sync(request.app.state.service.ready) and worker is not None and worker.running
        except Exception:
            ok = False
        return JSONResponse({"status": "ready" if ok else "not_ready"}, status_code=200 if ok else 503)

    app.mount("/serving", StaticFiles(directory=ROOT / "web" / "serving", html=True), name="serving-client")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(LOCAL_HOSTS))
    app.add_middleware(GuardMiddleware, settings=settings, telemetry=telemetry)
    return app
