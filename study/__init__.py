"""Offline study contracts; no actor, model, benchmark-data, or network import."""
from .gate import GateControl, StudyGate, classify_native_result, classify_response
from .intents import IntentBuild, MaterialFact, MutationIntent, ObservationRef, build_intent, canonical_json
from .registry import BENCHMARK_SHA, ToolRegistry, ToolSpec

__all__ = ["BENCHMARK_SHA", "GateControl", "IntentBuild", "MaterialFact", "MutationIntent",
           "ObservationRef", "StudyGate", "ToolRegistry", "ToolSpec", "build_intent",
           "canonical_json", "classify_native_result", "classify_response"]
