"""Independent synthetic retail effects for evaluation.

Trusted harness code. It never imports the system under test; the scorer reads
its invocation log and state, not the gate's own account of what happened.
"""
from __future__ import annotations

from copy import deepcopy
from collections import deque
from dataclasses import dataclass
import json
from types import SimpleNamespace
from typing import Any

ADDRESS_FIELDS = ("address1", "address2", "city", "state", "country", "zip")
MUTATION_TOOLS = frozenset({"modify_pending_order_address", "cancel_pending_order", "issue_store_credit"})
READ_TOOLS = frozenset({"get_order_details", "get_user_details", "get_product_details", "list_all_product_types",
                        "find_user_id_by_email", "find_user_id_by_name_zip"})
# Read-only synthetic catalog for the non-order reads; never mutated, so not part of the scored state.
SYN_USER = {"user_id": "SYN-USER-1", "name": {"first_name": "Sam", "last_name": "Sample"}, "email": "sam@example.invalid",
            "address": {"zip": "00000"}, "orders": ["SYN-ORDER-1042", "SYN-ORDER-2077"]}
SYN_PRODUCT = {"product_id": "SYN-PRODUCT-1", "name": "Sample Lamp", "variants": {"SYN-ITEM-1": {"price": 25, "available": True}}}
PROVIDER_MODES = frozenset({"healthy", "raise_after_effect", "uncertain_after_effect", "error_before_effect"})


def read_request(step: dict) -> tuple[str, dict]:
    """The read a tau_read step schedules; older steps name only an order_id."""
    if "tool" in step:
        return step["tool"], step.get("kwargs", {})
    return "get_order_details", {"order_id": step["order_id"]}


def initial_state() -> dict:
    return {
        "orders": {
            "SYN-ORDER-1042": {"status": "pending", "store_credit_usd": 0, "address": {
                "address1": "7 Original Road", "address2": "Unit 1", "city": "Sampleton",
                "state": "CA", "country": "USA", "zip": "00000"}},
            "SYN-ORDER-2077": {"status": "pending", "store_credit_usd": 0, "address": {
                "address1": "9 Second Street", "address2": "", "city": "Sampleton",
                "state": "CA", "country": "USA", "zip": "00001"}},
        }
    }


class SimulatedProviderTimeout(RuntimeError):
    """The provider applied the effect, then the response was lost."""


class UncertainReturn:
    """Sentinel: the effect was applied but the provider returned no outcome."""


UNCERTAIN = UncertainReturn()


