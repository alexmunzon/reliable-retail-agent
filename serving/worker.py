"""Lifespan-managed dispatch worker: bounded claims, one provider attempt, no blind retry.

Recording an outcome can fail after the provider acted. The claim committed before dispatch
(operation `dispatching` + resource hold) is already the durable record of that ambiguity, so the
worker never needs to invent one: transient storage contention is retried for a bounded time;
anything else parks the outcome in memory, frees the dispatch slot, and opens a circuit that stops
new claims, reconciliation and consent admission until a real write succeeds again. A parked outcome
is retried at each probe and never dropped. If the process stops first, startup recovery turns the
retained `dispatching` claim into `unknown` and reconciles it from provider evidence; nothing is
re-dispatched.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import time

from anyio import to_thread

from .provider import Outcome, ProviderClient
from .service import Claim, Service, ServiceError
from .config import Settings
from .telemetry import Telemetry


class Worker:
    def __init__(self, service: Service, provider: ProviderClient, settings: Settings, telemetry: Telemetry, notify, observation_spans=None):
        self.service, self.provider, self.settings = service, provider, settings
        self.telemetry, self.notify = telemetry, notify
        self.observation_spans = observation_spans
        self._wake = asyncio.Event()
        self._stopping = False
        self._loop_task: asyncio.Task | None = None
        self._reconcile_task: asyncio.Task | None = None
        self._inflight: dict[asyncio.Task, Claim] = {}
        self._last_maintenance = 0.0
        self.parked: dict[str, tuple[Claim, Outcome]] = {}  # Provider outcomes not yet durably recorded.
        self.storage_fault: str | None = None  # Last non-transient write error class while degraded.
        self._next_probe = 0.0
        # Claiming and opening the circuit are serialized: a claim either commits before the circuit
        # opens (and is dispatched like any other) or sees it open and claims nothing.
        self._claim_gate = asyncio.Lock()
        self.running = False

    @property
    def degraded(self) -> bool:
        """Circuit open: safe progress needs a working write path and every outcome recorded."""
        return bool(self.parked) or self.storage_fault is not None

    async def start(self) -> None:
        recovered = await to_thread.run_sync(self.service.recover_orphans)
        if recovered:
            self.telemetry.count("operations_recovered_unknown", recovered)
            self.telemetry.log("worker.recovered_orphans", count=recovered)
        self._loop_task = asyncio.create_task(self._loop(), name="rra-dispatch-worker")
        self.running = True

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.settings.worker_scan_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self._stopping:
                break
            try:
                await self._tick()
            except Exception as error:  # Keep the loop alive; durable state is the source of truth.
                self.telemetry.count("worker_tick_errors")
                self.telemetry.log("worker.tick_error", reason=type(error).__name__)

    async def _tick(self) -> None:
        if self.degraded:
            if time.monotonic() >= self._next_probe:
                await self._probe()
            if self.degraded:
                return  # No new claims, lookups or maintenance until a real write succeeds.
        capacity = self.settings.worker_concurrency - len(self._inflight)
        async with self._claim_gate:
            if self.degraded or self._stopping:
                return
            claims = await to_thread.run_sync(self.service.claim, capacity) if capacity > 0 else []
            if self._stopping and claims:
                # Shutdown began during the claim: send nothing. The rows stay `dispatching` with no
                # provider call; startup recovery makes them `unknown` and a fenced lookup settles them.
                self.telemetry.log("worker.claimed_during_shutdown", count=len(claims))
                return
            for claim in claims:
                task = asyncio.create_task(self._dispatch(claim), name=f"dispatch-{claim.operation_id}")
                self._inflight[task] = claim
                task.add_done_callback(self._inflight.pop)
        if claims:
            self.telemetry.count("dispatch_claims", len(claims))
            self.notify()
        self.telemetry.gauge("dispatch_inflight", len(self._inflight))
        if self._reconcile_task is None or self._reconcile_task.done():
            self._reconcile_task = asyncio.create_task(self._reconcile_due(), name="rra-reconcile")
        now = time.monotonic()
        if now - self._last_maintenance >= self.settings.maintenance_seconds:
            self._last_maintenance = now
            result = await to_thread.run_sync(self.service.maintenance)
            if any(result.values()):
                self.notify()

    async def _dispatch(self, claim: Claim) -> None:
        if self.settings.failpoint("crash_after_claim"):
            os._exit(70)  # Test-only: die after the claim commit, before any provider I/O.
        started = time.monotonic()
        provider_start_ns = time.monotonic_ns()
        self.telemetry.count("provider_attempts")
        try:
            outcome = await self.provider.mutate(provider_key=claim.provider_key, tenant=claim.tenant_id,
                                                 tool_name=claim.tool_name, arguments=claim.arguments,
                                                 expected_revision=claim.expected_revision)
        except Exception as error:
            # The request may already have acted. Preserve the claim as unknown; never redispatch.
            # CancelledError is a BaseException and remains owned by the shutdown/abandon path.
            outcome = Outcome("unknown", "provider_client_error")
            self.telemetry.count("provider_client_errors")
            self.telemetry.log("worker.provider_client_error", operation_id=claim.operation_id,
                               reason=type(error).__name__)
        provider_end_ns = time.monotonic_ns()
        if self.observation_spans is not None:
            try:
                self.observation_spans.record(claim, provider_start_ns, provider_end_ns,
                                              {"succeeded": "ok", "failed_no_effect": "error", "unknown": "unknown"}[outcome.state])
            except Exception:
                self.telemetry.count("observability_capture_dropped")
        if self.settings.failpoint("crash_after_provider_response"):
            os._exit(71)  # Test-only: provider answered; this process dies before recording it.
        self.telemetry.count(f"provider_outcome_{outcome.state}")
        await self._record(claim, outcome)
        self.telemetry.log("worker.dispatch_finished", run_id=claim.run_id, operation_id=claim.operation_id,
                           state=outcome.state, detail_code=outcome.code,
                           duration_ms=round((time.monotonic() - started) * 1000, 1))
        self.notify()
        self.wake()  # A released hold may unblock another pending operation.

    async def _record(self, claim: Claim, outcome: Outcome) -> None:
        """Local-only write guarded by claim identity, so retrying it can never repeat the provider call.

        Busy/locked contention is retried with capped backoff for at most `completion_retry_seconds`.
        Any other error, or running out of that time, parks the outcome (see the module docstring).
        """
        deadline = time.monotonic() + self.settings.completion_retry_seconds
        attempt = 0
        while True:
            try:
                await to_thread.run_sync(self.service.complete, claim, outcome)
                return
            except Exception as error:
                attempt += 1
                self.telemetry.count("completion_write_retries")
                remaining = deadline - time.monotonic()
                if not is_transient(error) or remaining <= 0:
                    async with self._claim_gate:
                        return self._park(claim, outcome, error)
                if attempt in {1, 10, 100}:
                    self.telemetry.log("worker.completion_write_retry", operation_id=claim.operation_id,
                                       count=attempt, reason=type(error).__name__)
                await asyncio.sleep(min(0.2 * 2 ** min(attempt, 5), 5.0, remaining))

    def _park(self, claim: Claim, outcome: Outcome, error: Exception) -> None:
        self.parked[claim.operation_id] = (claim, outcome)
        self.storage_fault = type(error).__name__
        self._next_probe = time.monotonic() + self.settings.recovery_probe_seconds
        self.telemetry.count("outcomes_parked")
        self.telemetry.gauge("outcomes_unrecorded", len(self.parked))
        # Provider evidence only (no customer data), so an operator can reconcile by hand if needed.
        self.telemetry.log("worker.outcome_parked", operation_id=claim.operation_id, state=outcome.state,
                           detail_code=outcome.code, reason=type(error).__name__,
                           effect_id=(outcome.result or {}).get("effect_id"))

    async def _probe(self) -> None:
        """Retry parked outcomes, then prove the write path with a real write before closing the circuit."""
        self._next_probe = time.monotonic() + self.settings.recovery_probe_seconds
        try:
            for operation_id, (claim, outcome) in list(self.parked.items()):
                await to_thread.run_sync(self.service.complete, claim, outcome)  # False = settled elsewhere.
                del self.parked[operation_id]
            await to_thread.run_sync(self.service.probe_write)
        except Exception as error:
            self.storage_fault = type(error).__name__
            self.telemetry.count("recovery_probe_failures")
        else:
            self.storage_fault = None
            self.telemetry.log("worker.circuit_closed")
            self.notify()
        self.telemetry.gauge("outcomes_unrecorded", len(self.parked))

    async def _reconcile_due(self) -> None:
        try:
            await self._reconcile_batch()
        except Exception as error:  # Lookups are read-only; the next tick tries again.
            self.telemetry.count("reconciliation_errors")
            self.telemetry.log("worker.reconciliation_error", reason=type(error).__name__)
            if not is_transient(error):  # A lookup whose result cannot be stored must not repeat every tick.
                async with self._claim_gate:  # Same serialization as _park: no claim straddles the opening.
                    self.storage_fault = type(error).__name__
                    self._next_probe = time.monotonic() + self.settings.recovery_probe_seconds

    async def _reconcile_batch(self) -> None:
        due = await to_thread.run_sync(self.service.due_reconciliations, 10)
        for operation_id, provider_key in due:
            try:
                outcome = await self.provider.status(provider_key)
            except Exception as error:
                # A client failure is an unsuccessful lookup, not a storage fault. Record it so
                # the existing lookup budget still terminates with the resource held.
                outcome = Outcome("unknown", "status_client_error")
                self.telemetry.count("provider_status_client_errors")
                self.telemetry.log("worker.provider_status_client_error", operation_id=operation_id,
                                   reason=type(error).__name__)
            result = await to_thread.run_sync(self.service.record_reconciliation, operation_id, outcome)
            self.telemetry.count(f"reconciliation_{result}")
            self.telemetry.log("worker.reconciliation", operation_id=operation_id, state=result, detail_code=outcome.code)
            self.notify()
            if result in {"succeeded", "failed_no_effect"}:
                self.wake()

    async def stop(self) -> None:
        """Stop claiming, drain in-flight dispatches, record what remains as unknown, within one deadline.

        The worker stops within 2 x drain_seconds. Storage calls are never interrupted mid-transaction,
        so a call already waiting on a locked database can delay process exit by at most one SQLite busy
        timeout (5 s) after that. Whatever is unrecorded then stays `dispatching` on disk; startup
        recovery turns it into `unknown` for reconciliation: never a second dispatch.
        """
        deadline = time.monotonic() + 2 * self.settings.drain_seconds

        def left() -> float:
            return max(0.0, deadline - time.monotonic())

        self._stopping = True
        self._wake.set()
        if self._loop_task:
            await asyncio.wait([self._loop_task], timeout=left())
        if self._inflight:
            _, pending = await asyncio.wait(list(self._inflight), timeout=min(self.settings.drain_seconds, left()))
            abandoned = [claim for task in pending if (claim := self._inflight.get(task)) is not None]
            for task in pending:
                task.cancel()  # Cancelling the await is not evidence the provider did nothing.
            writes = [asyncio.create_task(self._abandon(claim)) for claim in abandoned]
            if writes:
                await asyncio.wait(writes, timeout=left())
        if self.parked:
            await asyncio.wait([asyncio.create_task(self._probe())], timeout=left())
            if self.parked:
                self.telemetry.log("worker.stopped_with_unrecorded_outcomes", count=len(self.parked),
                                   operation_ids=sorted(self.parked))
        if self._reconcile_task and not self._reconcile_task.done():
            self._reconcile_task.cancel()
        self.running = False

    async def _abandon(self, claim: Claim) -> None:
        try:
            await to_thread.run_sync(self.service.abandon_claim, claim, "shutdown_during_dispatch", abandon_on_cancel=True)
            self.telemetry.log("worker.abandoned_on_shutdown", operation_id=claim.operation_id)
        except Exception as error:  # Row stays `dispatching`; startup recovery marks it unknown.
            self.telemetry.log("worker.abandon_write_failed", operation_id=claim.operation_id, reason=type(error).__name__)

def is_transient(error: Exception) -> bool:
    """Only lock contention is expected to clear by itself; everything else needs an operator."""
    if isinstance(error, ServiceError):
        return error.code == "database_busy"
    return isinstance(error, sqlite3.OperationalError) and ("locked" in str(error) or "busy" in str(error))
