"""Immutable exact native arguments and provenance-bound readable display facts."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import json
from typing import Any, Iterable

from .registry import EFFECTS, ToolRegistry

RENDERER_VERSION = "paper-era-renderer-v2-derived"
FORMULA_VERSION = "public-decimal-formulas-v1"
FORMULA_TOOLS = {
    "retail.modify_item_difference": ("retail", "modify_pending_order_items"),
    "retail.exchange_item_difference": ("retail", "exchange_delivered_order_items"),
    "retail.return_item_total": ("retail", "return_delivered_order_items"),
    "airline.booking_total": ("airline", "book_reservation"),
    "airline.flight_difference": ("airline", "update_reservation_flights"),
    "airline.baggage_charge": ("airline", "update_reservation_baggages"),
}
FORMULA_FACTS = {
    "retail.modify_item_difference": "price_difference",
    "retail.exchange_item_difference": "price_difference",
    "retail.return_item_total": "refund_amount",
    "airline.booking_total": "total_price",
    "airline.flight_difference": "price_difference",
    "airline.baggage_charge": "charge_amount",
}


def canonical_json(value: Any) -> str:
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
                    raise ValueError("JSON keys must be strings")
                check(child)
            return
        raise ValueError(f"Unsupported JSON type: {type(item).__name__}")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True, init=False)
class ObservationRef:
    event_id: str
    source: str
    field_path: str
    sequence: int
    _value_json: str

    def __init__(self, event_id: str, source: str, field_path: str, value: Any, sequence: int):
        allowed = {"user"} | {f"tool:{domain}/{name}" for domain, names in EFFECTS.items() for name in names if names[name][0] in {"read", "calculation", "mutation"}}
        if not event_id or type(event_id) is not str or source not in allowed:
            raise ValueError("Evidence requires an event ID and an allowed visible user/tool source")
        if type(field_path) is not str or not field_path or type(sequence) is not int or sequence < 0:
            raise ValueError("Evidence requires a field path and nonnegative sequence")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "field_path", field_path)
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "_value_json", canonical_json(value))

    @property
    def value(self) -> Any:
        return json.loads(self._value_json)

    def to_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "source": self.source, "field_path": self.field_path,
                "value": self.value, "sequence": self.sequence}


@dataclass(frozen=True)
class MaterialFact:
    name: str
    evidence: ObservationRef | DerivedRef

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name or not isinstance(self.evidence, (ObservationRef, DerivedRef)):
            raise ValueError("Material facts require a name and visible observation reference")
        if isinstance(self.evidence, DerivedRef) and self.name != FORMULA_FACTS[self.evidence.formula_id]:
            raise ValueError("Derived formula cannot be relabeled as a different material fact")

    @property
    def value(self) -> Any:
        return self.evidence.value

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "evidence": self.evidence.to_dict()}


def _money(value: Any) -> Decimal:
    if type(value) not in (int, float, str):
        raise ValueError("A visible exact numeric amount is required")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("A visible exact numeric amount is required") from None
    if not amount.is_finite():
        raise ValueError("Nonfinite monetary amount")
    return amount


def evaluate_formula(formula_id: str, args: dict[str, Any], inputs: Iterable[ObservationRef]) -> str:
    """Allowlisted public monetary arithmetic only; never a policy/eligibility engine.

    VisibleStore separately establishes related/latest native event provenance.
    This evaluator does no reads and never evaluates arbitrary expressions.
    """
    refs = tuple(inputs)
    values = [ref.value for ref in refs]
    with localcontext() as context:
        context.prec = max(80, 2 * (len(canonical_json(values)) + len(canonical_json(args))))
        try:
            if formula_id in {"retail.modify_item_difference", "retail.exchange_item_difference"}:
                if len(values) != 1 + 2 * len(args["item_ids"]):
                    raise ValueError("Incomplete paired item inputs")
                total = sum((_money(values[2 + 2 * i]["price"]) - _money(values[1 + 2 * i]["price"])
                             for i in range(len(args["item_ids"]))), Decimal(0))
                if formula_id == "retail.exchange_item_difference":
                    total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
            elif formula_id == "retail.return_item_total":
                if len(values) != 1 + len(args["item_ids"]):
                    raise ValueError("Incomplete return item inputs")
                total = sum((_money(item["price"]) for item in values[1:]), Decimal(0))
            elif formula_id == "airline.booking_total":
                if len(values) != len(args["flights"]):
                    raise ValueError("Incomplete booking flight inputs")
                total = sum((_money(row["prices"][args["cabin"]]) for row in values), Decimal(0)) * len(args["passengers"])
                total += Decimal(30) * len(args["passengers"]) if args["insurance"] == "yes" else Decimal(0)
                total += Decimal(50) * args["nonfree_baggages"]
            elif formula_id == "airline.flight_difference":
                reservation = values[0]
                if len(values) != 1 + len(args["flights"]):
                    raise ValueError("Incomplete flight change inputs")
                reservation_sources = {f"tool:airline/{name}" for name in ("get_reservation_details", "book_reservation", "cancel_reservation", "update_reservation_flights", "update_reservation_baggages", "update_reservation_passengers")}
                new_total = sum((_money(row["price"]) if refs[i + 1].source in reservation_sources
                                 else _money(row["prices"][args["cabin"]]) for i, row in enumerate(values[1:])), Decimal(0))
                old_total = sum((_money(row["price"]) for row in reservation["flights"]), Decimal(0))
                total = (new_total - old_total) * len(reservation["passengers"])
            elif formula_id == "airline.baggage_charge":
                if len(values) != 1 or type(values[0]["nonfree_baggages"]) is not int:
                    raise ValueError("Incomplete baggage inputs")
                total = Decimal(50) * max(0, args["nonfree_baggages"] - values[0]["nonfree_baggages"])
            else:
                raise ValueError("Unknown monetary formula")
        except (KeyError, TypeError, IndexError, InvalidOperation) as error:
            raise ValueError("Incomplete or invalid public formula inputs") from error
        if not total.is_finite():
            raise ValueError("Nonfinite derived amount")
        return str(total)


@dataclass(frozen=True, init=False)
class DerivedRef:
    """Explicit derivation, never masquerading as a native calculate observation."""
    formula_id: str
    formula_version: str
    inputs: tuple[ObservationRef, ...]
    _action_json: str
    _value_json: str

    def __init__(self, formula_id: str, inputs: Iterable[ObservationRef], proposed_action: dict[str, Any],
                 value: str, formula_version: str = FORMULA_VERSION):
        inputs = tuple(inputs)
        if formula_id not in FORMULA_TOOLS or formula_version != FORMULA_VERSION:
            raise ValueError("Unknown formula ID/version")
        if any(not isinstance(ref, ObservationRef) for ref in inputs):
            raise TypeError("Derived inputs must be ordered native ObservationRefs")
        if type(proposed_action) is not dict or set(proposed_action) != {"domain", "tool_name", "arguments"}:
            raise ValueError("Exact proposed native action reference required")
        if (proposed_action["domain"], proposed_action["tool_name"]) != FORMULA_TOOLS[formula_id]:
            raise ValueError("Formula/domain/native-tool mismatch")
        if any(not ref.source.startswith(f"tool:{proposed_action['domain']}/") for ref in inputs):
            raise ValueError("Derived inputs require same-domain native tool observations")
        expected = evaluate_formula(formula_id, proposed_action["arguments"], inputs)
        if type(value) is not str or value != expected:
            raise ValueError("Derived result differs from recorded public formula inputs")
        object.__setattr__(self, "formula_id", formula_id)
        object.__setattr__(self, "formula_version", formula_version)
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "_action_json", canonical_json(proposed_action))
        object.__setattr__(self, "_value_json", canonical_json(value))

    @property
    def proposed_action(self) -> dict[str, Any]:
        return json.loads(self._action_json)

    @property
    def value(self) -> str:
        return json.loads(self._value_json)

    @property
    def source(self) -> str:
        return f"derived:{self.formula_id}@{self.formula_version}"

    @property
    def sequence(self) -> int:
        return max((ref.sequence for ref in self.inputs), default=0)

    @property
    def field_path(self) -> str:
        return "$"

    @property
    def event_id(self) -> str:
        identity = {"formula_id": self.formula_id, "formula_version": self.formula_version,
                    "inputs": [ref.to_dict() for ref in self.inputs], "proposed_action": self.proposed_action, "value": self.value}
        return "derived:" + sha256(canonical_json(identity).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "derived", "formula_id": self.formula_id, "formula_version": self.formula_version,
                "inputs": [ref.to_dict() for ref in self.inputs], "proposed_action": self.proposed_action,
                "event_id": self.event_id, "source": self.source, "field_path": self.field_path,
                "value": self.value, "sequence": self.sequence}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DerivedRef:
        ref = cls(payload["formula_id"], [ObservationRef(**item) for item in payload["inputs"]],
                  payload["proposed_action"], payload["value"], payload["formula_version"])
        if canonical_json(ref.to_dict()) != canonical_json(payload):
            raise ValueError("Invalid derived reference fields/identity")
        return ref


@dataclass(frozen=True, init=False)
class MutationIntent:
    intent_id: str
    revision: int
    _payload: str

    def __init__(self, intent_id: str, revision: int, payload: dict[str, Any]):
        if type(intent_id) is not str or not intent_id or type(revision) is not int or revision < 1:
            raise ValueError("Stable nonempty intent_id and positive revision required")
        object.__setattr__(self, "intent_id", intent_id)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "_payload", canonical_json(payload))

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self._payload)

    @property
    def arguments(self) -> dict[str, Any]:
        return self.payload["arguments"]

    @property
    def domain(self) -> str:
        return self.payload["domain"]

    @property
    def tool_name(self) -> str:
        return self.payload["tool_name"]

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json({"intent_id": self.intent_id, "revision": self.revision,
                                      **self.payload}).encode()).hexdigest()

    @property
    def native_fingerprint(self) -> str:
        return sha256(canonical_json({"domain": self.domain, "tool_name": self.tool_name,
                                      "arguments": self.arguments}).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"intent_id": self.intent_id, "revision": self.revision, "fingerprint": self.fingerprint,
                **self.payload}


@dataclass(frozen=True)
class IntentBuild:
    status: str
    intent: MutationIntent | None = None
    missing_fields: tuple[str, ...] = ()
    detail: str = ""


def build_intent(domain: str, tool_name: str, arguments: dict[str, Any], *, intent_id: str,
                 revision: int, facts: Iterable[MaterialFact] = (), registry: ToolRegistry | None = None) -> IntentBuild:
    registry = registry or ToolRegistry()
    spec = registry.get(domain, tool_name)
    if spec.effect != "mutation":
        raise ValueError("Only explicitly protected mutations have confirmation intents")
    canonical_json(arguments)
    registry.validate_arguments(domain, tool_name, arguments)
    facts = tuple(facts)
    if any(not isinstance(fact, MaterialFact) for fact in facts):
        raise TypeError("Typed material facts required")
    by_name = {fact.name: fact for fact in facts}
    if len(by_name) != len(facts):
        raise ValueError("Duplicate material fact names")
    if set(by_name) - set(spec.display_fields):
        raise ValueError("Unknown material display fields")
    missing = tuple(name for name in spec.display_fields if name not in by_name)
    if missing:
        return IntentBuild("NEED_DETAILS", missing_fields=missing, detail="Collect missing material facts through ordinary visible tools or customer clarification.")
    for fact in facts:
        if isinstance(fact.evidence, DerivedRef) and fact.evidence.proposed_action != {"domain": domain, "tool_name": tool_name, "arguments": arguments}:
            raise ValueError("Derived fact is bound to a different proposed native action")
        if fact.evidence.source.startswith("tool:") and not fact.evidence.source.startswith(f"tool:{domain}/"):
            raise ValueError("Cross-domain material evidence")
        value = fact.value
        if fact.name in {"price_difference", "refund_amount", "charge_amount", "total_price"}:
            if _money(value) < 0 and fact.name != "price_difference":
                raise ValueError("Unsigned charge/refund/total amount required")
        elif fact.name in {"user_id", "original_payment_id", "cancellation_reason"}:
            if type(value) is not str or not value.strip():
                raise ValueError(f"{fact.name}: nonempty visible text required")
        elif fact.name == "original_nonfree_baggages":
            if type(value) is not int or value < 0:
                raise ValueError("Visible original baggage count required")
        elif fact.name in {"flights", "original_flights", "passengers", "original_passengers", "refund_payments"}:
            if type(value) is not list or not value or any(type(row) is not dict for row in value):
                raise ValueError(f"{fact.name}: nonempty visible records required")
            keys = ({"flight_number", "date"} if "flights" in fact.name else
                    {"first_name", "last_name", "dob"} if "passengers" in fact.name else
                    {"amount", "payment_method_id" if domain == "retail" else "payment_id"})
            if any(not keys <= row.keys() for row in value):
                raise ValueError(f"{fact.name}: incomplete visible records")
            if fact.name == "refund_payments":
                for row in value:
                    _money(row["amount"])
    if "new_item_ids" in arguments and len(arguments["item_ids"]) != len(arguments["new_item_ids"]):
        return IntentBuild("NEED_DETAILS", missing_fields=("paired_item_lists",), detail="Old and replacement item lists must have equal length to render each pair.")
    if tool_name == "book_reservation" and _money(by_name["total_price"].value) != sum((_money(row["amount"]) for row in arguments["payment_methods"]), Decimal(0)):
        return IntentBuild("NEED_DETAILS", missing_fields=("consistent_total_price",), detail="Visible total contradicts the proposed payment allocations; collect or correct the details.")
    # Values remain proposals/observations; no inference, hidden lookup, or injected native kwargs.
    details = [f"Proposed {tool_name.replace('_', ' ')} ({domain})."]
    for name in spec.schema["properties"]:
        details.append(f"Proposed {name.replace('_', ' ')}: {canonical_json(arguments[name])}")
    if "new_item_ids" in arguments:
        details += [f"Item pair {index + 1}: {canonical_json(old)} -> {canonical_json(new)}"
                    for index, (old, new) in enumerate(zip(arguments["item_ids"], arguments["new_item_ids"]))]
    for name in spec.display_fields:
        fact = by_name[name]
        ref = fact.evidence
        details.append(f"Visible {name.replace('_', ' ')}: {canonical_json(fact.value)} [event {ref.event_id}, {ref.source}, {ref.field_path}, sequence {ref.sequence}]")
        if isinstance(ref, DerivedRef):
            details.append(f"Derived using public formula {ref.formula_id} ({ref.formula_version}); exact native arguments are the proposed inputs. Ordered observed inputs:")
            details.extend(f"Input {index + 1}: event {item.event_id}, {item.source}, {item.field_path}, sequence {item.sequence}; exact value {canonical_json(item.value)}"
                           for index, item in enumerate(ref.inputs))
    if "price_difference" in by_name:
        amount = _money(by_name["price_difference"].value)
        details.append(f"Observed price difference direction: {'charge' if amount > 0 else 'refund' if amount < 0 else 'no charge or refund'}; amount: {abs(amount)}. This is not a verified entitlement or final tool-validated price.")
    if tool_name == "modify_pending_order_payment":
        details.append("The displayed charge amount is charged to the proposed method and refunded to the displayed original method.")
    if tool_name in {"cancel_pending_order", "cancel_reservation"}:
        details.append("Cancellation refunds go to the displayed original payment allocations; this gate does not decide cancellation eligibility.")
        if tool_name == "cancel_reservation":
            details.append("The native cancellation reverses each signed original allocation: a positive original payment becomes a refund, and a negative original refund becomes a charge. Amounts and payment IDs above are preserved exactly.")
    if tool_name == "return_delivered_order_items":
        details.append("Request return and refund to the proposed payment method. A follow-up email supplies return instructions; the displayed amount is not a verified refund entitlement.")
    if tool_name in {"modify_pending_order_items", "exchange_delivered_order_items"}:
        details.append("Confirm this is the complete list of items, including duplicates. This item modification/exchange is a one-shot operation; include every intended item now.")
    if tool_name == "modify_pending_order_items":
        details.append("After this item change the order cannot be modified or cancelled again through the pending-order tools.")
    if tool_name == "exchange_delivered_order_items":
        details.append("Exchange is requested; follow-up return instructions arrive by email. No new order is required.")
    if tool_name == "send_certificate":
        details.append("Credit the proposed certificate amount to the proposed customer. Confirmation is a conservative study extension; an absent confirmation is not automatically a native-policy violation.")
    details.append("Exact native arguments:\n" + json.dumps(arguments, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False))
    details.append("Reply yes to approve this entire operation and these exact details, or no to reject. A correction or condition requires a new proposal.")
    payload = {"domain": domain, "tool_name": tool_name, "arguments": arguments,
               "policy_version": spec.policy_version, "registry_version": spec.registry_version,
               "tool_source_sha256": spec.source_sha256, "renderer_version": RENDERER_VERSION,
               "confirmation_scope": spec.confirmation_scope,
               "material_facts": [by_name[name].to_dict() for name in spec.display_fields],
               "summary": "\n".join(details)}
    return IntentBuild("PREPARED", MutationIntent(intent_id, revision, payload))
