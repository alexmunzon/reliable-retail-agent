"""Public labels for the current scripted demo, separate from frozen studies."""
from study.consent_current import CLASSIFIER_VERSION

DEVELOPMENT_VERSION = "current-dev-v5"


def development_metadata() -> dict:
    return {"version": DEVELOPMENT_VERSION,
            "consent_classifier": CLASSIFIER_VERSION,
            "model_evaluated": False,
            "evidence": "scripted-synthetic"}
