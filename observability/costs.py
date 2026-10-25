"""Measured usage, estimated charges and confirmed charges: three separate series, never merged.

Token categories are disjoint by definition (docs/observability/DATA_DICTIONARY.md): `input` excludes cache
reads and writes, `output` excludes reasoning. A usage record is one provider call's final totals.
estimate = sum(tokens[c] * price[c]) / 1,000,000, exact Decimal; a missing count, price or applicable table
makes the estimate unknown (None), never $0. Only a billing receipt is a confirmed charge.
"""
from __future__ import annotations

from decimal import Decimal
import re

from . import PRICING_VERSION
from .sidecar import CURRENCY, DECIMAL, LABEL, MODEL, TOKEN_CATEGORIES

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MILLION = Decimal(1_000_000)
TABLE_KEYS = {"pricing_version", "table_id", "effective_from", "currency", "unit", "synthetic", "source", "assumptions", "models"}


def validate_table(table) -> list[str]:
    if not isinstance(table, dict) or set(table) != TABLE_KEYS:
        return ["pricing.invalid_fields"]
    p = []
    if table["pricing_version"] != PRICING_VERSION:
        p.append("pricing.unknown_version")
    if not (isinstance(table["table_id"], str) and LABEL.fullmatch(table["table_id"])):
        p.append("pricing.invalid_table_id")
    if not (isinstance(table["effective_from"], str) and DATE.fullmatch(table["effective_from"])):
        p.append("pricing.invalid_effective_from")
    if not (isinstance(table["currency"], str) and CURRENCY.fullmatch(table["currency"])):
        p.append("pricing.invalid_currency")
    if table["unit"] != "per_1m_tokens":
        p.append("pricing.unsupported_unit")
    if type(table["synthetic"]) is not bool or not isinstance(table["source"], str) or len(table["source"]) > 200:
        p.append("pricing.invalid_provenance")
    if not (isinstance(table["assumptions"], list) and all(isinstance(a, str) and len(a) <= 200 for a in table["assumptions"])):
        p.append("pricing.invalid_assumptions")
    models = table["models"]
    if not isinstance(models, dict) or len(models) > 64:
        return p + ["pricing.invalid_models"]
    for model, prices in models.items():
        if not (MODEL.fullmatch(model) and isinstance(prices, dict) and "provider" in prices
                and set(prices) <= {"provider", *TOKEN_CATEGORIES}
                and all(isinstance(v, str) and DECIMAL.fullmatch(v) for k, v in prices.items() if k != "provider")):
            p.append("pricing.invalid_model_entry")
    return p


def money(value: Decimal | None) -> str | None:
    """Exact decimal string with no exponent; rounding happens only in the UI."""
    if value is None:
        return None
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "0E-12") else text


def select_table(tables: list[dict], model: str, provider: str, observed_at: str | None):
    """The table in force on the usage's UTC date for this model; ambiguity or absence is unknown."""
    if not observed_at:
        return None, "no_usage_time"
    day = observed_at[:10]
    candidates = [t for t in tables if t["effective_from"] <= day and model in t["models"]]
    if not candidates:
        return None, "model_not_priced"
    latest = max(t["effective_from"] for t in candidates)
    chosen = [t for t in candidates if t["effective_from"] == latest]
    if len({(t["currency"], str(sorted(t["models"][model].items()))) for t in chosen}) > 1:
        return None, "ambiguous_pricing"
    table = chosen[0]
    if table["models"][model]["provider"] != provider:
        return None, "model_not_priced"
    return table, None


def estimate(usage: dict, tables: list[dict]) -> dict:
    table, reason = select_table(tables, usage["model_id"], usage["provider"], usage["observed_at"])
    result = {"amount": None, "currency": None, "table_id": None, "reason": reason}
    if table is None:
        return result
    prices, total = table["models"][usage["model_id"]], Decimal(0)
    result.update(currency=table["currency"], table_id=table["table_id"])
    for category in TOKEN_CATEGORIES:
        count = usage["tokens"][category]
        if count is None:
            result["reason"] = "token_category_not_supplied"
            return result
        if count == 0:
            continue  # zero tokens cost zero whatever the price
        if category not in prices:
            result["reason"] = "category_not_priced"
            return result
        total += Decimal(count) * Decimal(prices[category]) / MILLION
    result["amount"] = total
    return result


