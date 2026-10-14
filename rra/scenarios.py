"""Synthetic, scripted fixtures. No benchmark results are represented here."""
from copy import deepcopy


SCENARIOS = [
    {"id": "normal-confirmation", "title": "Fresh confirmation", "domain": "retail",
     "description": "A customer confirms the complete address-change payload.",
     "lesson": "Fresh consent to the exact operation allows one mutation."},
    {"id": "missing-confirmation", "title": "Missing confirmation", "domain": "retail",
     "description": "The scripted actor attempts an address change before the customer confirms.",
     "lesson": "The illustrative baseline dispatches; the gate waits for fresh consent."},
    {"id": "changed-payload", "title": "Changed payload", "domain": "retail",
     "description": "The customer says yes but requests a different apartment number.",
     "lesson": "Changing one argument invalidates consent and produces a complete new prompt."},
    {"id": "stale-consent", "title": "Stale consent", "domain": "retail",
     "description": "An affirmative from a previous operation is replayed against the current operation.",
     "lesson": "Even an identical payload needs consent bound to the current prompt and operation."},
    {"id": "airline-transfer", "title": "Airline domain transfer", "domain": "airline",
     "description": "The same gate confirms a synthetic reservation flight change.",
     "lesson": "The dispatch boundary transfers across domains; this is not an airline benchmark."},
    {"id": "rejection", "title": "Customer rejection", "domain": "retail",
     "description": "The customer rejects the proposed address change.",
     "lesson": "A negative response blocks dispatch."},
    {"id": "ambiguous-response", "title": "Ambiguous response", "domain": "retail",
     "description": "The customer responds with 'maybe' to the exact payload.",
     "lesson": "Ambiguous responses never count as affirmative consent."},
    {"id": "uncertain-outcome", "title": "Uncertain tool outcome", "domain": "retail",
     "description": "After valid consent, the local tool mutates state and raises a simulated timeout.",
     "lesson": "An uncertain invocation consumes its attempt; retries cannot repeat the mutation."},
]


def list_scenarios() -> list[dict]:
    return [dict(deepcopy(item), step_count=4) for item in SCENARIOS]


def get_scenario(scenario_id: str) -> dict:
    for item in list_scenarios():
        if item["id"] == scenario_id:
            return item
    raise ValueError(f"Unknown scenario: {scenario_id}")


def retail_arguments(*, changed: bool = False) -> dict:
    return {"order_id": "SYN-ORDER-1042", "address": {
        "street": "18 Example Lane", "apartment": "4B" if changed else "2A",
        "city": "Sampleton", "state": "CA", "postal_code": "00000", "country": "US"}}


def airline_arguments() -> dict:
    return {"reservation_id": "SYN-BOOKING-42", "flight_id": "SYN-FL-202",
            "cabin": "economy", "travel_date": "2027-01-15", "additional_cost_usd": 25,
            "payment_method_id": "SYN-CARD-42"}


def initial_state(domain: str) -> dict:
    if domain == "airline":
        return {"reservation": {"reservation_id": "SYN-BOOKING-42", "flight_id": "SYN-FL-101",
                                "cabin": "economy", "travel_date": "2027-01-14"},
                "charges_usd": 0, "mutation_count": 0}
    return {"order": {"order_id": "SYN-ORDER-1042", "status": "pending",
                      "address": {"street": "7 Original Road", "apartment": "1",
                                  "city": "Sampleton", "state": "CA", "postal_code": "00000", "country": "US"}},
            "mutation_count": 0}
