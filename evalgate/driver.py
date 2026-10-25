"""Execute actor-visible case scripts against one system under test (SUT).

Run only by evalgate.runner as `python -I driver.py` in a fresh process whose
import path holds the SUT tree alone. Scorer labels never reach this process.
Reads one JSON request on stdin and writes one JSON result on stdout.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import socket
import sys

PROTOCOL = "evalgate-driver-v1"
SUT_MODULES = ("rra.gate", "study.consent_current", "adapters.tau_dispatch")
DEFAULT_REGISTRY = ("modify_pending_order_address", "cancel_pending_order", "issue_store_credit")


def _load_simulator():
    path = Path(__file__).resolve().with_name("simulator.py")
    spec = importlib.util.spec_from_file_location("_evalgate_simulator", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _block_network():
    def refuse(*_args, **_kwargs):
        raise RuntimeError("Network access is disabled in the evaluation driver")
    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    socket.create_connection = refuse


def _decode(value):
    """JSON cannot carry NaN/Infinity; cases use explicit markers for them."""
    if isinstance(value, dict):
        if set(value) == {"$float"}:
            return float(value["$float"])
        return {key: _decode(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_decode(child) for child in value]
    return value


def _set_path(target: dict, path: str, value) -> None:
    keys = path.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value


class ProviderError(RuntimeError):
    """A provider error string; the gate cannot know whether an effect happened."""


def run_case(case: dict, intents: dict, gate_mod, tau_mod, sim_mod) -> dict:
    sim = sim_mod.RetailSimulator(case.get("provider", "healthy"))
    before = deepcopy(sim.state)
    sources = {name: _decode(deepcopy(spec["arguments"])) for name, spec in intents.items()}
    built: dict = {}
    steps: list[dict] = []

    def intent(name):
        if name not in built:
            spec = intents[name]
            built[name] = gate_mod.MutationIntent(spec["tool"], sources[name], spec["operation_id"])
        return built[name]

    def action(name):
        return sim_mod.Action(intents[name]["tool"], deepcopy(sources[name]))

    gate = adapter = env = None
    shown: list[dict] = []  # prompts actually returned to / displayed on the customer channel

    def gate_events():
        return deepcopy((gate.snapshot() if gate is not None else adapter.snapshot()["gate"])["events"])

    sim.observer = lambda: [{"type": e.get("type"), "fingerprint": e.get("fingerprint")} for e in gate_events()]
    if case["adapter"] == "gate":
        def make_tool(tool_name):
            def tool(arguments):
                result = sim.invoke(tool_name, arguments)
                if result is sim_mod.UNCERTAIN:
                    return gate_mod.UncertainMutationResult("Synthetic response lost after the effect.")
                if isinstance(result, str) and result.startswith("Error:"):
                    raise ProviderError(result)
                return result
            return tool
        gate = gate_mod.ConfirmationGate({name: make_tool(name) for name in case.get("registry", DEFAULT_REGISTRY)})
    else:
        env = sim_mod.TauEnvSimulator(sim)
        adapter = tau_mod.TauDispatchAdapter(env, sim_mod.Action)

    shown_prompt = None  # the prompt identity trusted orchestration displayed
    captured: dict = {}
    for index, step in enumerate(case["steps"]):
        op, record = step["op"], {"op": step["op"], "invocations_before": len(sim.invocations)}
        presented_before = len(env.respond_contents) if env is not None else 0
        try:
            if op == "build":
                intent(step["intent"])
            elif op == "mutate_source":
                intent(step["intent"])
                _set_path(sources[step["intent"]], step["path"], step["value"])
            elif op == "mutate_returned":
                returned = intent(step["intent"]).arguments
                _set_path(returned, step["path"], step["value"])
            elif op in ("propose", "dispatch"):
                result = getattr(gate, op)(intent(step["intent"]))
                record["status"] = result.status
                if result.prompt_id and result.status == "awaiting_confirmation":
                    shown_prompt = result.prompt_id
                    shown.append({"step": index, "intent": step["intent"], "text": result.prompt})
                if "capture" in step:
                    captured[step["capture"]] = result.prompt_id
            elif op == "respond":
                ref = step.get("prompt", "current")
                prompt_id = {"current": shown_prompt, "none": None, "forged": "prompt-forged-0"}.get(ref, captured.get(ref))
                record["status"] = gate.respond(step["text"], prompt_id=prompt_id).status
            elif op == "tau_confirm":
                env.queue_reply(step["reply"])
                spec = intents[step["intent"]]
                record["status"] = adapter.request_confirmation(action(step["intent"]), operation_id=spec["operation_id"]).status
                shown += [{"step": index, "intent": step["intent"], "text": text} for text in env.respond_contents[presented_before:]]
            elif op == "tau_dispatch":
                spec = intents[step["intent"]]
                record["status"] = adapter.dispatch(action(step["intent"]), operation_id=spec["operation_id"]).status
            elif op == "tau_respond":
                env.queue_reply(step["reply"])
                record["status"] = adapter.dispatch(sim_mod.Action("respond", {"content": step["text"]})).status
            elif op == "tau_read":
                tool, kwargs = sim_mod.read_request(step)
                result = adapter.dispatch(sim_mod.Action(tool, deepcopy(kwargs)))
                record["status"] = result.status
                record["read_ok"] = getattr(result.native_response, "observation", None) == sim.answer(tool, kwargs)
            elif op == "tau_call":
                record["status"] = adapter.dispatch(sim_mod.Action(step["tool"], deepcopy(step["kwargs"])),
                                                    operation_id=step.get("operation_id")).status
            else:
                raise KeyError(f"unknown step op {op}")
        except Exception as error:  # observed, then scored against allowed errors
            record["error"] = type(error).__name__
        record["invocations_after"] = len(sim.invocations)
        steps.append(record)

    if gate is not None:
        raw = {"gate_events": gate.snapshot()["events"]}
    else:
        snapshot = adapter.snapshot()
        raw = {"gate_events": snapshot["gate"]["events"], "actor_dispatches": snapshot["actor_dispatches"],
               "presented_prompts": env.respond_contents}
    sim.observer = None
    return {"case_id": case["case_id"], "steps": steps, "shown_prompts": shown, "invocations": sim.invocations,
            "reads": sim.reads, "state_before": before, "state_after": sim.state, "raw": raw}


def main() -> int:
    request = json.loads(sys.stdin.read())
    if request.get("protocol") != PROTOCOL:
        raise SystemExit("unsupported driver protocol")
    sut_root = Path(request["sut_root"]).resolve()
    _block_network()
    sim_mod = _load_simulator()
    sys.path.insert(0, str(sut_root))
    loaded = {}
    modules = {}
    for name in SUT_MODULES:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(sut_root):
            raise SystemExit(f"{name} loaded from outside the SUT tree: {path}")
        loaded[name] = {"file": str(path.relative_to(sut_root)), "sha256": sha256(path.read_bytes()).hexdigest()}
        modules[name] = module
    results = []
    for case in request["cases"]:
        try:
            results.append(run_case(case, request["intents"], modules["rra.gate"], modules["adapters.tau_dispatch"], sim_mod))
        except Exception as error:
            results.append({"case_id": case["case_id"], "crash": f"{type(error).__name__}: {error}"})
    classifier = getattr(modules["study.consent_current"], "CLASSIFIER_VERSION", "unknown")
    json.dump({"protocol": PROTOCOL, "loaded": loaded, "classifier_version": classifier, "results": results}, sys.stdout,
              sort_keys=True, ensure_ascii=False, allow_nan=False, default=repr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
