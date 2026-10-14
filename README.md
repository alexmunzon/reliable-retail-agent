# Reliable Retail Agent

A working, no-key demonstration of confirmation at the tool-dispatch boundary, plus an honest offline audit of the original **τ-bench** paper.

![Actual desktop demo](evidence/demo-desktop.jpg)

## Run locally

Requires Python 3.11 or later. The demo uses the standard library; no API key, installation, or cloud account is needed.

```bash
python3 scripts/start.py
```

Open **http://127.0.0.1:8765**. Stop with Ctrl+C and run the same command to restart. An alternative port is supported with `python3 scripts/start.py --port 8766`. Sessions are synthetic, in memory, and reset on restart. The service runs only while its process remains alive.

## Try it

1. Choose **Missing confirmation**, then **Run scenario**. The illustrative baseline dispatches once; the gate waits and the gated fixture stays unchanged.
2. Review the exact payload, prompt identity, and SHA-256. Click **Yes, confirm**. One local dispatch occurs.
3. Choose **Changed payload** and run it. A prior “yes” does not authorize the changed payload; a fresh response is required.
4. Try **Stale consent**, **Customer rejection**, **Ambiguous response**, and **Uncertain tool outcome**. An uncertain mutation stops without retry.
5. Try **Airline domain transfer** to inspect the same gate in a second synthetic domain. Expand the dispatch log, state, and event trace.

All eight scenarios are **scripted and synthetic**. The baseline illustrates an ungated dispatch; it is **not a measured ReAct failure**. No purchases, refunds, real accounts, passengers, or customer email addresses are used by the demo. Current development `current-dev-v5` is **unevaluated**.

## What the code enforces

The gate owns the complete confirmation prompt and binds fresh customer assent to a typed operation, tool, exact canonical payload, prompt identity, and payload fingerprint. It rejects or holds ambiguous responses, invalidates consent after payload changes, prevents duplicate local dispatches, and halts on uncertain mutations. The browser reconciles every command against server state and does not automatically retry writes. Model integration is separate from the state machine.

This is a local proof of concept, not a production transaction system. Exactly-once behavior is scoped to an in-memory local session, not distributed services or crash recovery.

## Research evidence

- [Zero-cost author-artifact analysis](docs/ZERO_COST_REPRODUCTION.md): recomputes **1,980 later official author trajectories**, with complete task/trial grids, exact `pass^k` and distinct `pass@k`, and pinned source hashes.
- [Full-paper experiment audit](docs/FULL_REPRODUCTION_AUDIT.md): maps the paper's model comparison, method comparison, policy ablation, reliability, failure analysis, and task difficulty experiments.
- [Offline implementation runbook](docs/FULL_REPRODUCTION_RUNBOOK.md): verifies original author agent/environment/scorer code with explicit synthetic completion fixtures and no network calls.
- [Call walkthrough](docs/CALL_WALKTHROUGH.md) and [publication notes](docs/PUBLICATION.md).

**This is not a full historical paper reproduction.** The pinned paper-era source has no original released run logs; exact trial counts and some historical model snapshots are unspecified or unavailable. The four later author files were introduced in November 2024 and are not the original paper's Table 2 evidence. No fresh provider benchmark was run for this release. The upstream repository warns that these original tasks are outdated; no current leaderboard comparability is claimed.

The original study is [Yao et al., τ-bench (2024)](https://arxiv.org/abs/2406.12045). Official source: [sierra-research/tau-bench](https://github.com/sierra-research/tau-bench). Paper-era code: `6f4b718037db619539b8b692060e6686f3f0dcc9`. Requested later reference: `59a200c6d575d595120f1cb70fea53cef0632f6b`. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Verify

```bash
python3 -m unittest discover -s tests -v
python3 -m reproduction.metrics --repo-root . --output /tmp/zero-cost-results.json
```

The published snapshot passes **154 demo/metrics tests and 8 optional native integration tests**; see [verification receipt](evidence/VERIFICATION.json) and [browser evidence](evidence/BROWSER_WALKTHROUGH.md), with an [independent provenance and scoring review](evidence/ZERO_COST_INDEPENDENT_REVIEW.md).

The UI, core gate, HTTP API, and metrics tests are offline. The separate original-agent implementation checks use the dependencies described in the runbook. This snapshot intentionally has no paid-run command, credentials, or private account ledgers.
