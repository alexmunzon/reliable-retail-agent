"""Outcome-informed offline prototype, not imported by any runtime gate.

Extends finite v3 grammar with unqualified operation-directed imperatives.
Language recognition alone never establishes authority to dispatch.
"""
from __future__ import annotations

import re

from .consent import TERMINAL_MARKER, classify_response as classify_v3

CLASSIFIER_VERSION = 'whole-response-assent-offline-candidate-v1'

_OPERATION = r'(?:this|the) (?:entire |whole )?operation'
_DETAILS = r'(?:these|the) (?:exact )?details'
_PAYLOAD = rf'{_OPERATION}(?: (?:with|and) {_DETAILS})?'
_APPROVE = rf'(?:please )?approve {_PAYLOAD}'
_CONFIRM_APPROVE = rf'i confirm to approve {_PAYLOAD}'
# These are finite immediate commands, meaningful only at the current prompt.
# Bare references to old confirmations and arbitrary frustration are excluded.
_IMPERATIVE = r'(?:please )?do it(?: now)?(?:,? please)?'
_CORE = rf'(?:yes(?:,|\.|!)? (?:{_APPROVE}|{_CONFIRM_APPROVE}|{_IMPERATIVE})|{_APPROVE}|{_CONFIRM_APPROVE})'
_COURTESY = r'(?:thank you(?: very much)?|thanks)'
_AFFIRM = re.compile(rf'{_CORE}(?:[.!]? |, ){_COURTESY}[.!]?|{_CORE}[.!]?')


def classify_response(text: str, *, allow_terminal_marker: bool = False) -> str:
    """Whole-response classification only; no stripping mixed customer content."""
    # Retain base type checks, strict marker rules, and all existing outcomes.
    baseline = classify_v3(text, allow_terminal_marker=allow_terminal_marker)
    if baseline in {'AFFIRM', 'REJECT'}:
        return baseline
    stripped = text.strip()
    if TERMINAL_MARKER in stripped:
        if not allow_terminal_marker or stripped.count(TERMINAL_MARKER) != 1 or not stripped.endswith(TERMINAL_MARKER):
            return baseline
        stripped = stripped[:-len(TERMINAL_MARKER)].strip()
    normalized = ' '.join(stripped.casefold().split())
    return 'AFFIRM' if _AFFIRM.fullmatch(normalized) else baseline
