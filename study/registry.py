"""Explicit effects and public-schema loading for the original paper-era pin.

Only tools/*.py and wiki.md are read. Native code is never imported or executed.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

BENCHMARK_SHA = "6f4b718037db619539b8b692060e6686f3f0dcc9"
REGISTRY_VERSION = "paper-era-effects-v1"
DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "vendor/tau-bench-paper-era"

# effect, native policy confirmation scope, required actor-visible material facts
_RETAIL = {
    "calculate": ("calculation", "none", ()),
    "cancel_pending_order": ("mutation", "native", ("user_id", "refund_payments")),
    "exchange_delivered_order_items": ("mutation", "native", ("user_id", "price_difference")),
    "find_user_id_by_email": ("read", "none", ()),
    "find_user_id_by_name_zip": ("read", "none", ()),
    "get_order_details": ("read", "none", ()),
    "get_product_details": ("read", "none", ()),
    "get_user_details": ("read", "none", ()),
    "list_all_product_types": ("read", "none", ()),
    "modify_pending_order_address": ("mutation", "native", ("user_id",)),
    "modify_pending_order_items": ("mutation", "native", ("user_id", "price_difference")),
    "modify_pending_order_payment": ("mutation", "native", ("user_id", "original_payment_id", "charge_amount")),
    "modify_user_address": ("mutation", "native", ()),
    "return_delivered_order_items": ("mutation", "native", ("user_id", "refund_amount")),
    "think": ("internal", "none", ()),
    "transfer_to_human_agents": ("control", "none", ()),
}
_AIRLINE = {
    "book_reservation": ("mutation", "native", ("total_price",)),
    "calculate": ("calculation", "none", ()),
    "cancel_reservation": ("mutation", "native", ("user_id", "cancellation_reason", "flights", "passengers", "refund_payments")),
    "get_reservation_details": ("read", "none", ()),
    "get_user_details": ("read", "none", ()),
    "list_all_airports": ("read", "none", ()),
    "search_direct_flight": ("read", "none", ()),
    "search_onestop_flight": ("read", "none", ()),
    "send_certificate": ("mutation", "conservative_extension", ()),
    "think": ("internal", "none", ()),
    "transfer_to_human_agents": ("control", "none", ()),
    "update_reservation_baggages": ("mutation", "native", ("user_id", "original_nonfree_baggages", "charge_amount")),
    "update_reservation_flights": ("mutation", "native", ("user_id", "original_flights", "passengers", "price_difference")),
    "update_reservation_passengers": ("mutation", "native", ("user_id", "original_passengers")),
}
EFFECTS = {"retail": _RETAIL, "airline": _AIRLINE}


@dataclass(frozen=True)
class ToolSpec:
    domain: str
    name: str
    effect: str
    confirmation_scope: str
    display_fields: tuple[str, ...]
    source: str
    source_sha256: str
    policy_version: str
    registry_version: str
    _schema_json: str

    @property
    def schema(self) -> dict[str, Any]:
        return json.loads(self._schema_json)


def _public_info(path: Path, name: str) -> dict[str, Any]:
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Attribute) and target.attr == "__info__"
            and isinstance(target.value, ast.Name) and target.value.id == name
            for target in node.targets
        ):
            info = ast.literal_eval(node.value)["function"]
            if info["name"] != name:
                raise ValueError(f"Schema/name mismatch: {name}")
            return info
    raise ValueError(f"No public schema: {name}")


class ToolRegistry:
    """Source-validated inventory; unknown tools fail closed, including prefixes."""

    def __init__(self, source_root: Path = DEFAULT_SOURCE):
        self._specs: dict[tuple[str, str], ToolSpec] = {}
        for domain, entries in EFFECTS.items():
            root = Path(source_root) / "tau_bench/envs" / domain
            policy_version = "sha256:" + sha256((root / "wiki.md").read_bytes()).hexdigest()
            for name, (effect, scope, fields) in entries.items():
                path = root / "tools" / (name + ".py")
                info = _public_info(path, name)
                self._specs[domain, name] = ToolSpec(
                    domain, name, effect, scope, fields,
                    f"tau_bench/envs/{domain}/tools/{name}.py", sha256(path.read_bytes()).hexdigest(),
                    policy_version, REGISTRY_VERSION,
                    json.dumps(info["parameters"], sort_keys=True, separators=(",", ":")),
                )
            tree = ast.parse((root / "tools/__init__.py").read_text())
            inventory = next(
                [item.id for item in node.value.elts]
                for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "tools" for t in node.targets)
            )
            self.validate_inventory(domain, inventory)

    def get(self, domain: str, name: str) -> ToolSpec:
        try:
            return self._specs[domain, name]
        except KeyError:
            raise ValueError(f"Unclassified tool: {domain}/{name}") from None

    def names(self, domain: str) -> frozenset[str]:
        if domain not in EFFECTS:
            raise ValueError(f"Unknown domain: {domain}")
        return frozenset(name for d, name in self._specs if d == domain)

    def validate_inventory(self, domain: str, names: Iterable[str]) -> None:
        actual = list(names)
        expected = self.names(domain)
        # Some actor implementations omit think; every other tool is mandatory.
        if len(actual) != len(set(actual)) or set(actual) not in (expected, expected - {"think"}):
            raise ValueError(f"Inventory mismatch for {domain}: missing={sorted(expected - set(actual) - {'think'})}, unknown={sorted(set(actual) - expected)}")

    def validate_arguments(self, domain: str, name: str, arguments: dict[str, Any]) -> None:
        validate_schema(arguments, self.get(domain, name).schema)


def validate_schema(value: Any, schema: dict[str, Any], path: str = "arguments") -> None:
    """Pinned JSON subset, with unknown fields rejected at project boundaries.

    No coercion, repair, business-eligibility validation, or native signature change.
    """
    kind = schema["type"]
    valid = {"object": type(value) is dict, "array": type(value) is list,
             "string": type(value) is str, "integer": type(value) is int,
             "number": type(value) in (int, float), "boolean": type(value) is bool}.get(kind, False)
    if not valid:
        raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: invalid enum value")
    if kind == "object":
        properties = schema["properties"]
        if set(value) - set(properties) or set(schema.get("required", ())) - set(value):
            raise ValueError(f"{path}: missing or unknown fields")
        for key, child in value.items():
            validate_schema(child, properties[key], f"{path}.{key}")
    elif kind == "array":
        for index, child in enumerate(value):
            validate_schema(child, schema["items"], f"{path}[{index}]")
