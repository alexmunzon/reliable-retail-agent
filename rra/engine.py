"""Deterministic comparison of scripted ungated and gated local dispatch."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .gate import ConfirmationGate, MutationIntent, UncertainMutationError
from .scenarios import airline_arguments, get_scenario, initial_state, list_scenarios, retail_arguments
from .version import development_metadata


class DemoSession:
    def __init__(self, scenario_id: str):
        self.scenario = get_scenario(scenario_id)
        self._initialize()

    def _initialize(self) -> None:
        self.step_index = 0
        self.current_step = None
        self._before = initial_state(self.scenario["domain"])
        self._states = {lane: deepcopy(self._before) for lane in ("baseline", "gated")}
        disclaimer = "Scripted synthetic fixture. Baseline is illustrative ungated dispatch, not a measured ChatReActAgent run."
        self._conversations = {lane: [{"role": "system", "content": disclaimer}] for lane in self._states}
        self._baseline_events: list[dict] = []
        self._baseline_dispatches: list[dict] = []
        self._baseline_status = "idle"
        self._tool_name = "change_reservation_flight" if self.scenario["domain"] == "airline" else "change_order_address"
        arguments = airline_arguments() if self.scenario["domain"] == "airline" else retail_arguments()
        self._intent = MutationIntent(self._tool_name, arguments, "operation-1")
        self._gate = ConfirmationGate({self._tool_name: self._gated_tool})
        self._old_prompt_id: str | None = None
        self._seen_prompt_ids: set[str] = set()

    def _mutate(self, lane: str, arguments: dict[str, Any]) -> dict:
        state = self._states[lane]
        if self.scenario["domain"] == "airline":
            if arguments["reservation_id"] != state["reservation"]["reservation_id"]:
                raise ValueError("Reservation mismatch")
            for key in ("flight_id", "cabin", "travel_date"):
                state["reservation"][key] = arguments[key]
            state["charges_usd"] += arguments["additional_cost_usd"]
        else:
            if arguments["order_id"] != state["order"]["order_id"]:
                raise ValueError("Order mismatch")
            state["order"]["address"] = deepcopy(arguments["address"])
        state["mutation_count"] += 1
        if self.scenario["id"] == "uncertain-outcome":
            raise UncertainMutationError("Synthetic timeout after the local state changed")
        return {"ok": True, "mutation_count": state["mutation_count"]}

    def _gated_tool(self, arguments: dict) -> dict:
        return self._mutate("gated", arguments)

    def _say(self, role: str, content: str, lane: str | None = None) -> None:
        for name in ([lane] if lane else self._conversations):
            self._conversations[name].append({"role": role, "content": content})

    def _baseline_event(self, kind: str, title: str, detail: str) -> None:
        self._baseline_events.append({"seq": len(self._baseline_events) + 1, "type": kind,
                                      "title": title, "detail": detail, "fingerprint": self._intent.fingerprint})

    def _show_prompt(self) -> None:
        confirmation = self._gate.snapshot()["confirmation"]
        if confirmation["prompt_id"] and confirmation["prompt_id"] not in self._seen_prompt_ids:
            self._seen_prompt_ids.add(confirmation["prompt_id"])
            self._say("assistant", confirmation["prompt"], "gated")

    def _dispatch_baseline(self) -> None:
        if self._baseline_dispatches:
            return
        record = {"id": self._intent.operation_id, "tool_name": self._intent.tool_name,
                  "arguments": self._intent.arguments, "fingerprint": self._intent.fingerprint, "outcome": "invoking"}
        self._baseline_dispatches.append(record)
        self._baseline_event("dispatch_started", "Scripted ungated invocation", "Attempt logged before invoking the independent baseline tool.")
        try:
            self._mutate("baseline", self._intent.arguments)
        except Exception as error:
            record["outcome"] = "uncertain"
            self._baseline_status = "uncertain"
            self._baseline_event("dispatch_uncertain", "Tool outcome uncertain", str(error))
        else:
            record["outcome"] = "succeeded"
            self._baseline_status = "succeeded"
            self._baseline_event("dispatch_succeeded", "Local mutation succeeded", "Illustrative scripted baseline dispatched without an enforcing confirmation boundary.")

    def step(self) -> dict:
        if self.step_index >= 4:
            return self.snapshot()
        scenario_id = self.scenario["id"]
        if self.step_index == 0:
            title, description = "Customer request", "Load independent synthetic state for both lanes."
            request = ("Please change my reservation to flight SYN-FL-202 on January 15, 2027, economy."
                       if self.scenario["domain"] == "airline" else "Please change my pending order's address to 18 Example Lane, apartment 2A.")
            self._say("customer", request)
            self._baseline_status = "request_received"
            self._baseline_event("request", "Synthetic request received", "This scripted request is not itself an affirmative to a generated confirmation prompt.")
        elif self.step_index == 1:
            title, description = "Present exact payload", "The gate generates a complete confirmation prompt from the actual tool arguments."
            self._gate.propose(self._intent)
            self._show_prompt()
            self._say("assistant", "Scripted actor proposes " + self._tool_name + "; the baseline has no enforcing dispatch gate.", "baseline")
            self._baseline_status = "proposed"
            self._baseline_event("proposed", "Mutation proposed", "All tool arguments are shown in the operation preview.")
            if scenario_id == "stale-consent":
                self._old_prompt_id = self._gate.snapshot()["confirmation"]["prompt_id"]
        elif self.step_index == 2:
            title, description = "Customer response", "Evaluate whether the response authorizes this exact active operation."
            prompt_id = self._gate.snapshot()["confirmation"]["prompt_id"]
            if scenario_id == "missing-confirmation":
                self._say("system", "No customer answer has arrived. The scripted actor tries to continue.")
            elif scenario_id == "changed-payload":
                text = "Yes, but change the apartment to 4B."
                self._say("customer", text)
                self._gate.respond(text, prompt_id=prompt_id)
                self._intent = MutationIntent(self._tool_name, retail_arguments(changed=True), "operation-1")
                self._gate.propose(self._intent)
                self._show_prompt()
            elif scenario_id == "stale-consent":
                self._intent = MutationIntent(self._tool_name, self._intent.arguments, "operation-2")
                self._gate.propose(self._intent)
                self._show_prompt()
                self._say("system", "Replay a recorded 'yes' from the previous prompt; no fresh customer answer exists.")
                self._gate.respond("yes", prompt_id=self._old_prompt_id)
            else:
                response = "no" if scenario_id == "rejection" else "maybe" if scenario_id == "ambiguous-response" else "yes"
                self._say("customer", response)
                self._gate.respond(response, prompt_id=prompt_id)
            self._baseline_status = "ready_to_dispatch"
        else:
            title, description = "Attempt actual tool dispatch", "Compare real local calls and state changes in the two independent lanes."
            self._dispatch_baseline()
            result = self._gate.dispatch(self._intent)
            self._show_prompt()
            self._say("assistant", result.detail, "gated")
            if scenario_id == "uncertain-outcome":
                self._gate.dispatch(self._intent)
                self._say("system", "A duplicate dispatch attempt was blocked; the uncertain operation was not retried.", "gated")
        self.current_step = {"title": title, "description": description}
        self.step_index += 1
        return self.snapshot()

    def run(self) -> dict:
        while self.step_index < 4:
            self.step()
        return self.snapshot()

    def respond(self, text: str, prompt_id: str | None = None) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("A nonempty customer response is required")
        if self.step_index < 4:
            raise ValueError("Finish the four-step script before adding a fresh response")
        self._say("customer", text)
        # Never attach delayed or unbound text to whichever prompt is current.
        # Trusted callers must carry the prompt identity the customer saw.
        result = self._gate.respond(text, prompt_id=prompt_id)
        if result.status == "authorized":
            result = self._gate.dispatch(self._intent)
        self._say("assistant", result.detail, "gated")
        return self.snapshot()

    def reset(self) -> dict:
        self._initialize()
        return self.snapshot()

    def snapshot(self) -> dict:
        gated = self._gate.snapshot()
        baseline_pending = None if self._baseline_dispatches or self.step_index == 0 else self._intent.to_dict()
        baseline = {"label": "Scripted ungated baseline", "status": self._baseline_status,
                    "pending": baseline_pending,
                    "confirmation": {"status": "not_enforced", "prompt": None, "fingerprint": None, "prompt_id": None},
                    "conversation": self._conversations["baseline"], "dispatches": self._baseline_dispatches,
                    "before_state": self._before, "after_state": self._states["baseline"], "events": self._baseline_events}
        gated.update({"label": "Exact-payload confirmation gate", "conversation": self._conversations["gated"],
                      "before_state": self._before, "after_state": self._states["gated"]})
        return deepcopy({"scenario": self.scenario, "development": development_metadata(),
                         "step_index": self.step_index, "total_steps": 4,
                         "complete": self.step_index >= 4, "current_step": self.current_step,
                         "lanes": {"baseline": baseline, "gated": gated}})
