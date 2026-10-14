# Original tau-bench: zero-cost verification and reproduction boundaries

This entry point verifies unchanged original author code locally and inventories
the paper's experiments. It does **not** reproduce the original paper's model
results. No paid command exists. The gated developmental studies and current
demo are separate methods and are not imported by this runner.

The demo and metrics need only Python 3.11+. For the separate original-author integration checks, create an optional virtual environment and install the public pinned packages (no provider key is used):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-offline.txt
.venv/bin/python scripts/reproduction.py
.venv/bin/python scripts/reproduction.py inventory
.venv/bin/python scripts/reproduction.py offline --output /tmp/new-original-native-check.json
.venv/bin/python -m unittest discover -s tests_native -v
```

The default is a content-bound dry plan. `inventory` checks the source and
inventories all seven audited experiment scopes. `offline` runs nine synthetic
contracts: original GPTFunctionCallingAgent, text ReAct, and Act, each with a
success, missing required response output, and native 30-action cutoff. Each
cell runs in a fresh process. OpenAI construction/completions are mocked, socket
connections are blocked, and the original native user completion and accounting
code execute against zero-token stubs. No key or environment file is read.
Outputs use exclusive creation and a unique attempt identifier; rerunning into
an existing file fails rather than replacing evidence.

The synthetic counter tools, policy, task instruction, and expected state are
local fixtures. They are not benchmark data and do not select or tune a model
against benchmark outcomes. The original factory constructs the actual author
agents, and original `BaseEnv.step` and `calculate_reward` execute unchanged.
The passive outer observer records actor dispatches; reference replay runs on
the raw environment and therefore does not become an actor dispatch. The test
also checks that hidden task instruction does not enter actor requests. This
checks plumbing, not general resistance to hidden-data leakage.

The fixture uses the source's `gpt-4` simulator alias against a local stub. It
does not imply availability or equivalence to the paper's `gpt-4-0613`. ReAct
retains its original six-line parser; Act uses the original `reason=False`
prompt; FC retains its original tool-call parser. No parser compatibility shim,
terminal normalizer, consent gate, planning feedback, or current controller is
used. The original actor sampling temperature is zero; the actual native user
request preserves temperature one and `max_tokens=150`.

## Provenance and portability

Primary official source candidate:
`6f4b718037db619539b8b692060e6686f3f0dcc9`. All 70 tracked files are verified
against the Git pin when metadata exists, or the bundled SHA-256 manifest in a
portable snapshot. This is the nearest visible pre-submission revision, not a
proven identity of the unpublished executed paper snapshot. Bundled hashes are
provenance records, not independent signatures. The upstream MIT license is
retained.

Later official trajectories at
`59a200c6d575d595120f1cb70fea53cef0632f6b` are verified individually against the
later manifest. A public snapshot needs only those four files for this part,
not the later runtime. Their metrics are handled separately by
`reproduction.metrics`; they must not be presented as original paper-v1 trials.

Minimal local dependencies are `openai`, `tenacity`, and `termcolor`; the tested
versions are recorded in the native evidence. Author `setup.py` does not pin all
historical dependency versions. The stub supplies the three original FC usage
keys; this does not validate modern provider response compatibility or access.

## What is blocked

The dry manifest includes Table 2, Figures 3–7, and Table 3 as audited scopes.
Many immutable model snapshots, exact trials per task, per-episode stochastic
sampling records, the executed source revision, and original failure-analysis
annotations are unavailable or unspecified. Historical model availability is
audited separately. Table 2's at-least-three trials is not the original CLI's
one-trial default; Figure 4 requires enough trials for k up to 32. Unknown
settings are not filled with convenient defaults.

Original `run.py` records an ordinary exception from `agent.act` as reward zero.
An isolated environment or actor setup exception occurs outside that catch.
An external `BaseException` stop bypasses it. Original agents reaching 30 native
actions return their last reward (zero for the nonterminal fixture); they do not
force the scorer to run. Our external cost, evidence, and controller censors
remain separate incomplete outcomes. They are not retroactively converted to
author reward-zero trials or used to reconstruct a native reward.

No additional model evaluation is authorized or enabled by these commands.
Full historical reproduction remains incomplete. The completed deliverables
are source verification, original-code contracts, audited dry scopes, and
separate retrospective author-artifact analysis.
