"""Trusted facts and audited public arithmetic from actor-visible native events.

No data-loader, task, reference-action, environment, or network access is used.
Missing, unrelated, stale, or incomplete inputs remain unresolved.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import Any

from .intents import DerivedRef, FORMULA_FACTS, FORMULA_TOOLS, MaterialFact, ObservationRef, canonical_json, evaluate_formula
from .gate import classify_response
from .registry import ToolRegistry


@dataclass(frozen=True)
class _VisibleEvent:
    event_id: str
    sequence: int
    source: str
    _action_json: str
    observation: str
    _parsed_json: str | None

    @property
    def action(self) -> dict[str, Any]:
        return json.loads(self._action_json)

    @property
    def value(self) -> Any:
        return json.loads(self._parsed_json) if self._parsed_json is not None else None


def _finite_money(value: Any) -> bool:
    if type(value) not in (int, float, str):
        return False
    try:
        amount = Decimal(str(value))
        return amount.is_finite()
    except InvalidOperation:
        return False


def _nonnegative_money(value: Any) -> bool:
    return _finite_money(value) and Decimal(str(value)) >= 0


class VisibleStore:
    """Monotone visible event store and conservative, related-object extractor.

    The native adapter records events, never the actor/model. record() accepts
    actual native action/observation pairs, not facts chosen by a model.
    """

    def __init__(self, domain: str, registry: ToolRegistry | None = None):
        self.registry = registry or ToolRegistry()
        self.registry.names(domain)
        self.domain = domain
        self._events: list[_VisibleEvent] = []
        self._by_id: dict[str, _VisibleEvent] = {}

    def _append(self, event: _VisibleEvent) -> None:
        if type(event.event_id) is not str or not event.event_id or event.event_id in self._by_id:
            raise ValueError("Unique nonempty visible event ID required")
        if type(event.sequence) is not int or event.sequence < 0 or (self._events and event.sequence <= self._events[-1].sequence):
            raise ValueError("Visible event sequences must strictly increase")
        self._events.append(event)
        self._by_id[event.event_id] = event

    def record(self, event_id: str, sequence: int, action: dict[str, Any], observation: str) -> None:
        if type(action) is not dict or set(action) != {"name", "arguments"}:
            raise ValueError("Actual native action requires exactly name and arguments")
        if type(observation) is not str:
            raise TypeError("Actual native observation text required")
        canonical_json(action)
        spec = self.registry.get(self.domain, action["name"])
        if spec.effect not in {"read", "calculation", "mutation"}:
            raise ValueError("Only visible information-producing native tools are recorded here")
        self.registry.validate_arguments(self.domain, action["name"], action["arguments"])
        parsed = None
        try:
            # Calculation observations are text, preserving original monetary digits.
            value = observation if spec.effect == "calculation" else json.loads(observation)
            parsed = canonical_json(value)
        except (ValueError, TypeError):
            pass  # Native error/unparseable return is still an actual event.
        self._append(_VisibleEvent(event_id, sequence, f"tool:{self.domain}/{action['name']}",
                                   canonical_json(action), observation, parsed))

    def record_user(self, event_id: str, sequence: int, text: str) -> None:
        if type(text) is not str:
            raise TypeError("Actual native customer text required")
        self._append(_VisibleEvent(event_id, sequence, "user", canonical_json({}), text, canonical_json(text)))

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple({"event_id": event.event_id, "sequence": event.sequence, "source": event.source,
                      "action": event.action, "observation": event.observation}
                     for event in self._events)

    @staticmethod
    def _at(value: Any, field_path: str) -> Any:
        if field_path == "$":
            return value
        if not field_path.startswith("/"):
            raise ValueError("Store evidence uses $ or an RFC 6901 JSON pointer")
        for part in field_path[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if type(value) is list:
                if not part.isdigit() or (part != "0" and part.startswith("0")):
                    raise ValueError("Invalid evidence array index")
                value = value[int(part)]
            elif type(value) is dict:
                value = value[part]
            else:
                raise ValueError("Evidence path does not resolve")
        return value

    def resolve(self, ref: ObservationRef | DerivedRef) -> Any:
        if isinstance(ref, DerivedRef):
            if ref.proposed_action["domain"] != self.domain:
                raise ValueError("Derived reference domain mismatch")
            self.registry.validate_arguments(self.domain, ref.proposed_action["tool_name"], ref.proposed_action["arguments"])
            for item in ref.inputs:
                self.resolve(item)
            latest_inputs = self._formula_inputs(ref.proposed_action["tool_name"], ref.proposed_action["arguments"])
            if latest_inputs is None or latest_inputs != ref.inputs:
                raise ValueError("Derived inputs are not the latest related visible observations")
            if evaluate_formula(ref.formula_id, ref.proposed_action["arguments"], latest_inputs) != ref.value:
                raise ValueError("Derived result failed public formula re-evaluation")
            return ref.value
        if not isinstance(ref, ObservationRef):
            raise TypeError("Typed visible reference required")
        event = self._by_id.get(ref.event_id)
        if event is None or event.sequence != ref.sequence or event.source != ref.source or event._parsed_json is None:
            raise ValueError("Reference does not match a stored actual native event")
        try:
            value = self._at(event.value, ref.field_path)
        except (KeyError, IndexError, ValueError):
            raise ValueError("Evidence path does not resolve") from None
        if canonical_json(value) != canonical_json(ref.value):
            raise ValueError("Reference value differs from the actual visible event")
        return deepcopy(value)

    def _fact(self, name: str, event: _VisibleEvent, path: str) -> MaterialFact | None:
        if event._parsed_json is None:
            return None
        try:
            value = self._at(event.value, path)
        except (KeyError, IndexError, ValueError):
            return None
        ref = ObservationRef(event.event_id, event.source, path, value, event.sequence)
        self.resolve(ref)
        return MaterialFact(name, ref)

    def _entity(self, key: str, identity: str) -> _VisibleEvent | None:
        names = ({"get_order_details", "cancel_pending_order", "modify_pending_order_address", "modify_pending_order_items",
                  "modify_pending_order_payment", "return_delivered_order_items", "exchange_delivered_order_items"}
                 if key == "order_id" else
                 {"get_reservation_details", "book_reservation", "cancel_reservation", "update_reservation_flights", "update_reservation_baggages", "update_reservation_passengers"})
        for event in reversed(self._events):
            if event.source == "user" or event.action.get("name") not in names:
                continue
            if event.action["name"] == "book_reservation":
                if type(event.value) is not dict or event.value.get(key) != identity:
                    continue
                return event if event.value.get("user_id") == event.action["arguments"]["user_id"] else None
            if event.action["arguments"].get(key) != identity:
                continue
            # An error or contradictory latest response does not resurrect older data.
            value = event.value
            return event if type(value) is dict and value.get(key) == identity else None
        return None

    def _ref(self, event: _VisibleEvent, path: str = "$") -> ObservationRef:
        ref = ObservationRef(event.event_id, event.source, path, self._at(event.value, path), event.sequence)
        self.resolve(ref)
        return ref

    def _product(self, product_id: str) -> _VisibleEvent | None:
        for event in reversed(self._events):
            if event.action.get("name") != "get_product_details" or event.action["arguments"].get("product_id") != product_id:
                continue
            value = event.value
            return event if type(value) is dict and value.get("product_id") == product_id else None
        return None

    def _searched_flight(self, flight_number: str, date: str, cabin: str) -> ObservationRef | None:
        seen_queries: set[str] = set()
        for event in reversed(self._events):
            name = event.action.get("name")
            if name not in {"search_direct_flight", "search_onestop_flight"}:
                continue
            query = canonical_json(event.action)
            if query in seen_queries:
                continue
            seen_queries.add(query)
            requested_date = event.action["arguments"]["date"]
            value = event.value
            if type(value) is not list:
                # This query cannot supply a price and supersedes its own older
                # results, without poisoning an unrelated route's observations.
                continue
            rows = [(f"/{i}", row) for i, row in enumerate(value)] if name == "search_direct_flight" else [
                (f"/{i}/{j}", row) for i, group in enumerate(value) if type(group) is list for j, row in enumerate(group)]
            for path, row in rows:
                if type(row) is not dict or row.get("flight_number") != flight_number:
                    continue
                row_date = requested_date if name == "search_direct_flight" else row.get("date")
                if name == "search_direct_flight" and row.get("date", requested_date) != requested_date:
                    continue
                if row_date != date:
                    continue
                prices = row.get("prices")
                return self._ref(event, path) if type(prices) is dict and _finite_money(prices.get(cabin)) else None
            # Never resurrect an older result for this exact search query.
        return None

    def _formula_inputs(self, tool_name: str, args: dict[str, Any]) -> tuple[ObservationRef, ...] | None:
        """Ordered, related, latest native inputs; no hidden reads or guesses."""
        if self.domain == "retail":
            order = self._entity("order_id", args["order_id"]) if "order_id" in args else None
            if not order:
                return None
            items = order.value.get("items")
            if type(items) is not list:
                return None
            if tool_name in {"modify_pending_order_items", "exchange_delivered_order_items"}:
                if len(args["item_ids"]) != len(args["new_item_ids"]):
                    return None
                refs = [self._ref(order)]
                for old_id, new_id in zip(args["item_ids"], args["new_item_ids"]):
                    index = next((i for i, row in enumerate(items) if type(row) is dict and row.get("item_id") == old_id), None)
                    if index is None or not _finite_money(items[index].get("price")) or type(items[index].get("product_id")) is not str:
                        return None
                    product = self._product(items[index]["product_id"])
                    if not product or type(product.value.get("variants")) is not dict:
                        return None
                    variant = product.value["variants"].get(new_id)
                    if type(variant) is not dict or not _finite_money(variant.get("price")):
                        return None
                    escaped = new_id.replace("~", "~0").replace("/", "~1")
                    refs.extend((self._ref(order, f"/items/{index}"), self._ref(product, f"/variants/{escaped}")))
                return tuple(refs)
            if tool_name == "return_delivered_order_items":
                refs = [self._ref(order)]
                used: set[int] = set()
                for item_id in args["item_ids"]:
                    index = next((i for i, row in enumerate(items) if i not in used and type(row) is dict and row.get("item_id") == item_id), None)
                    if index is None or not _nonnegative_money(items[index].get("price")):
                        return None
                    used.add(index)
                    refs.append(self._ref(order, f"/items/{index}"))
                return tuple(refs)
            return None
        if tool_name == "book_reservation":
            refs = []
            for flight in args["flights"]:
                ref = self._searched_flight(flight["flight_number"], flight["date"], args["cabin"])
                if ref is None:
                    return None
                refs.append(ref)
            return tuple(refs)
        reservation = self._entity("reservation_id", args["reservation_id"]) if "reservation_id" in args else None
        if not reservation:
            return None
        if tool_name == "update_reservation_baggages":
            return (self._ref(reservation),) if type(reservation.value.get("nonfree_baggages")) is int else None
        if tool_name == "update_reservation_flights":
            value = reservation.value
            if type(value.get("flights")) is not list or type(value.get("passengers")) is not list or not value["passengers"] or type(value.get("cabin")) is not str:
                return None
            if any(type(row) is not dict or not _finite_money(row.get("price")) for row in value["flights"]):
                return None
            refs = [self._ref(reservation)]
            for flight in args["flights"]:
                old_index = next((i for i, row in enumerate(value["flights"]) if row.get("flight_number") == flight["flight_number"] and row.get("date") == flight["date"] and value["cabin"] == args["cabin"]), None)
                ref = self._ref(reservation, f"/flights/{old_index}") if old_index is not None else self._searched_flight(flight["flight_number"], flight["date"], args["cabin"])
                if ref is None:
                    return None
                refs.append(ref)
            return tuple(refs)
        return None

    def _derived(self, tool_name: str, args: dict[str, Any]) -> MaterialFact | None:
        formula_id = next((name for name, action in FORMULA_TOOLS.items() if action == (self.domain, tool_name)), None)
        if formula_id is None:
            return None
        inputs = self._formula_inputs(tool_name, args)
        if inputs is None:
            return None
        name = FORMULA_FACTS[formula_id]
        try:
            value = evaluate_formula(formula_id, args, inputs)
            ref = DerivedRef(formula_id, inputs, {"domain": self.domain, "tool_name": tool_name, "arguments": args}, value)
            self.resolve(ref)
        except ValueError:
            return None
        return MaterialFact(name, ref)

    def facts_for(self, tool_name: str, args: dict[str, Any]) -> tuple[MaterialFact, ...]:
        spec = self.registry.get(self.domain, tool_name)
        self.registry.validate_arguments(self.domain, tool_name, args)
        if spec.effect != "mutation":
            raise ValueError("Material confirmation facts are only requested for mutations")
        resolved: dict[str, MaterialFact] = {}
        key = "order_id" if self.domain == "retail" else "reservation_id"
        entity = self._entity(key, args[key]) if key in args else None
        if entity:
            user = self._fact("user_id", entity, "/user_id")
            if user and type(user.value) is str and user.value.strip():
                resolved["user_id"] = user
            if tool_name in {"cancel_pending_order", "cancel_reservation"}:
                payments = self._fact("refund_payments", entity, "/payment_history")
                payment_key = "payment_method_id" if self.domain == "retail" else "payment_id"
                if payments and type(payments.value) is list and payments.value and all(
                    type(row) is dict and type(row.get(payment_key)) is str and _finite_money(row.get("amount"))
                    for row in payments.value
                ):
                    resolved["refund_payments"] = payments
            if tool_name == "modify_pending_order_payment":
                history = entity.value.get("payment_history")
                if type(history) is list and len(history) == 1 and type(history[0]) is dict and history[0].get("transaction_type") == "payment":
                    original = self._fact("original_payment_id", entity, "/payment_history/0/payment_method_id")
                    charge = self._fact("charge_amount", entity, "/payment_history/0/amount")
                    if original and type(original.value) is str and original.value and charge and _nonnegative_money(charge.value):
                        resolved.update(original_payment_id=original, charge_amount=charge)
            if tool_name == "return_delivered_order_items" and len(args["item_ids"]) == 1:
                # One item needs no monetary aggregation/derivation.
                items = entity.value.get("items")
                if type(items) is list:
                    for index, item in enumerate(items):
                        if type(item) is dict and item.get("item_id") == args["item_ids"][0] and _nonnegative_money(item.get("price")):
                            refund = self._fact("refund_amount", entity, f"/items/{index}/price")
                            if refund:
                                resolved["refund_amount"] = refund
                            break
            aliases = {"cancel_reservation": {"flights": "/flights", "passengers": "/passengers"},
                       "update_reservation_flights": {"original_flights": "/flights", "passengers": "/passengers"},
                       "update_reservation_passengers": {"original_passengers": "/passengers"},
                       "update_reservation_baggages": {"original_nonfree_baggages": "/nonfree_baggages"}}
            for name, path in aliases.get(tool_name, {}).items():
                fact = self._fact(name, entity, path)
                if fact:
                    value = fact.value
                    fields = ({"flight_number", "date"} if "flights" in name else {"first_name", "last_name", "dob"})
                    if (name == "original_nonfree_baggages" and type(value) is int and value >= 0) or (
                        type(value) is list and value and all(type(row) is dict and fields <= row.keys() for row in value)
                    ):
                        resolved[name] = fact
            if tool_name == "cancel_reservation":
                # No semantic inference from arbitrary customer text. Resolve an
                # exact policy reason only when one reservation identity is visible.
                seen_ids = {event.action.get("arguments", {}).get("reservation_id") for event in self._events
                            if "reservation_id" in event.action.get("arguments", {})}
                seen_ids |= {event.value["reservation_id"] for event in self._events
                             if type(event.value) is dict and type(event.value.get("reservation_id")) is str}
                # Recorded native user observations carry the simulator's outer
                # terminal protocol. Pure assent cannot erase an exact reason;
                # this does not authorize dispatch after native done.
                latest_user = next((event for event in reversed(self._events) if event.source == "user" and classify_response(event.observation, allow_terminal_marker=True) != "AFFIRM"), None)
                if seen_ids == {args["reservation_id"]} and latest_user and latest_user.observation in {"change of plan", "airline cancelled flight", "other reasons"}:
                    reason = self._fact("cancellation_reason", latest_user, "$")
                    if reason:
                        resolved["cancellation_reason"] = reason
        derived = self._derived(tool_name, args)
        if derived:
            resolved[derived.name] = derived
        # Return only actually resolvable required fields, in frozen registry order.
        # build_intent reports NEED_DETAILS for the rest. No implicit native read.
        return tuple(resolved[name] for name in spec.display_fields if name in resolved)
