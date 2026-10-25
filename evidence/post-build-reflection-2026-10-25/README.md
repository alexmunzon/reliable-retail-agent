# Deferred post-build reflection evidence — 2026-10-25

Decision: **improved** within the predeclared bounded scope. Baseline
`86e1900969cb304ce890a61e58a770c1fab9931d`; corrected runtime
`0dd21e0676c1ef306069edfffa6a1ec399fc994d`; pin/harness checkpoint
`9549d37138abba0e142c8d12f354623182aadf30`. Full findings, acceptance and limitations are in
[the reflection report](../../docs/POST_BUILD_REFLECTION.md).

All inputs are synthetic or existing frozen fixtures. New probes are exposed regressions, not
untouched holdout. Original failed receipts remain unchanged. MANIFEST.json records hashes and
identifies derivatives with local host paths redacted; original bytes remain in the local checkpoint.
Two whitespace findings inside the copied mutant patch/control summary are retained byte-for-byte
because they are raw evidence; source and documentation formatting checks pass.

## Measured before and after

| Outcome | Baseline | Corrected |
| --- | --- | --- |
| Malformed provider reply after a real effect | One POST/effect; stuck dispatching, held, zero lookups | One POST/effect; unknown/held through malformed status; complete status settles |
| Applied status without effect ID | Accepted success, hold released | Unknown, hold retained |
| Six corrupt receipt variants with refreshed file hashes | Incorrectly VALID | INVALID; genuine replay control still VALID |
| Null manifest | Unhandled exception | Structured INVALID |
| Newer pricing table damaged | Invalid JSON crashes dataset; rejected table may fall back to old rate | All 53 visible requests survive; estimates unknown; tokens/receipts unchanged |
| Impossible usage calendar date | Numeric synthetic charge | Rejected usage; unknown charge |
| Actual-JS response-order regressions | 11 failures | 11 passes |
| Real-browser reversed selection | Wrong detail rendered | Latest selection retained; stale errors ignored |

Provider comparison includes real local HTTP and the independent provider effect ledger, not just
mocked return values. The corrupted receipt comparison replays existing observations and is not
fresh execution. Fresh runtime execution is recorded separately below.

## Current verification

- Full clean-checkout offline suite: 361 PASS in 23.687s; native-author synthetic checks: 8 PASS in 3.324s.
- Full serving suite: 99 PASS in 99.669s, including real HTTP/provider boundary and crash/restore tests.
- Headless browser: 30 PASS, business state unchanged, no external requests or page errors.
- Damaged-pricing browser: 53 requests visible, unknown estimate, confirmed receipt retained.
- Core gate: 68/68 normative cases, zero changed outcomes; seven exploratory language cases remain non-gating.
- [Fresh pinned backend](20261025T170554Z-backend-c9f9ce78/summary.md): 24/24 gating PASS under harness-v3.
- [Affected backend mutants](backend-mutants/receipts.json): B1, B2, B8 and B9 KILLED for named reasons; 11-case unmutated control PASS.
- All five mutant/control receipts validate under current harness identity; four have expected FAIL decisions.
- Fourteen focused integrity tests passed again against the final pin. Independent scoped review also passed them.

The initial in-place full offline run had 360 passes and one import-boundary failure caused by the
preserved untracked historical `observability/live 2.py`. The failure is retained in
offline-in-place-failure.txt. The unchanged gate passed against the exact committed source in a
clean checkout. No copy was deleted, ignored by a new rule, staged or published.

The stale-restore-before-approval scenario still lacks historical authorization/attempt events and
remains exploratory, non-gating and invalid as complete trace evidence. No case or threshold changed.
Prior 33 core/evidence and 11 backend mutant proofs are historical; this pass reran the four affected
backend mutants, not every historical proof. Performance budgets from the integration are historical:
this pass measures correctness/recovery and UI fidelity, not new throughput or production reliability.

## Reproduce

Run from the committed source using its existing locked serving Python where required:

```bash
python3 -B scripts/ci.py --native
python3 -B scripts/ci_serving.py
node scripts/verify_dashboard_races.js
python3 -m evalgate backend --pinned --backend-git . --backend-python /path/to/locked/python --out /tmp/retail-backend-evidence
python3 -m evalgate backend-validate evidence/post-build-reflection-2026-10-25/20261025T170554Z-backend-c9f9ce78
```

The existing headless script accepts explicit paths to installed Playwright and Chromium; it does
not install or download them. CI includes the actual-JS ordering regression and the full serving suite.

Harness-v3 checks the current pin/source, frozen suite, raw observation coverage, reconstructed
traces/records/metrics/summary and dependency inventory. It cannot attest that a producer actually
executed the claimed source. Working-tree/mutant receipts explicitly have unverified backend provenance.
Historical v2 receipts must be validated with their matching `86e1900` source and pin, for example:

```bash
git worktree add --detach /tmp/retail-harness-v2 86e1900
cd /tmp/retail-harness-v2
python3 -m evalgate backend-validate evidence/integration-2026-10-25-followup/20261025T074658Z-backend-c6b27bb4
```

Do not rewrite old manifests to make them appear newly executed. Hosted status must be verified
against the exact publication head separately; no required-check settings or remote merge are implied.

## Hosted portability repair

The first post-reflection offline CI run on `75bce3d` failed one test because its parser-depth
assertion expected `manifest unreadable`; the runner instead parsed the nested array and returned
`manifest must be an object`. Both runtime paths correctly return INVALID. The same failure was
reproduced with local Python 3.12. The regression now requires INVALID, CLI exit 2 and no traceback,
and explicitly exercises RecursionError handling. All 14 integrity tests pass on Python 3.11
(1.928s) and 3.12 (1.634s). Separate review confirms the runtime, pin, labels and thresholds did not
change. Original failed CI and local receipts are preserved above.
