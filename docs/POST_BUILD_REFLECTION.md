# Deferred post-build reflection — Retail

Decision: **improved**, then freeze the bounded scope. This is the separately requested review after
all original builds reached their scoped checkpoints, not a relabeling of the integration review.
Baseline `86e1900` and its failed receipts are preserved. Runtime is now frozen at
`0dd21e0676c1ef306069edfffa6a1ec399fc994d`; all frozen evaluation cases, labels, effect oracles,
trace-v1/v2 contracts and original source branches remain unchanged.

## Ranked findings, acceptance and actual changes

1. **Provider boundary: high impact, plausible malformed upstream response.** The previous code
   could lose the active task while a durable claim stayed dispatching, or call an incomplete status
   receipt success. Acceptance required exactly one real provider mutation, durable unknown/hold
   through malformed evidence, and settlement only from complete read-only evidence. Typed/strict
   response parsing and a small Exception boundary now preserve uncertainty; cancellation retains
   the existing shutdown path. Lookup exceptions consume the existing bounded reconciliation budget.
   A parsed-origin guard also rejects a userinfo URL that passed the old loopback prefix check; that
   address was never contacted. The observed HTTP comparison went from one effect stuck forever in
   dispatching to one effect reconciled after two malformed and one complete status reply.
2. **Receipt integrity: high impact on review decisions, plausible damaged/copied artifacts.**
   Acceptance required rejection of false exports/provenance even after their file hashes were
   refreshed, while a genuine control remained valid. Harness-v3 reconstructs trace and summary
   exports, checks exact observation membership and frozen suite identity, and verifies pinned source
   bytes/dependency claims. Six formerly false-VALID variants now become INVALID; malformed structures
   fail cleanly. This is evidence consistency, not cryptographic execution attestation. Old receipts
   retain their original validator version and provenance.
3. **Dashboard: medium impact, normal asynchronous use or damaged optional data.** Acceptance required
   latest-request rendering under reversed replies, preserved business traces with missing pricing,
   and no numeric charge for impossible calendar dates. Small request generations suppress stale
   selection/filter/aggregate/auth responses and closed-pane updates. Optional pricing is quarantined
   as an incomplete set so a missing newer table cannot silently select older rates. Valid trace,
   token and receipt evidence remains visible. Five corruption cases retain all 53 visible requests;
   11 exposed JS races changed from failure to pass. A real browser confirms both reversed replies
   and the damaged-pricing page, including its unknown estimate and retained confirmed receipt.

## Verification and review

[Versioned evidence and commands](../evidence/post-build-reflection-2026-10-25/README.md) include
baseline failures, before/after observations, a fresh 24/24 pinned backend PASS, 361 offline tests,
8 native-author checks, 99 serving tests, 30 browser checks, 11 controlled-JS races and four affected
backend mutants killed with a passing unmutated control. The core normative gate stays 68/68 with
no changed outcomes. The preserved workspace's historical copy caused one initial import-boundary
test failure; the exact publication source passed that same unchanged gate in a clean checkout.

Three scoped implementers reviewed raw source, contracts, fixtures, assertions and artifacts. A
separate read-only role reviewed all patches and reran 7 provider, 14 receipt, 5 pricing/date and 11
JS checks. It requested summary reconstruction and invalidation when a pending detail is closed;
both were incorporated and tested. The coordinator added real-browser race and damaged-pricing
verification. Roles share model/context/filesystem and synthetic data: this is neither an external
audit nor held-out evidence. Newly exposed regressions are never renamed independent holdout.

## Reflection on maintenance, performance and limits

The added code targets measured failures. Most additions are explicit fault/evidence/UI regression
tests; runtime changes reuse existing transactions, Outcome states, projection and rendering. No new
framework, agent graph, dependency, network service or queue was introduced. Provider parsing adds
strict field checks; rendering adds constant-size counters. Receipt validation does additional local
Git/archive work off the request path. Existing provider retry budgets, resource holds, observation
limits and read-only contracts remain intact. No throughput improvement or capacity claim is made;
earlier synthetic performance receipts describe their original runtime, not this revision.

Deferred low-impact finding: CLI `serve`/`serve-provider` helpers still parse the provider port with
string splitting, so a trailing-slash URL accepted by configuration/the client fails there. Use the
documented explicit-port origin without a trailing slash. This does not weaken the parsed-host guard
or the measured default startup/recovery path. Response body streaming/size budgets and slow-body
deadlines would require a separate resource-limit review; this pass did not add speculative machinery.

Existing holds remain: stale restores cannot invent missing consent/attempt history; live timings
are bounded process-local provider spans, not full request/model latency; prices and workloads are
synthetic, not bills or customer evidence. No paid models, production traffic, public deployment,
GitHub merge, visibility or security-setting changes occurred. Required backend-check enforcement
and the stacked base's missing rules remain outside authorization. The 23 historical untracked
copies are preserved locally and excluded from publication; the entire canonical workspace is not
claimed clean. Original conversation history was not migrated; one ChatGPT/Astra session and preserved
handoffs retain continuity. Stop here after exact remote CI succeeds; do not expand into endless review.
