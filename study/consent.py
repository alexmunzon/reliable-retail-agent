"""Finite whole-response assent grammar; prospective development tuning only.

This parses language, never establishes prompt freshness or payload authority.
"""
from __future__ import annotations

import re

CLASSIFIER_VERSION = "whole-response-assent-v3-development"
TERMINAL_MARKER = "###STOP###"

# Enumerated referents authorize all details of the currently displayed intent.
# No interpolated item/address/price text and no arbitrary affirmative prefix.
_SCOPE = (
    r"(?:this|the) (?:entire |whole )?operation"
    r"|(?:these|the) (?:exact )?details"
    r"|all (?:of )?these (?:exact )?details"
    r"|(?:this|the) (?:entire |whole )?operation and (?:all (?:of )?)?(?:these|the) (?:exact )?details"
    r"|everything (?:exactly )?as (?:shown|described)"
)
_APPROVAL = rf"i (?:approve|confirm|authorize) (?:{_SCOPE})"
_PROCEED = rf"(?:please )?proceed (?:with (?:{_SCOPE})|(?:exactly )?as (?:shown|described))"
_CORE = rf"(?:yes(?:,? (?:{_APPROVAL}|{_PROCEED}))?|confirm|i confirm|approved|proceed|{_APPROVAL}|{_PROCEED})"
_COURTESY = r"(?:thank you(?: very much)?|thanks)"
# A courtesy is a complete suffix; its separator cannot introduce new content.
_AFFIRM = re.compile(rf"{_CORE}(?:[.!]? |, ){_COURTESY}[.!]?|{_CORE}[.!]?")
_REJECT = frozenset({"no", "no.", "cancel", "cancel.", "reject", "do not proceed", "don't proceed"})
_CHANGE = re.compile(r"\b(change|instead|actually|but|except|correction|replace|different|only|unless)\b")


def classify_response(text: str, *, allow_terminal_marker: bool = False) -> str:
    """Classify the full reply, allowing one terminal sentinel only by opt-in.

    The opt-in is for a caller that has established this is an actual native
    simulator response, not for generic customer text. A marker is not assent:
    the complete preceding reply must independently match the finite grammar.
    """
    if type(text) is not str:
        raise TypeError("Customer response must be text")
    if type(allow_terminal_marker) is not bool:
        raise TypeError("Terminal-marker protocol option must be boolean")
    stripped = text.strip()
    if TERMINAL_MARKER in stripped:
        if not allow_terminal_marker or stripped.count(TERMINAL_MARKER) != 1 or not stripped.endswith(TERMINAL_MARKER):
            return "AMBIGUOUS"
        stripped = stripped[:-len(TERMINAL_MARKER)].strip()
    normalized = " ".join(stripped.casefold().split())
    if _AFFIRM.fullmatch(normalized):
        return "AFFIRM"
    if normalized in _REJECT:
        return "REJECT"
    if _CHANGE.search(normalized):
        return "CHANGE"
    return "AMBIGUOUS"
