"""Narrow client for the synthetic provider, with conservative outcome classification.

Only an explicit, documented no-effect reply counts as a definite failure. Timeouts,
transport errors, unexpected statuses, and malformed bodies are `unknown`: a request
may have been applied even if this process never saw the answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

NO_EFFECT_CODES = frozenset({"revision_conflict", "key_conflict", "key_fenced", "invalid_request",
                             "order_not_found", "rate_limited"})


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Outcome:
    state: str  # succeeded | failed_no_effect | unknown
    code: str
    result: dict[str, Any] | None = None


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def classify_mutation(response: httpx.Response, provider_key: str) -> Outcome:
    body = _json(response)
    if response.status_code == 200:
        if (isinstance(body, dict) and body.get("status") == "applied" and body.get("idempotency_key") == provider_key
                and type(body.get("revision")) is int and type(body.get("effect_id")) is int):
            return Outcome("succeeded", "applied", {"effect_id": body["effect_id"], "revision": body["revision"],
                                                    "duplicate": body.get("duplicate") is True})
        return Outcome("unknown", "provider_malformed_response")
    if (response.status_code in {404, 409, 422, 429} and isinstance(body, dict)
            and body.get("effect") == "none" and body.get("code") in NO_EFFECT_CODES):
        return Outcome("failed_no_effect", body["code"])
    return Outcome("unknown", f"provider_http_{response.status_code}")


class ProviderClient:
    def __init__(self, base_url: str, key: str, timeout: float):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                         headers={"Authorization": f"Bearer {key}"}, trust_env=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_order(self, tenant: str, order_id: str) -> dict[str, Any] | None:
        """Read-only. Failures raise ProviderUnavailable; nothing is retried here."""
        try:
            response = await self._client.get(f"/v1/orders/{tenant}/{order_id}")
        except httpx.HTTPError as error:
            raise ProviderUnavailable(type(error).__name__) from None
        if response.status_code == 404:
            return None
        body = _json(response)
        if response.status_code != 200 or not isinstance(body, dict) or type(body.get("revision")) is not int:
            raise ProviderUnavailable(f"http_{response.status_code}")
        return body

    async def mutate(self, *, provider_key: str, tenant: str, tool_name: str, arguments: dict[str, Any],
                     expected_revision: int) -> Outcome:
        """Exactly one HTTP attempt. Never wrapped in a retry: the caller holds the claim."""
        payload = {"idempotency_key": provider_key, "tenant": tenant, "tool_name": tool_name,
                   "arguments": arguments, "expected_revision": expected_revision}
        try:
            response = await self._client.post("/v1/mutations", json=payload)
        except httpx.TimeoutException:
            return Outcome("unknown", "provider_timeout")
        except httpx.HTTPError as error:
            return Outcome("unknown", "provider_transport_" + type(error).__name__)
        return classify_mutation(response, provider_key)

    async def status(self, provider_key: str) -> Outcome:
        """Read-only reconciliation lookup (the provider fences absent keys)."""
        try:
            response = await self._client.get(f"/v1/mutations/{provider_key}")
        except httpx.HTTPError as error:
            return Outcome("unknown", "status_transport_" + type(error).__name__)
        body = _json(response)
        if response.status_code == 501:
            return Outcome("unknown", "status_unsupported")
        if response.status_code == 200 and isinstance(body, dict) and body.get("idempotency_key") == provider_key:
            if body.get("status") == "not_found" and body.get("fenced") is True:
                return Outcome("failed_no_effect", "status_not_found_fenced", {"fenced": True})
            if body.get("status") == "applied" and type(body.get("revision")) is int:
                return Outcome("succeeded", "status_applied", {"effect_id": body.get("effect_id"),
                                                               "revision": body["revision"]})
        return Outcome("unknown", f"status_http_{response.status_code}")
