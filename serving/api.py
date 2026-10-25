"""Versioned HTTP routes. Commands are POSTs with Idempotency-Key; observation is GET/SSE."""
from __future__ import annotations

import json
import time
from typing import Annotated, Literal

from anyio import to_thread
from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from . import auth
from .provider import ProviderUnavailable
from .service import ServiceError
from .streaming import SSEResponse, event_stream
from .trace_export import to_trace

RUN_ID = r"^run_[0-9a-f]{32}$"
SESSION_ID = r"^ses_[0-9a-f]{32}$"
Text = Annotated[str, Field(min_length=1, max_length=100)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SessionCreate(Strict):
    resource_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")


class Address(Strict):
    street: Text
    apartment: Annotated[str, Field(max_length=32)]
    city: Text
    state: Text
    postal_code: Annotated[str, Field(min_length=1, max_length=16)]
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]


class RunRequest(Strict):
    type: Literal["change_order_address"]
    address: Address


class RunCreate(Strict):
    request: RunRequest
    message: Annotated[str, Field(min_length=1, max_length=4000)] | None = None


class ConsentCreate(Strict):
    response: Annotated[str, Field(min_length=1, max_length=4000)]
    prompt_id: str = Field(pattern=r"^prm_[0-9a-f]{32}$")
    proposal_version: int = Field(ge=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RateLimiter:
    """In-memory token buckets; per-process and reset on restart (documented limitation)."""

    def __init__(self, per_minute: int, burst: int, max_keys: int = 10_000):
        self.rate, self.burst, self.max_keys = per_minute / 60.0, burst, max_keys
        self.buckets: dict[str, tuple[float, float]] = {}

    def take(self, key: str) -> int | None:
        now = time.monotonic()
        tokens, last = self.buckets.get(key, (float(self.burst), now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1:
            self.buckets[key] = (tokens, now)
            return max(1, int((1 - tokens) / self.rate + 0.999))
        if key not in self.buckets and len(self.buckets) >= self.max_keys:
            self.buckets.pop(next(iter(self.buckets)))
        self.buckets[key] = (tokens - 1, now)
        return None


def error_response(request: Request, error: ServiceError) -> JSONResponse:
    body = {"code": error.code, "message": error.message, "request_id": getattr(request.state, "request_id", None),
            "retryable": error.retryable, **error.extra}
    headers = {}
    if error.retry_after is not None:
        body["retry_after_seconds"] = error.retry_after
        headers["Retry-After"] = str(error.retry_after)
    if error.status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="rra-local-pilot"'
    return JSONResponse(body, status_code=error.status, headers=headers)


class Caller:
    def __init__(self, principal: auth.Principal, token: str):
        self.principal, self.token = principal, token


async def authenticate(request: Request) -> Caller:
    state = request.app.state
    client = request.client.host if request.client else "unknown"  # Never X-Forwarded-For.
    token = auth.parse_bearer(request.headers.get("authorization"))
    principal = None
    if token is not None:
        principal = await to_thread.run_sync(_resolve, state.service, token)
    if principal is None:
        retry = state.unauth_limiter.take(client)
        state.telemetry.count("auth_failures")
        if retry is not None:
            state.telemetry.count("rate_limited_unauthenticated")
            raise ServiceError(429, "rate_limited", "Too many unauthenticated requests.", retry_after=retry)
        raise ServiceError(401, "unauthenticated", "A valid bearer token is required.")
    request.state.actor_id, request.state.tenant_id = principal.actor_id, principal.tenant_id
    return Caller(principal, token)


def _resolve(service, token):
    conn = service.connect()
    try:
        return auth.resolve(conn, token)
    finally:
        conn.close()


def require(scope: str, *, command: bool = False):
    async def dependency(request: Request, caller: Caller = Depends(authenticate)) -> Caller:
        if not caller.principal.has(scope):
            raise ServiceError(403, "insufficient_scope", f"This credential lacks the {scope} scope.")
        if command:
            retry = request.app.state.command_limiter.take(caller.principal.actor_id)
            if retry is not None:
                request.app.state.telemetry.count("rate_limited_commands")
                raise ServiceError(429, "rate_limited", "Command rate limit exceeded. Retry with the SAME "
                                   "Idempotency-Key; do not mint a new key for a rate-limited mutation.",
                                   retryable=True, retry_after=retry)
        return caller
    return dependency


def idempotency_key(request: Request) -> str:
    return request.headers.get("idempotency-key", "")


def reply(request: Request, status: int, body: dict) -> JSONResponse:
    request.app.state.notifier.notify()
    return JSONResponse(body, status_code=status)


router = APIRouter(prefix="/api/v1")
RunId = Annotated[str, Path(pattern=RUN_ID)]
SessionId = Annotated[str, Path(pattern=SESSION_ID)]


@router.post("/sessions", status_code=201)
async def create_session(request: Request, body: SessionCreate, caller: Caller = Depends(require("runs:write", command=True))):
    service = request.app.state.service
    status, result = await to_thread.run_sync(service.create_session, caller.principal, idempotency_key(request), body.model_dump())
    request.app.state.telemetry.count("commands_accepted")
    return reply(request, status, result)


@router.post("/sessions/{session_id}/runs", status_code=202)
async def create_run(request: Request, session_id: SessionId, body: RunCreate,
                     caller: Caller = Depends(require("runs:write", command=True))):
    state = request.app.state
    data = body.model_dump()
    key = idempotency_key(request)
    replay, resource_id = await to_thread.run_sync(state.service.prepare_run, caller.principal, session_id, key, data)
    if replay is not None:
        return reply(request, *replay)
    try:
        order = await state.provider.get_order(caller.principal.tenant_id, resource_id)
    except ProviderUnavailable:
        raise ServiceError(503, "provider_unavailable", "Order lookup failed; nothing was created. Retry with the same "
                           "Idempotency-Key.", retryable=True, retry_after=2) from None
    if order is None:
        raise ServiceError(404, "not_found", "Resource not found.")
    status, result = await to_thread.run_sync(state.service.create_run, caller.principal, session_id, key, data, order)
    state.telemetry.count("commands_accepted")
    return reply(request, status, result)


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: RunId, caller: Caller = Depends(require("runs:read"))):
    return await to_thread.run_sync(request.app.state.service.snapshot, caller.principal, run_id)


@router.post("/runs/{run_id}/consents")
async def post_consent(request: Request, run_id: RunId, body: ConsentCreate,
                       caller: Caller = Depends(require("consent:write", command=True))):
    state = request.app.state
    if state.worker.degraded:  # Admission control: no new authorization while outcomes cannot be recorded.
        raise ServiceError(503, "service_degraded", "Dispatch is paused because outcomes cannot be recorded right now; "
                           "nothing was authorized. Retry later with the same Idempotency-Key.",
                           retryable=True, retry_after=int(state.settings.recovery_probe_seconds))
    status, result = await to_thread.run_sync(state.service.consent, caller.principal, run_id,
                                              idempotency_key(request), body.model_dump())
    state.telemetry.count(f"consent_{result.get('decision')}")
    if result.get("decision") == "authorized":
        state.worker.wake()
    return reply(request, status, result)


@router.post("/runs/{run_id}/cancel")
async def post_cancel(request: Request, run_id: RunId, caller: Caller = Depends(require("runs:write", command=True))):
    status, result = await to_thread.run_sync(request.app.state.service.cancel, caller.principal, run_id, idempotency_key(request))
    return reply(request, status, result)


@router.get("/runs/{run_id}/events")
async def get_events(request: Request, run_id: RunId, caller: Caller = Depends(require("runs:read"))):
    state = request.app.state
    cursor = request.headers.get("last-event-id")
    after = await to_thread.run_sync(state.service.stream_start, caller.principal, run_id, cursor)
    actor_id = caller.principal.actor_id
    if not state.stream_slots.acquire(actor_id):
        state.telemetry.count("streams_rejected_capacity")
        raise ServiceError(429, "stream_limit", "Too many open event streams.", retryable=True, retry_after=5)

    def still_authorized() -> bool:
        conn = state.service.connect()
        try:
            return auth.is_active(conn, caller.principal, caller.token) and state.service.can_observe(caller.principal, run_id)
        finally:
            conn.close()

    stream = event_stream(service=state.service, settings=state.settings, notifier=state.notifier,
                          telemetry=state.telemetry, run_id=run_id, after=after,
                          still_authorized=still_authorized)
    return SSEResponse(stream, send_timeout=state.settings.sse_send_timeout_seconds, telemetry=state.telemetry,
                       release=lambda: state.stream_slots.release(actor_id))


@router.get("/runs/{run_id}/trace")
async def get_trace(request: Request, run_id: RunId, contract: Literal["trace-v1", "trace-v2"] = "trace-v1",
                    caller: Caller = Depends(require("runs:read"))):
    """The run's events mapped onto the evaluation owner's trace-v1 envelope (hashes and codes only)."""
    envelopes = await to_thread.run_sync(request.app.state.service.trace_envelopes, caller.principal, run_id)
    body = "".join(json.dumps(to_trace(event, contract), sort_keys=True, separators=(",", ":")) + "\n" for event in envelopes)
    return Response(body, media_type="application/x-ndjson")


@router.get("/ops/metrics")
async def metrics(request: Request, caller: Caller = Depends(require("ops:read"))):
    state = request.app.state
    counts = await to_thread.run_sync(state.service.counts)
    snapshot = state.telemetry.snapshot()
    snapshot["gauges"].update({"streams_open": state.stream_slots.total, "worker_degraded": int(state.worker.degraded),
                               "outcomes_unrecorded": len(state.worker.parked),
                               "observation_spans": state.observation_spans.stats(), **counts})
    return snapshot
