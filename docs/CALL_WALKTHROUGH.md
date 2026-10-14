# Three-minute call walkthrough

Start `python3 scripts/start.py` and open http://127.0.0.1:8765. Keep the terminal running.

**Opening:** “This is a scripted proof of concept, not a benchmark score. Both lanes use synthetic data. The left illustrates ungated tool dispatch; the right executes a payload-bound confirmation state machine.”

**Missing confirmation:** Select the scenario and run it. Point out baseline dispatch 1, gated dispatch 0, and unchanged gated state. Open the pending payload and complete confirmation prompt. Give a fresh “Yes, confirm”; the gate dispatches once. Repeated clicks cannot dispatch the operation twice.

**Changed payload:** Run this scenario. Explain that assent is attached to the exact operation and payload; a modified address needs new consent. Review the new prompt before responding.

**Stale consent / uncertain outcome:** Old confirmation cannot authorize another operation. An uncertain tool result stops, without automatic retry that could duplicate a mutation.

**Airline:** Show the second synthetic domain and expand the before/after state and dispatch trace. The architecture is one runtime actor plus a gate; it does not require multiple agent actors.

**Research boundary:** “We audited the original τ-bench study, verified author code offline, and recomputed 1,980 later author-published trajectories. That is separate from fresh model evaluation. Original paper-run logs and some exact historical model versions are unavailable, so I am not claiming a full historical paper reproduction or an improvement.”

Use Reset to return to fresh fixtures. Browser reload restores the active in-memory session while the server is running. The terminal process must remain running; Ctrl+C stops it, and the start command restarts it. No real data or API credentials are needed.
