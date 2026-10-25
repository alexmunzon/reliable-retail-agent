"""Exact-payload confirmation for explicitly registered local mutation tools.

This boundary checks consent, not the business correctness of a proposed mutation.
Callers must authenticate the customer and bind their response to the prompt they saw.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Callable, Mapping
from uuid import uuid4

from study.consent_current import classify_response


def canonical_json(value: Any) -> str:
    """Serialize strict JSON without silently coercing keys or Python objects."""
    def check(item: Any) -> None:
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("JSON object keys must be strings")
                check(child)
            return
        raise ValueError(f"Unsupported JSON value: {type(item).__name__}")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True, init=False)
class MutationIntent:
    tool_name: str
    operation_id: str
    _payload: str

    def __init__(self, tool_name: str, arguments: dict[str, Any], operation_id: str):
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name must be a nonempty string")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a nonempty string")
        if type(arguments) is not dict:
            raise ValueError("arguments must be a JSON object")
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "_payload", canonical_json({"tool_name": tool_name, "arguments": arguments}))

    @property
    def arguments(self) -> dict[str, Any]:
        return json.loads(self._payload)["arguments"]

    @property
    def fingerprint(self) -> str:
        return sha256(self._payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"tool_name": self.tool_name, "arguments": self.arguments,
                "fingerprint": self.fingerprint, "operation_id": self.operation_id}


@dataclass(frozen=True)
class GateResult:
    status: str
    detail: str
    dispatched: bool = False
    result: Any = None
    prompt: str | None = None
    prompt_id: str | None = None
    fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


class UncertainMutationError(RuntimeError):
    """The tool may have mutated state; its operation must never be retried."""


@dataclass(frozen=True)
class UncertainMutationResult:
    """Explicit uncertain return for tools that do not raise on a lost outcome."""
    detail: str = "The mutation outcome could not be determined."


class ConfirmationGate:
    """One active proposal; each operation ID can invoke at most one local tool.

    A registry entry is an explicit declaration that a tool mutates state. Unknown
    tools are denied; read-only dispatch belongs outside this mutation boundary.
    This in-memory ledger provides at-most-once invocation within this instance,
    not durable exactly-once guarantees across crashes or multiple processes.
    """
    def __init__(self, registry: Mapping[str, Callable[[dict[str, Any]], Any]]):
        self._registry = dict(registry)
        if any(not isinstance(name, str) or not name or not callable(tool)
               for name, tool in self._registry.items()):
            raise ValueError("Registry requires named callable mutation tools")
        self._active: MutationIntent | None = None
        self._prompt: str | None = None
        self._prompt_id: str | None = None
        self._authorized: tuple[str, str, str] | None = None
        self._counter = 0
        self._instance_id = uuid4().hex
        self._rejected = False
        self._halted = False
        self._ledger: dict[str, dict[str, Any]] = {}
        self._dispatches: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self.status = "idle"

    def _event(self, kind: str, title: str, detail: str, intent: MutationIntent | None = None) -> None:
        event = {"seq": len(self._events) + 1, "type": kind, "title": title, "detail": detail}
        if intent is not None:
            event["fingerprint"] = intent.fingerprint
        self._events.append(event)

    def _result(self, status: str, detail: str, *, dispatched: bool = False, result: Any = None) -> GateResult:
        self.status = status
        return GateResult(status, detail, dispatched, result, self._prompt, self._prompt_id,
                          self._active.fingerprint if self._active else None)

    def propose(self, intent: MutationIntent) -> GateResult:
        if not isinstance(intent, MutationIntent):
            raise TypeError("A typed MutationIntent is required")
        if self._halted:
            return self._halt_result()
        if intent.tool_name not in self._registry:
            self._authorized = None
            self._event("blocked", "Unknown tool blocked", intent.tool_name, intent)
            return self._result("blocked_unknown_tool", "Tool is not in the explicit mutation registry.")
        if intent.operation_id in self._ledger:
            return self._result("already_attempted", "This operation has already consumed its one dispatch attempt.")
        if self._active == intent and self.status in {"awaiting_confirmation", "authorized", "ambiguous", "stale_consent"}:
            return self._result(self.status, "Existing exact-payload prompt remains active.")
        changed = self._active is not None and self._active != intent
        self._authorized = None
        self._active = intent
        self._rejected = False
        self._counter += 1
        self._prompt_id = f"prompt-{self._instance_id}-{self._counter}-{intent.fingerprint[:12]}"
        self._prompt = ("Please confirm this exact mutation.\n"
                        f"Operation: {intent.operation_id}\nTool: {intent.tool_name}\n"
                        "Complete arguments:\n" + json.dumps(intent.arguments, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
                        + "\nReply yes to authorize only this operation and these exact arguments; reply no to reject.")
        if changed:
            self._event("invalidated", "Previous consent invalidated", "Operation or payload changed; a new prompt requires a fresh affirmative.", intent)
        self._event("prompt", "Exact payload presented", self._prompt, intent)
        return self._result("awaiting_confirmation", "Fresh customer confirmation is required.")

    def respond(self, text: str, *, prompt_id: str | None) -> GateResult:
        if not isinstance(text, str):
            raise TypeError("Customer response must be text")
        if self._halted:
            return self._halt_result()
        self._authorized = None
        if self._active is None or self._active.operation_id in self._ledger:
            return self._result("no_pending_operation", "No unattempted operation is awaiting consent.")
        if self._rejected:
            return self._result("rejected", "Rejected operation must be proposed again with a fresh prompt.")
        if prompt_id is None or prompt_id != self._prompt_id:
            self._event("blocked", "Stale consent blocked", "Response is not bound to the active prompt.", self._active)
            return self._result("stale_consent", "A fresh answer to the current prompt is required.")
        classification = classify_response(text)
        if classification == "REJECT":
            self._rejected = True
            self._event("rejected", "Customer rejected mutation", text, self._active)
            return self._result("rejected", "Customer rejection blocks dispatch.")
        if classification != "AFFIRM":
            self._event("blocked", "Ambiguous response blocked", text, self._active)
            return self._result("ambiguous", "An unambiguous affirmative to the exact prompt is required.")
        self._authorized = (self._active.operation_id, self._active.fingerprint, self._prompt_id)
        self._event("authorized", "Fresh exact-payload consent", "Customer affirmed the active prompt.", self._active)
        return self._result("authorized", "The exact operation is authorized for one dispatch attempt.")

    def dispatch(self, intent: MutationIntent) -> GateResult:
        if not isinstance(intent, MutationIntent):
            raise TypeError("A typed MutationIntent is required")
        if self._halted:
            return self._halt_result()
        if intent.tool_name not in self._registry:
            self._authorized = None
            self._event("blocked", "Unknown tool blocked", intent.tool_name, intent)
            return self._result("blocked_unknown_tool", "Tool is not in the explicit mutation registry.")
        if intent.operation_id in self._ledger:
            self._event("blocked", "Repeat dispatch blocked", "Operation already attempted; no retry is allowed.", intent)
            return self._result("already_attempted", "Operation already attempted; inspect prior outcome instead of retrying.")
        if intent != self._active:
            self.propose(intent)
            self._event("blocked", "Unconfirmed dispatch blocked", "The newly proposed payload has no consent.", intent)
            return self._result("awaiting_confirmation", "Payload or operation changed; fresh confirmation is required.")
        expected = (intent.operation_id, intent.fingerprint, self._prompt_id)
        if self._authorized != expected:
            self._event("blocked", "Unconfirmed dispatch blocked", "No matching fresh affirmative is available.", intent)
            return self._result("rejected" if self._rejected else "awaiting_confirmation", "Dispatch blocked: exact-payload consent is absent.")
        # Consume consent and write the actual attempt BEFORE entering tool code.
        self._authorized = None
        record = {"id": intent.operation_id, "tool_name": intent.tool_name,
                  "arguments": intent.arguments, "fingerprint": intent.fingerprint, "outcome": "invoking"}
        self._ledger[intent.operation_id] = record
        self._dispatches.append(record)
        self._event("dispatch_started", "Tool invocation started", "One attempt recorded before calling the registered tool.", intent)
        try:
            result = self._registry[intent.tool_name](intent.arguments)
        except Exception as error:
            self._halted = True
            record["outcome"] = "uncertain"
            self._event("dispatch_uncertain", "Outcome uncertain; retry stopped", f"{type(error).__name__}: {error}", intent)
            return self._result("uncertain", "Tool raised after invocation began. The attempt is consumed; do not retry.", dispatched=True)
        if isinstance(result, UncertainMutationResult):
            self._halted = True
            record["outcome"] = "uncertain"
            self._event("dispatch_uncertain", "Outcome uncertain; retry stopped", result.detail, intent)
            return self._result("uncertain", result.detail + " Attempt consumed; do not retry.", dispatched=True)
        record["outcome"] = "succeeded"
        self._event("dispatch_succeeded", "Tool invocation succeeded", "Registered tool returned successfully.", intent)
        return self._result("succeeded", "Exact confirmed mutation dispatched once.", dispatched=True, result=result)

    def _halt_result(self) -> GateResult:
        detail = "A prior mutation has an uncertain outcome. This gate is stopped; reconcile externally before starting a new gate."
        self._event("blocked", "Uncertain outcome stops further mutation", detail, self._active)
        return self._result("uncertain", detail)

    def snapshot(self) -> dict[str, Any]:
        pending = self._active.to_dict() if self._active and self._active.operation_id not in self._ledger else None
        return json.loads(json.dumps({"status": self.status, "pending": pending,
            "confirmation": {"status": self.status, "prompt": self._prompt,
                             "fingerprint": self._active.fingerprint if self._active else None,
                             "prompt_id": self._prompt_id},
            "dispatches": self._dispatches, "events": self._events}, allow_nan=False))
