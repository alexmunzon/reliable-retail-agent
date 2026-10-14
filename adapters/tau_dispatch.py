"""Pre-dispatch adapter for the pinned tau-bench Action/Env interface.

Caller owns the agent loop and must use this boundary for ALL actor dispatches.
Never assign this adapter to env.step: native scoring calls env.step internally.
Use one fresh adapter per environment reset. No tau-bench import or model call is
performed here. Pass tau_bench.types.Action as action_factory in a real integration.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Iterable

from rra.gate import ConfirmationGate, GateResult, MutationIntent, UncertainMutationResult

PINNED_COMMIT = "59a200c6d575d595120f1cb70fea53cef0632f6b"

# This focused adapter is deliberately conservative. Other tools require an
# explicit audited classification before use; naming heuristics are unsafe.
ADDRESS_MUTATIONS = frozenset({"modify_pending_order_address"})
RETAIL_READ_TOOLS = frozenset({
    "find_user_id_by_email", "find_user_id_by_name_zip", "get_order_details",
    "get_product_details", "get_user_details", "list_all_product_types",
})


@dataclass(frozen=True)
class AdapterResult:
    status: str
    native_response: Any = None
    gate_result: GateResult | None = None


class TauDispatchAdapter:
    """Consent boundary, not an agent, scorer, or authentication system.

    operation_id must be assigned by trusted orchestration and remain stable for
    retries of the same logical mutation. This in-memory scope is not durable.
    Explicitly passed passthrough_tools must not contain database mutations.
    """

    def __init__(self, env: Any, action_factory: Callable[..., Any], *,
                 mutating_tools: Iterable[str] = ADDRESS_MUTATIONS,
                 passthrough_tools: Iterable[str] = RETAIL_READ_TOOLS):
        self._env = env
        self._action_factory = action_factory
        self._mutations = frozenset(mutating_tools)
        self._passthrough = frozenset(passthrough_tools) | {"respond"}
        if self._mutations & self._passthrough:
            raise ValueError("Mutation and passthrough classifications must be disjoint")
        self._lock = RLock()
        self._trace: list[dict[str, Any]] = []
        self._terminated = False
        self._mutation_response: Any = None
        self._gate = ConfirmationGate({
            name: self._mutation_callable(name) for name in self._mutations
        })

    def _mutation_callable(self, name: str) -> Callable[[dict], Any]:
        def invoke(arguments: dict) -> Any:
            response = self._native_dispatch(self._action_factory(name=name, kwargs=arguments))
            self._mutation_response = response
            if str(response.observation).lstrip().startswith("Error:"):
                # Native Env.step catches tool exceptions. Conservatively halt
                # on an error observation: it cannot prove no mutation occurred.
                return UncertainMutationResult("Native mutation returned an error observation; inspect and reconcile its outcome.")
            return response
        return invoke

    def _native_dispatch(self, action: Any) -> Any:
        # Written before native invocation; native reward replay never enters
        # this function, even if it appends more entries to env.actions.
        record = {"sequence": len(self._trace) + 1, "name": action.name,
                  "kwargs": deepcopy(action.kwargs), "outcome": "invoking"}
        self._trace.append(record)
        try:
            response = self._env.step(action)
        except Exception as error:
            record.update(outcome="uncertain", error=type(error).__name__)
            raise
        self._terminated = bool(response.done)
        record.update(outcome="returned", observation=response.observation,
                      source=getattr(response.info, "source", None), done=response.done)
        return response

    @staticmethod
    def _intent(action: Any, operation_id: str | None) -> MutationIntent:
        if not operation_id:
            raise ValueError("A stable operation_id is required for every mutation")
        return MutationIntent(action.name, action.kwargs, operation_id)

    def dispatch(self, action: Any, *, operation_id: str | None = None) -> AdapterResult:
        """Try an actor action; a blocked mutation never calls native env.step."""
        with self._lock:
            if self._terminated:
                return AdapterResult("terminated")
            if action.name in self._mutations:
                self._mutation_response = None
                decision = self._gate.dispatch(self._intent(action, operation_id))
                return AdapterResult(decision.status, self._mutation_response, decision)
            if action.name not in self._passthrough:
                raise ValueError(f"Unclassified tool blocked: {action.name}")
            if action.name == "respond":
                # A later conversation turn may retract or qualify consent.
                # It cannot keep authorization from an earlier prompt alive.
                self._gate.respond("", prompt_id=None)
            return AdapterResult("passthrough", self._native_dispatch(action))

    def request_confirmation(self, action: Any, *, operation_id: str) -> AdapterResult:
        """Present the gate's entire payload, then consume ONLY its native reply.

        The reply must come from the user channel of this synchronous exchange.
        Model-generated text and prior conversation observations are never read.
        A changed proposal must pass through this method again before dispatch.
        """
        with self._lock:
            if self._terminated:
                return AdapterResult("terminated")
            proposal = self._gate.propose(self._intent(action, operation_id))
            if proposal.status not in {"awaiting_confirmation", "ambiguous", "stale_consent"}:
                return AdapterResult(proposal.status, gate_result=proposal)
            prompt_action = self._action_factory(name="respond", kwargs={"content": proposal.prompt})
            response = self._native_dispatch(prompt_action)
            if response.done or getattr(response.info, "source", None) != "user":
                return AdapterResult("no_fresh_user_confirmation", response, proposal)
            decision = self._gate.respond(response.observation, prompt_id=proposal.prompt_id)
            return AdapterResult(decision.status, response, decision)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"pinned_commit": PINNED_COMMIT, "terminated": self._terminated,
                    "gate": self._gate.snapshot(), "actor_dispatches": deepcopy(self._trace)}
