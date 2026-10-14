"""Deterministic research consent boundary, independent of actor/native runtime.

Controls are project decisions, never fabricated native tool observations.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
import re
from threading import RLock
from typing import Any, Callable, Mapping
from uuid import uuid4

from .intents import DerivedRef, MaterialFact, MutationIntent, ObservationRef, build_intent, canonical_json
from .registry import ToolRegistry

from .consent_current import CLASSIFIER_VERSION, classify_response


# Exact literals reviewed before the first write in the corresponding source.
# Dynamic templates below are separately audited and bound to proposed args.
NATIVE_RESULT_CLASSIFIER_VERSION = "pinned-pre-effect-v2"
_COMMON_ORDER_ERRORS = {"Error: order not found"}
PRE_EFFECT_ERRORS = {
    ("retail", "cancel_pending_order"): _COMMON_ORDER_ERRORS | {"Error: non-pending order cannot be cancelled", "Error: invalid reason"},
    ("retail", "modify_pending_order_address"): _COMMON_ORDER_ERRORS | {"Error: non-pending order cannot be modified"},
    ("retail", "modify_user_address"): {"Error: user not found"},
    ("retail", "modify_pending_order_items"): _COMMON_ORDER_ERRORS | {"Error: non-pending order cannot be modified", "Error: the number of items to be exchanged should match", "Error: payment method not found", "Error: insufficient gift card balance to pay for the new item"},
    ("retail", "exchange_delivered_order_items"): _COMMON_ORDER_ERRORS | {"Error: non-delivered order cannot be exchanged", "Error: the number of items to be exchanged should match", "Error: payment method not found", "Error: insufficient gift card balance to pay for the price difference"},
    ("retail", "modify_pending_order_payment"): _COMMON_ORDER_ERRORS | {"Error: non-pending order cannot be modified", "Error: payment method not found", "Error: there should be exactly one payment for a pending order", "Error: the new payment method should be different from the current one", "Error: insufficient gift card balance to pay for the order"},
    ("retail", "return_delivered_order_items"): _COMMON_ORDER_ERRORS | {"Error: non-delivered order cannot be returned", "Error: payment method not found", "Error: payment method should be either the original payment method or a gift card", "Error: some item not found"},
    ("airline", "book_reservation"): {"Error: user not found"},
    ("airline", "cancel_reservation"): {"Error: reservation not found"},
    ("airline", "update_reservation_flights"): {"Error: reservation not found", "Error: payment method not found", "Error: certificate cannot be used to update reservation", "Error: gift card balance is not enough"},
    ("airline", "update_reservation_baggages"): {"Error: reservation not found", "Error: payment method not found", "Error: certificate cannot be used to update reservation", "Error: gift card balance is not enough"},
    ("airline", "update_reservation_passengers"): {"Error: reservation not found", "Error: number of passengers does not match"},
    ("airline", "send_certificate"): {"Error: user not found"},
}


def _dynamic_pre_effect_error(domain: str, name: str, args: dict[str, Any], observation: str) -> bool:
    """Only exact returns proven to precede any shared-data write at the pin.

    No database inspection or error-prefix inference. Local/deepcopied candidate
    updates in these functions are not writes to native data. Exceptions bypass
    this return classifier entirely. See docs/NATIVE_REJECTION_AUDIT.md.
    """
    try:
        if domain == "retail" and name in {"modify_pending_order_items", "exchange_delivered_order_items"}:
            return (any(observation == f"Error: {item_id} not found" for item_id in args["item_ids"])
                    or any(observation == f"Error: new item {item_id} not found or available" for item_id in args["new_item_ids"]))
        if domain != "airline":
            return False
        if name in {"book_reservation", "update_reservation_flights"}:
            for flight in args["flights"]:
                number, date = flight["flight_number"], flight["date"]
                if observation in {f"Error: flight {number} not found",
                                   f"Error: flight {number} not found on date {date}",
                                   f"Error: flight {number} not available on date {date}",
                                   f"Error: not enough seats on flight {number}"}:
                    return True
        if name == "book_reservation":
            payments = args["payment_methods"]
            for payment in payments:
                payment_id = payment["payment_id"]
                if observation in {f"Error: payment method {payment_id} not found",
                                   f"Error: not enough balance in payment method {payment_id}"}:
                    return True
            # Exactly Python's original ordered sum and numeric formatting, not
            # a guessed total or Decimal reimplementation of native float math.
            amounts = [payment["amount"] for payment in payments]
            if not all(type(value) in (int, float) and math.isfinite(value) for value in amounts):
                return False
            paid = sum(amounts)
            numeric = r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:e[+-][0-9]+)?"
            match = re.fullmatch(rf"Error: payment amount does not add up, total price is ({numeric}), but paid ({numeric})", observation)
            if not match or match[2] != str(paid):
                return False
            total_text = match[1]
            total = float(total_text) if "." in total_text or "e" in total_text else int(total_text)
            return math.isfinite(total) and total >= 0 and total != paid and str(total) == total_text
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return False


def classify_native_result(intent: MutationIntent, result: Any) -> str:
    """Conservative pinned return-shape classification, with no name heuristics.

    This classifies a native RETURN only. An invocation that raises is always
    uncertain, even when exception text happens to equal an audited literal.
    """
    observation = getattr(result, "observation", result)
    if type(observation) is not str:
        return "UNCERTAIN"
    if observation in PRE_EFFECT_ERRORS.get((intent.domain, intent.tool_name), ()):
        return "REJECTED"
    args = intent.arguments
    if _dynamic_pre_effect_error(intent.domain, intent.tool_name, args, observation):
        return "REJECTED"
    if intent.tool_name == "send_certificate":
        expected = rf"Certificate certificate_(3221322|3221323|3221324) added to user {re.escape(args['user_id'])} with amount {re.escape(str(args['amount']))}\."
        return "SUCCEEDED" if re.fullmatch(expected, observation) else "UNCERTAIN"
    try:
        returned = json.loads(observation)
    except (ValueError, TypeError):
        return "UNCERTAIN"
    if type(returned) is not dict:
        return "UNCERTAIN"
    if intent.domain == "retail":
        key = "user_id" if intent.tool_name == "modify_user_address" else "order_id"
        if returned.get(key) != args[key]:
            return "UNCERTAIN"
        expected_status = {"cancel_pending_order": "cancelled", "modify_pending_order_address": "pending",
                           "modify_pending_order_payment": "pending", "modify_pending_order_items": "pending (item modified)",
                           "return_delivered_order_items": "return requested", "exchange_delivered_order_items": "exchange requested"}
        if intent.tool_name in expected_status and returned.get("status") != expected_status[intent.tool_name]:
            return "UNCERTAIN"
        if intent.tool_name in {"modify_user_address", "modify_pending_order_address"}:
            address = {name: args[name] for name in ("address1", "address2", "city", "state", "country", "zip")}
            if returned.get("address") != address:
                return "UNCERTAIN"
    elif intent.tool_name == "book_reservation":
        if returned.get("reservation_id") not in {"HATHAT", "HATHAU", "HATHAV"} or returned.get("user_id") != args["user_id"]:
            return "UNCERTAIN"
    elif returned.get("reservation_id") != args["reservation_id"]:
        return "UNCERTAIN"
    if intent.tool_name == "cancel_reservation" and returned.get("status") != "cancelled":
        return "UNCERTAIN"
    return "SUCCEEDED"


@dataclass(frozen=True)
class GateControl:
    status: str
    detail: str
    prompt: str | None = None
    prompt_id: str | None = None
    fingerprint: str | None = None
    dispatched: bool = False
    native_result: Any = None


class StudyGate:
    """One proposal and one immediate customer reply; no automatic write retry.

    Caller binds prompt/response metadata to actual native dialogue events and
    re-builds the current intent from latest visible evidence before dispatch.
    event_callback must durably complete ATTEMPTED before the native call. Its
    exception fails closed. In-memory state alone is not crash-safe.
    """

    def __init__(self, tools: Mapping[tuple[str, str], Callable[[dict[str, Any]], Any]], *,
                 registry: ToolRegistry | None = None,
                 event_callback: Callable[[str, dict[str, Any]], None] | None = None):
        self.registry = registry or ToolRegistry()
        self._tools = dict(tools)
        for (domain, name), fn in self._tools.items():
            if self.registry.get(domain, name).effect != "mutation" or not callable(fn):
                raise ValueError("Gate dispatch registry requires explicitly classified mutation callables")
        self._callback = event_callback
        self._callback_active = False
        self._lock = RLock()
        self._instance = uuid4().hex
        self._counter = 0
        self._active: MutationIntent | None = None
        self._prompt_id: str | None = None
        self._prompt_sequence: int | None = None
        self._response_sequence: int | None = None
        self._authorized: str | None = None
        self._halted = False
        self._halt_reason = "UNCERTAIN_MUTATION"
        self._blocked_reply = False
        self._attempts: list[dict[str, Any]] = []
        self._attempted_ids: set[str] = set()
        self._attempted_native: set[str] = set()
        self._revisions: dict[str, int] = {}
        self._events: list[dict[str, Any]] = []
        self.status = "IDLE"

    def _emit(self, event_type: str, payload: dict[str, Any]) -> bool:
        event = {"sequence": len(self._events) + 1, "event_type": event_type, **deepcopy(payload)}
        self._events.append(event)
        if self._callback:
            self._callback_active = True
            try:
                if self._callback(event_type, deepcopy(event)) is False:
                    raise OSError('Current gate evidence callback explicitly declined durability')
            except BaseException as error:
                self._authorized = None
                self._halted = True
                self._halt_reason = "PERSISTENCE_ERROR"
                self._events.append({"sequence": len(self._events) + 1, "event_type": "PERSISTENCE_ERROR", "error_type": type(error).__name__})
                return False
            finally:
                self._callback_active = False
        return True

    def _check_callback(self) -> None:
        if self._callback_active:
            raise RuntimeError("Persistence callbacks cannot re-enter gate mutation methods")

    def _control(self, status: str, detail: str, *, dispatched: bool = False, native_result: Any = None) -> GateControl:
        self.status = status
        return GateControl(status, detail, self._active.payload["summary"] if self._active else None,
                           self._prompt_id, self._active.fingerprint if self._active else None,
                           dispatched, native_result)

    def _halt(self) -> GateControl:
        return self._control(self._halt_reason, "Gate halted. Preserve and reconcile the journal; no automatic retry or further mutation is permitted.")

    def _validate(self, intent: MutationIntent) -> None:
        if not isinstance(intent, MutationIntent):
            raise TypeError("Typed immutable MutationIntent required")
        payload = intent.payload
        facts = []
        for fact in payload["material_facts"]:
            if set(fact) != {"name", "value", "evidence"}:
                raise ValueError("Unknown material fact fields")
            ref = (DerivedRef.from_dict(fact["evidence"]) if fact["evidence"].get("kind") == "derived"
                   else ObservationRef(**fact["evidence"]))
            if canonical_json(fact["value"]) != canonical_json(ref.value):
                raise ValueError("Displayed fact differs from its evidence value")
            facts.append(MaterialFact(fact["name"], ref))
        rebuilt = build_intent(intent.domain, intent.tool_name, intent.arguments,
                               intent_id=intent.intent_id, revision=intent.revision,
                               facts=facts, registry=self.registry)
        if rebuilt.intent != intent:
            raise ValueError("Intent is not a complete current gate-owned rendering")

    def propose(self, intent: MutationIntent, *, prompt_event_id: str, prompt_sequence: int) -> GateControl:
        with self._lock:
            self._check_callback()
            if self._halted:
                return self._halt()
            self._authorized = None
            try:
                self._validate(intent)
            except (ValueError, TypeError, KeyError):
                self.invalidate("Invalid replacement intent")
                raise
            if type(prompt_sequence) is not int or prompt_sequence < 0 or type(prompt_event_id) is not str or not prompt_event_id:
                raise ValueError("Actual prompt event ID and sequence required")
            if any(fact["evidence"]["sequence"] >= prompt_sequence for fact in intent.payload["material_facts"]):
                self.invalidate("Evidence must precede its displayed prompt")
                raise ValueError("Material evidence must precede the prompt event")
            if (intent.domain, intent.tool_name) not in self._tools:
                self.invalidate("No dispatch callable for this explicitly classified mutation")
                return self._halt() if self._halted else self._control("UNAVAILABLE_TOOL", "Protected tool has no registered native dispatch callable.")
            if intent.intent_id in self._attempted_ids or intent.native_fingerprint in self._attempted_native:
                self.invalidate("Previously attempted mutation cannot be re-proposed")
                return self._halt() if self._halted else self._control("ALREADY_ATTEMPTED", "A prior attempt consumed this intent or exact native payload; do not retry.")
            last_revision = self._revisions.get(intent.intent_id, 0)
            if intent.revision <= last_revision:
                self.invalidate("New prompt requires a new intent revision")
                return self._halt() if self._halted else self._control("INVALID_REVISION", "Every proposal/clarification requires a strictly newer revision for this intent ID.")
            if self._active:
                if not self._emit("INVALIDATED", {"intent_id": self._active.intent_id, "fingerprint": self._active.fingerprint, "reason": "Replacement proposal or prompt"}):
                    return self._halt()
            self._active = intent
            self._revisions[intent.intent_id] = intent.revision
            self._counter += 1
            self._prompt_id = f"{self._instance}:{self._counter}:{intent.fingerprint}"
            self._prompt_sequence = prompt_sequence
            self._response_sequence = None
            self._blocked_reply = False
            if not self._emit("PREPARED", {"intent": intent.to_dict(), "prompt_event_id": prompt_event_id,
                                            "prompt_sequence": prompt_sequence, "prompt_id": self._prompt_id,
                                            "classifier_version": CLASSIFIER_VERSION}):
                return self._halt()
            return self._control("WAITING_FOR_CUSTOMER", "Send the gate-owned complete prompt and bind only its immediate native customer response.")

    def respond(self, text: str, *, prompt_id: str, event_id: str, sequence: int, source: str) -> GateControl:
        with self._lock:
            self._check_callback()
            if self._halted:
                return self._halt()
            self._authorized = None
            if not self._active:
                return self._control("NO_PENDING", "No confirmation prompt is active.")
            if self._blocked_reply:
                return self._control("STALE_RESPONSE", "The immediate response slot was consumed; issue a fresh prompt revision.")
            self._blocked_reply = True
            if source != "user" or prompt_id != self._prompt_id or type(sequence) is not int or sequence != self._prompt_sequence + 1 or type(event_id) is not str or not event_id:
                self._emit("STALE_RESPONSE", {"intent_id": self._active.intent_id, "response_event_id": event_id,
                                              "response_sequence": sequence, "source": source})
                return self._halt() if self._halted else self._control("STALE_RESPONSE", "Only the immediate user-channel reply to the current prompt can authorize a write.")
            classification = classify_response(text, allow_terminal_marker=True)
            self._response_sequence = sequence
            if not self._emit("CUSTOMER_RESPONSE", {"intent_id": self._active.intent_id, "fingerprint": self._active.fingerprint,
                                                      "prompt_id": self._prompt_id, "response_event_id": event_id,
                                                      "response_sequence": sequence, "classification": classification,
                                                      "text": text, "source": source}):
                return self._halt()
            if classification == "AFFIRM":
                self._authorized = self._active.fingerprint
                return self._control("AUTHORIZED", "Fresh affirmative binds to this one frozen intent.")
            return self._control(classification, "No native write was dispatched. Return this classified customer response to ordinary actor control; clarification requires a fresh prompt revision.")

    def invalidate(self, reason: str) -> GateControl:
        with self._lock:
            self._check_callback()
            self._authorized = None
            if self._active:
                self._emit("INVALIDATED", {"intent_id": self._active.intent_id, "fingerprint": self._active.fingerprint, "reason": reason})
            self._active = None
            self._prompt_id = None
            self._prompt_sequence = None
            self._response_sequence = None
            self._blocked_reply = True
            return self._halt() if self._halted else self._control("INVALIDATED", reason)

    def restart(self) -> GateControl:
        """Invalidate approval while retaining all attempts and uncertainty halts."""
        return self.invalidate("Episode restart invalidates all pending confirmation")

    def dispatch(self, current_intent: MutationIntent) -> GateControl:
        with self._lock:
            self._check_callback()
            if self._halted:
                return self._halt()
            try:
                self._validate(current_intent)
            except (ValueError, TypeError, KeyError):
                self.invalidate("Invalid dispatch intent")
                raise
            if current_intent.intent_id in self._attempted_ids or current_intent.native_fingerprint in self._attempted_native:
                self._authorized = None
                return self._control("ALREADY_ATTEMPTED", "Prior attempt consumed approval and payload; no duplicate dispatch is allowed.")
            if current_intent != self._active:
                self.invalidate("Native payload, material evidence, version, or revision changed before dispatch")
                return self._halt() if self._halted else self._control("INVALIDATED", "Rebuild and present the current complete intent; previous assent is invalid.")
            if self._authorized != current_intent.fingerprint:
                return self._control("NOT_AUTHORIZED", "Matching fresh immediate customer affirmative is absent.")
            self._authorized = None
            self._attempted_ids.add(current_intent.intent_id)
            self._attempted_native.add(current_intent.native_fingerprint)
            record = {"dispatch_id": f"{self._instance}:dispatch:{len(self._attempts) + 1}",
                      "intent_id": current_intent.intent_id, "revision": current_intent.revision,
                      "fingerprint": current_intent.fingerprint, "domain": current_intent.domain,
                      "tool_name": current_intent.tool_name, "arguments": current_intent.arguments,
                      "prompt_id": self._prompt_id, "response_sequence": self._response_sequence,
                      "outcome": "ATTEMPTED", "native_call_started": False}
            self._attempts.append(record)
            # Callback completes BEFORE any side effect. Failure consumes local
            # approval and halts; record distinguishes a call that never started.
            if not self._emit("ATTEMPTED", record):
                record["outcome"] = "PERSISTENCE_ERROR"
                return self._halt()
            record["native_call_started"] = True
            try:
                native_result = self._tools[current_intent.domain, current_intent.tool_name](current_intent.arguments)
                outcome = classify_native_result(current_intent, native_result)
            except BaseException as error:
                outcome = "UNCERTAIN"
                native_result = None
                record["error_type"] = type(error).__name__
            record["outcome"] = outcome
            if outcome == "UNCERTAIN":
                self._halted = True
                self._halt_reason = "UNCERTAIN_MUTATION"
            if not self._emit(outcome, record):
                return self._control("PERSISTENCE_ERROR", "A native attempt occurred but its completion could not be persisted; preserve the local journal and do not retry.", dispatched=True, native_result=native_result)
            return self._control("UNCERTAIN_MUTATION" if outcome == "UNCERTAIN" else outcome,
                                 "Native attempt is consumed; no automatic retry. Outcome is separately journaled.",
                                 dispatched=True, native_result=native_result)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy({"status": self.status, "halted": self._halted,
                             "pending": self._active.to_dict() if self._active and self._active.intent_id not in self._attempted_ids else None,
                             "attempts": self._attempts, "events": self._events,
                             "classifier_version": CLASSIFIER_VERSION})
