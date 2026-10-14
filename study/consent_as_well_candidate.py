"""Unevaluated offline follow-up for the observed v4 additive-assent miss.

No runtime imports this module. Language recognition does not establish
freshness, customer identity, scope or authority to dispatch.
"""
from __future__ import annotations

import re

from .consent import TERMINAL_MARKER
from .consent_candidate import classify_response as classify_v4

CLASSIFIER_VERSION = "whole-response-additive-assent-offline-v1"

# Match a complete current-details approval, not an arbitrary suffix removal.
_CORE = r"(?:yes,? )?i approve (?:these|the) (?:exact )?details as well"
_COURTESY = r"(?:thank you(?: very much)?|thanks)"
_ADDITIVE = re.compile(rf"(?:{_CORE}[.!]?|{_CORE}(?:[.!]? |, ){_COURTESY}[.!]?)")


def classify_response(text: str, *, allow_terminal_marker: bool = False) -> str:
    """Recognize the narrow new form; preserve the frozen classifier otherwise."""
    previous = classify_v4(text, allow_terminal_marker=allow_terminal_marker)
    if previous != "AMBIGUOUS" or TERMINAL_MARKER in text:
        return previous
    normalized = " ".join(text.casefold().split())
    return "AFFIRM" if _ADDITIVE.fullmatch(normalized) else previous