def json_safe(value: Any) -> Any:
    """Copy for evidence; values strict JSON cannot represent become markers."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return {"$unrepresentable": repr(value)}


class RetailSimulator:
    def __init__(self, mode: str = "healthy"):
        if mode not in PROVIDER_MODES:
            raise ValueError(f"Unknown provider mode: {mode}")
        self.mode = mode
        self.state = initial_state()
        self.invocations: list[dict] = []
        self.reads: list[dict] = []
        # Optional harness hook: snapshot of the gate's trace at the moment the
        # tool is entered, so "attempt recorded before the tool" is observable.
        self.observer = None

    def invoke(self, tool: str, arguments: Any) -> Any:
        # Record the attempt before any effect, exactly as received.
        record = {"tool": tool, "arguments": json_safe(arguments), "effect_applied": False, "outcome": "invoking"}
        if self.observer is not None:
            record["trace_at_invoke"] = self.observer()
        self.invocations.append(record)
        if self.mode == "error_before_effect":
            record["outcome"] = "error_before_effect"
            return "Error: synthetic provider rejected the request before any effect"
        try:
            error = self._apply(tool, arguments)
        except (KeyError, TypeError, AttributeError) as failure:
            record["outcome"] = "malformed"
            return f"Error: malformed arguments ({type(failure).__name__})"
        if error:
            record["outcome"] = "rejected"
            return error
        record["effect_applied"] = True
        if self.mode == "raise_after_effect":
            record["outcome"] = "effect_then_timeout"
            raise SimulatedProviderTimeout("synthetic timeout after the effect was applied")
        if self.mode == "uncertain_after_effect":
            record["outcome"] = "effect_then_lost_response"
            return UNCERTAIN
        record["outcome"] = "succeeded"
        return {"ok": True}

    def _apply(self, tool: str, arguments: dict) -> str | None:
        order = self.state["orders"].get(arguments["order_id"])
        if order is None:
            return "Error: order not found"
        if tool == "modify_pending_order_address":
            if order["status"] != "pending":
                return "Error: non-pending order cannot be modified"
            order["address"] = {field: deepcopy(arguments[field]) for field in ADDRESS_FIELDS}
        elif tool == "cancel_pending_order":
            if order["status"] != "pending":
                return "Error: non-pending order cannot be cancelled"
            order["status"] = "cancelled"
        elif tool == "issue_store_credit":
            order["store_credit_usd"] = order["store_credit_usd"] + arguments["amount_usd"]
        else:
            return "Error: unknown tool"
        return None

    def answer(self, tool: str, arguments: dict) -> str:
        """What the provider returns for a read right now; recording nothing."""
        user_found = "Error: user not found"
        if tool == "get_order_details":
            order = self.state["orders"].get(arguments.get("order_id"))
            return json.dumps(order, sort_keys=True) if order else "Error: order not found"
        if tool == "get_user_details":
            return json.dumps(SYN_USER, sort_keys=True) if arguments == {"user_id": SYN_USER["user_id"]} else user_found
        if tool == "find_user_id_by_email":
            return SYN_USER["user_id"] if arguments == {"email": SYN_USER["email"]} else user_found
        if tool == "find_user_id_by_name_zip":
            wanted = {**SYN_USER["name"], "zip": SYN_USER["address"]["zip"]}
            return SYN_USER["user_id"] if arguments == wanted else user_found
        if tool == "get_product_details":
            return json.dumps(SYN_PRODUCT, sort_keys=True) if arguments == {"product_id": SYN_PRODUCT["product_id"]} else "Error: product not found"
        if tool == "list_all_product_types":
            return json.dumps({SYN_PRODUCT["name"]: SYN_PRODUCT["product_id"]}, sort_keys=True)
        raise ValueError(f"Unknown read tool: {tool}")

    def read(self, tool: str, arguments: dict) -> str:
        observation = self.answer(tool, arguments)
        self.reads.append({"tool": tool, "arguments": json_safe(arguments), "observation": observation})
        return observation


@dataclass
class Action:
    """Stand-in for tau_bench.types.Action (name + kwargs)."""
    name: str
    kwargs: dict


class TauEnvSimulator:
    """Minimal Env.step surface used by adapters.tau_dispatch; not tau-bench.

    Like the native Env.step, tool exceptions become 'Error:' observations.
    The user channel replays only replies the harness queued for this step.
    """

    def __init__(self, simulator: RetailSimulator):
        self.simulator = simulator
        self.replies: deque = deque()
        self.respond_contents: list[str] = []

    def queue_reply(self, reply: dict) -> None:
        self.replies.append(reply)

    def step(self, action: Any) -> SimpleNamespace:
        done, source = False, action.name
        if action.name == "respond":
            self.respond_contents.append(str(action.kwargs.get("content", "")))
            reply = self.replies.popleft() if self.replies else {"text": "", "source": "user"}
            observation, source, done = reply["text"], reply.get("source", "user"), bool(reply.get("done", False))
        elif action.name in READ_TOOLS:
            observation = self.simulator.read(action.name, action.kwargs)
        else:
            try:
                result = self.simulator.invoke(action.name, action.kwargs)
            except SimulatedProviderTimeout as error:
                result = f"Error: {error}"
            observation = "Error: response lost after the request was sent" if result is UNCERTAIN else (
                result if isinstance(result, str) else json.dumps(result, sort_keys=True))
        return SimpleNamespace(observation=observation, reward=0, done=done, info=SimpleNamespace(source=source))
