# Zero-cost original native verification

The unchanged official author implementation executes locally with scripted
completions and the original scorer. These checks reproduce implementation
behavior, not model benchmark results. No provider request, credential read,
live budget write, retrospective score repair, or frozen v4/v5 edit occurred.

Primary source is official commit
`6f4b718037db619539b8b692060e6686f3f0dcc9`, with all 70 tracked files verified
byte for byte and upstream MIT retained. It is the nearest visible
pre-submission candidate; the exact executed paper snapshot is unproven.
`reproduction/evidence/native-verification.json` records every source SHA-256.
Key fingerprints:

| Author file | SHA-256 |
|---|---|
| `run.py` | `6707095b789735468ad66729d768cd7e0fd2b65fb9049d41e25531cdf906b9e9` |
| `tau_bench/agents/chat_react_agent.py` | `a72ad3d05000c9593f80b9fa136fdc177c306162f11c5dcc9370e65b714548d4` |
| `tau_bench/envs/base.py` | `93ae15cbd27b1edb4cbdcd783f9eda5b26c890b7ae34e0ee10192158e983b6fb` |

Nine fresh-process synthetic contracts exercise original function calling,
ReAct, and Act. Each checks successful database mutation plus required output,
missing-output failure, and 30-action cutoff. The original factory selects the
actual author classes; parser, prompts, sampling, user simulator accounting,
`Env.step`, and `calculate_reward` remain unchanged. Actor traces are captured
outside the scorer's reference replay. Success returns native reward one;
missing output returns native terminal reward zero; cutoff returns reward zero
without scorer invocation. The local stub supplies completions; no model makes
decisions. Network is blocked and worker environments contain no provider keys.

The actual original `run.py` is also executed with isolated temporary
checkpoints to confirm three different outcomes: an ordinary actor exception
creates a reward-zero record, an external `BaseException` stop propagates, and
an isolated setup failure propagates because it lies outside the actor catch.
The author's mean includes every recorded reward. This does not justify
turning absent or externally censored local cost/controller/evidence stops into
completed author trials. No local censored outcome was rescored.

The paper-era pinned tree contains task/database/policy files but **no released
trial trajectories**. The later pin
`59a200c6d575d595120f1cb70fea53cef0632f6b` includes four official files introduced
by author commit `7362a1708d720a4d697d042f21fd8637ea144c3b` on 2024-11-22:
`gpt-4o-{retail,airline}.json` and
`sonnet-35-new-{retail,airline}.json`. Their complete grids are respectively
115/50 tasks × four/eight trials. Separate `reproduction.metrics` analysis can
recompute those released rewards. They are later author artifacts, not original
paper-v1 trial logs.

Verification commands:

```bash
.venv-native/bin/python -m unittest tests.test_native_integration.PaperEraNativeTests tests.test_native_integration.PrimaryReadinessTests
.venv-native/bin/python -m unittest tests.test_full_reproduction_runner
```

The first group passed eight existing tests. The new group additionally checks
original runner failures, exclusive evidence output, absence of a paid command,
and portable source validation without Git metadata or the later runtime.
Exact final counts are recorded in the JSON evidence. Full original paper
reproduction remains blocked by unavailable or unspecified historical model
snapshots, run counts, seeds/logs, and annotations. See
`docs/FULL_REPRODUCTION_RUNBOOK.md` for the dry inventory and claim boundaries.
