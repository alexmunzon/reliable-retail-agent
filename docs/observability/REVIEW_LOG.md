# Review log

## Astra design review (one read-only worker, before implementation)

- **Run:** `~/.claude/orchestrate-astra/runs/20261024-225155-trace-dashboard-design/` (outside the repo), Codex thread `01a17130-e223-75c1-a880-9a1dd8d68661`.
- **Evidence:** `verified: true`. The runtime model was only `gpt-6-astra`, provider openai, approval `never`, Standard tier, Ultra reasoning, network off, repo-only reads, no writes. It made 46 command executions and spawned no sub-agents. `boundary_enforcement_verified` stays false by design (it is proven by the separate synthetic harness).
- **Reviewer count:** one. No second reviewer and no other Desktop session.
- **Status:** worker output is advice. Claude verified each finding against the cited code before acting.

| # | Finding (Astra) | Status | Resolution |
|---|---|---|---|
| 1 | Ids are not unique across artifacts; the offline example holds 3 requests in one run | **Confirmed** (`serving-success.jsonl:5` vs `serving-illustrative.jsonl:5`) | Request identity = artifact + run + trace; sidecar digest-bound to its trace; test loads all 3 contract examples |
| 2 | Dedup/sort would erase the frozen INVALID verdict | **Confirmed** (`evalgate/trace.py:275-279`) | Checker runs on the raw stream; verdict is kept; diagnostic view labelled; in-file duplicate is INVALID, redelivered artifact is counted as ingestion |
| 3 | Status precedence insufficient; `still_unknown` then `effect_confirmed` | **Confirmed** | Headline is a label only; outcomes folded in order; `unknown-then-confirmed` fixture; `attempt_no` restricted to lookups |
| 4 | Token categories may overlap; delta vs final | **Confirmed** (design gap) | Disjoint categories defined; `usage_semantics: final_per_call` required |
| 5 | Leaf-only cost is not uniqueness | **Confirmed** | Usage identity by provider request id; parent usage allowed; rollups derived; re-sent record counted once |
| 6 | Same-domain rule needs incarnation identity | **Confirmed** | `clock_domain` = `name:incarnation`; `end_clock_domain` gives unknown; raw ns exported as strings |
| 7 | Dated pricing lacks applicability | **Confirmed** | `observed_at` required for an estimate; table id recorded; ambiguity is unknown; strict decimal syntax |
| 8 | Limits applied after `check_file` reads all | **Confirmed** (`evalgate/trace.py:248-255`) | Own bounded reader before the checker; oversize line never parsed (test) |
| 9 | Allowlist doesn't redact values/diagnostics | **Confirmed** (`evalgate/trace.py:102`) | Checker messages become fixed codes; `error_class` pattern-gated; free-form payloads and `raw_title` never exported |
| 10 | Quantile cohort/window rules underspecified | **Confirmed** | All counts including invalid; exclusions; warmup per incarnation; n = 0 unknown; undated counted |
| 11 | Fixture principal must be server-side on every route | **Confirmed** | Principal fixed at server start; `?principal=` rejected (400); hidden and missing both return the same 404 |
| 12 | Separate suite won't run in CI | **Confirmed** (`scripts/ci.py:44-56`) | Tests live in `tests/test_observability*.py`, discovered by the unchanged `scripts/ci.py`, within the 120 s budget (≈29 s total) |

No finding was refuted. Where Claude went further than the review: the time-window comparison bug (string vs instant) was found by a Claude-written test, and so were the cross-tenant flag scoping, the Host-header (DNS-rebinding) guard and telemetry failure isolation.

## Bugs found by tests and browser checks during the build

| Found by | Bug | Fix |
|---|---|---|
| Server test | `to=…10:12Z` compared as text against `…10:12:00.000Z`, so a request at exactly 10:12 was included | Compare parsed instants (`report.parse_utc`) |
| Oracle test | Contract hash fields (`prompt_sha256`, consent binding) were withheld and counted as redactions | Export the contract's closed hash fields |
| Bench | 2,000 requests in one sidecar exceeded the 4 MiB limit (correctly rejected) | Bench rotates artifacts; the limit is unchanged |
| Browser | JS syntax error; Route select overflow; date input overflow at 375 px; hash links ignored | Fixed; re-verified in the browser |

## Independent post-implementation review

Not run in this session. The spec calls for an independent Astra review of the actual code and results. It needs a new worker run, which Alex asked to request himself ("Alex will take over and request review as needed").
