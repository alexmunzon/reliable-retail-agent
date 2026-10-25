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
    return (usage["model_id"], usage["measurement"], tuple(usage["tokens"][c] for c in TOKEN_CATEGORIES),
            usage["observed_at"], usage["source"], usage["usage_semantics"])


def request_costs(usages: list[dict], receipts: list[dict], tables: list[dict], spans: dict, parents: dict,
                  evidence_issues: tuple | list = ()) -> dict:
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
    conflict_counts = {kind: len({_identity(u) for u in usages
                                 if u["measurement"] == kind and _identity(u) in conflicts})
                       for kind in ("measured", "estimated")}
    records, series = [], {"measured": [], "estimated": []}
    for usage in unique:
        est = estimate(usage, tables)
        row = {"record_id": usage["record_id"], "span_id": usage["span_id"], "provider": usage["provider"],
               "provider_request_id": usage["provider_request_id"], "source": usage["source"],
               "usage_semantics": usage["usage_semantics"], "emitter": dict(usage["emitter"]),
               "model_id": usage["model_id"], "measurement": usage["measurement"], "tokens": dict(usage["tokens"]),
               "observed_at": usage["observed_at"], "table_id": est["table_id"], "currency": est["currency"],
               "estimate": money(est["amount"]), "unknown_reason": est["reason"] if est["amount"] is None else None}
        records.append(row)
        series[usage["measurement"]].append((row, est))

    def total(rows, kind):
        unknown = conflict_counts[kind]
        known, reasons, tables_used = {}, (["conflicting_usage_identity"] if unknown else []), set()
        for row, est in rows:
            if est["amount"] is None:
                unknown += 1
                reasons.append(est["reason"])
                continue
            known[est["currency"]] = known.get(est["currency"], Decimal(0)) + est["amount"]
            tables_used.add(est["table_id"])
        complete = bool(rows) and unknown == 0 and not evidence_issues
        return {"usage_records": len(rows) + conflict_counts[kind], "unknown_usage_records": unknown,
                "known_subtotal": {c: money(v) for c, v in sorted(known.items())},
                "total": {c: money(v) for c, v in sorted(known.items())} if complete else None,
                "reason": None if complete else (reasons[0] if reasons else "telemetry_incomplete" if rows else "no_usage_records"),
                "evidence_complete": not evidence_issues and conflict_counts[kind] == 0,
                "pricing_tables": sorted(tables_used)}

    confirmed, receipt_seen, receipt_dupes, receipt_conflicts = {}, {}, 0, set()
    known_usage = {u["record_id"]: _identity(u) for u in usages if _identity(u) not in conflicts}
    rejected_receipts = 0
    for receipt in receipts:
        if receipt["usage_record_id"] not in known_usage:
            rejected_receipts += 1
            continue
        key = (receipt["source"], receipt["receipt_id"])
        if key in receipt_seen:
            previous = receipt_seen[key]
            if all(previous[k] == receipt[k] for k in ("usage_record_id", "amount", "currency")):
                receipt_dupes += 1
            else:
                receipt_conflicts.add(key)
            continue
        receipt_seen[key] = receipt
    accepted_receipts = {key: receipt for key, receipt in receipt_seen.items() if key not in receipt_conflicts}
    receipted_usage = {known_usage[r["usage_record_id"]] for r in accepted_receipts.values()}
    unreceipted = len(set(known_usage.values()) - receipted_usage)
    for receipt in accepted_receipts.values():
        confirmed[receipt["currency"]] = confirmed.get(receipt["currency"], Decimal(0)) + Decimal(receipt["amount"])
    confirmed = {c: money(v) for c, v in sorted(confirmed.items())}
    receipt_reason = ("conflicting_receipt" if receipt_conflicts else "unbound_receipt" if rejected_receipts
                      else "conflicting_usage_identity" if conflicts else "no_billing_receipts" if not accepted_receipts
                      else "missing_billing_receipts" if unreceipted else "telemetry_incomplete" if evidence_issues else None)

    rollup: dict[str, dict] = {}
    def rollup_usage(span_id, est, conflict=None):
        node, guard = span_id, 0
        while node is not None and node in spans and guard <= len(spans):
            bucket = rollup.setdefault(node, {"known": {}, "unknown": 0, "conflicts": set()})
            if est["amount"] is None:
                if conflict is None or conflict not in bucket["conflicts"]:
                    bucket["unknown"] += 1
                    if conflict is not None:
                        bucket["conflicts"].add(conflict)
            else:
                currency = est["currency"]
                bucket["known"][currency] = bucket["known"].get(currency, Decimal(0)) + est["amount"]
            node, guard = parents.get(node), guard + 1
    for row, est in series["measured"]:
        rollup_usage(row["span_id"], est)
    for usage in usages:
        if usage["measurement"] == "measured" and _identity(usage) in conflicts:
            rollup_usage(usage["span_id"], {"amount": None}, _identity(usage))

    def rollup_view(bucket):
        amounts = {c: money(v) for c, v in sorted(bucket["known"].items())}
        return {"known_by_currency": amounts, "currency": next(iter(amounts)) if len(amounts) == 1 else None,
                # Retain the v1 scalar only when no currency mixing is possible.
                "known": next(iter(amounts.values()), "0") if len(amounts) <= 1 else None,
                "unknown_usage_records": bucket["unknown"]}

    return {"usage": records, "duplicate_usage_identities": duplicates, "conflicting_usage_identities": len(conflicts),
            "incomplete_evidence_reasons": sorted(set(evidence_issues)),
            "estimated_charge": total(series["measured"], "measured"),
            "estimated_charge_from_estimated_tokens": total(series["estimated"], "estimated"),
            "confirmed_charge": confirmed, "confirmed_charge_total": confirmed if receipt_reason is None else None,
            "confirmed_charge_complete": receipt_reason is None, "confirmed_charge_unknown_reason": receipt_reason,
            "confirmed_receipts": len(accepted_receipts), "duplicate_receipts": receipt_dupes,
            "conflicting_receipts": len(receipt_conflicts), "unbound_receipts": rejected_receipts,
            "unreceipted_usage_identities": unreceipted,
            "span_rollup": {span: rollup_view(b) for span, b in rollup.items()},
            "measured_tokens": {c: sum(r["tokens"][c] for r, _ in series["measured"] if r["tokens"][c] is not None)
                                for c in TOKEN_CATEGORIES},
            "measured_tokens_missing": {c: sum(1 for r, _ in series["measured"] if r["tokens"][c] is None)
                                        for c in TOKEN_CATEGORIES}}