def _identity(usage: dict) -> tuple:
    return (usage["provider"], usage["provider_request_id"]) if usage["provider_request_id"] else ("record", usage["record_id"])


def _content(usage: dict) -> tuple:
    return (usage["model_id"], usage["measurement"], tuple(usage["tokens"][c] for c in TOKEN_CATEGORIES))


def request_costs(usages: list[dict], receipts: list[dict], tables: list[dict], spans: dict, parents: dict) -> dict:
    """Count each provider call once, then derive per-span rollups (direct usage plus descendants)."""
    unique, seen, duplicates, conflicts = [], {}, 0, set()
    for usage in usages:
        key = _identity(usage)
        if key in seen:
            if _content(seen[key]) == _content(usage):
                duplicates += 1
            else:
                conflicts.add(key)
            continue
        seen[key] = usage
        unique.append(usage)
    unique = [u for u in unique if _identity(u) not in conflicts]
    records, series = [], {"measured": [], "estimated": []}
    for usage in unique:
        est = estimate(usage, tables)
        row = {"record_id": usage["record_id"], "span_id": usage["span_id"], "provider": usage["provider"],
               "model_id": usage["model_id"], "measurement": usage["measurement"], "tokens": dict(usage["tokens"]),
               "observed_at": usage["observed_at"], "table_id": est["table_id"], "currency": est["currency"],
               "estimate": money(est["amount"]), "unknown_reason": est["reason"] if est["amount"] is None else None}
        records.append(row)
        series[usage["measurement"]].append((row, est))

    def total(rows):
        known, unknown, currencies, reasons, tables_used = {}, 0, set(), [], set()
        for row, est in rows:
            if est["amount"] is None:
                unknown += 1
                reasons.append(est["reason"])
                continue
            known[est["currency"]] = known.get(est["currency"], Decimal(0)) + est["amount"]
            currencies.add(est["currency"])
            tables_used.add(est["table_id"])
        complete = bool(rows) and unknown == 0
        return {"usage_records": len(rows), "unknown_usage_records": unknown,
                "known_subtotal": {c: money(v) for c, v in sorted(known.items())},
                "total": {c: money(v) for c, v in sorted(known.items())} if complete else None,
                "reason": None if complete else ("no_usage_records" if not rows else reasons[0]),
                "pricing_tables": sorted(tables_used)}

    confirmed, receipt_seen, receipt_dupes = {}, set(), 0
    known_usage = {u["record_id"] for u in usages}
    rejected_receipts = 0
    for receipt in receipts:
        if receipt["usage_record_id"] not in known_usage:
            rejected_receipts += 1
            continue
        key = (receipt["source"], receipt["receipt_id"])
        if key in receipt_seen:
            receipt_dupes += 1
            continue
        receipt_seen.add(key)
        confirmed[receipt["currency"]] = confirmed.get(receipt["currency"], Decimal(0)) + Decimal(receipt["amount"])

    rollup: dict[str, dict] = {}
    for row, est in series["measured"]:
        node, guard = row["span_id"], 0
        while node is not None and node in spans and guard <= len(spans):
            bucket = rollup.setdefault(node, {"known": Decimal(0), "unknown": 0})
            if est["amount"] is None:
                bucket["unknown"] += 1
            else:
                bucket["known"] += est["amount"]
            node, guard = parents.get(node), guard + 1
    return {"usage": records, "duplicate_usage_identities": duplicates, "conflicting_usage_identities": len(conflicts),
            "estimated_charge": total(series["measured"]),
            "estimated_charge_from_estimated_tokens": total(series["estimated"]),
            "confirmed_charge": {c: money(v) for c, v in sorted(confirmed.items())},
            "confirmed_receipts": len(receipt_seen), "duplicate_receipts": receipt_dupes, "unbound_receipts": rejected_receipts,
            "span_rollup": {span: {"known": money(b["known"]), "unknown_usage_records": b["unknown"]} for span, b in rollup.items()},
            "measured_tokens": {c: sum(r["tokens"][c] for r, _ in series["measured"] if r["tokens"][c] is not None)
                                for c in TOKEN_CATEGORIES},
            "measured_tokens_missing": {c: sum(1 for r, _ in series["measured"] if r["tokens"][c] is None)
                                        for c in TOKEN_CATEGORIES}}
