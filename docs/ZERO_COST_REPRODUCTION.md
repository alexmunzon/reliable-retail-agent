# Zero-cost τ-bench author-artifact analysis

This is a **read-only recomputation of released author trajectories**, not a new model run or a reproduction of the full 2024 paper. It made zero provider calls and used no project budget. The machine-readable result is [`reproduction/evidence/zero-cost-results.json`](../reproduction/evidence/zero-cost-results.json).

## What was actually available

The pinned paper-era author repository at `6f4b718037db619539b8b692060e6686f3f0dcc9` (2024-06-06) contains source and tasks but no saved result or trajectory files. Thus none of the original Table 2 cells, method comparisons, policy ablations, or 32-trial reliability curves can be recalculated from original run-level evidence here. Re-running them would require model access, historical model versions, and funding.

The [official τ-bench repository](https://github.com/sierra-research/tau-bench) later added four files under `historical_trajectories/` in commit `7362a1708d720a4d697d042f21fd8637ea144c3b` (2024-11-22). The local checked-out commit is `59a200c6d575d595120f1cb70fea53cef0632f6b`; its README describes these as historical trajectories and invites contributions. The reporter requires each file's SHA-256 to match independently inspected bytes at that Git commit before it asserts official-release provenance. Their file hashes, all task/trial identities, complete denominators, and exact fraction results are in the JSON report. The release does not include a signed provider transcript or a fully pinned run configuration, so this analysis verifies the released records and Git provenance, not the underlying provider calls. In particular, `sonnet-35-new` is a later release label, not a 2024 paper Table 2 model.

| Later author file | Tasks × trials | pass^1 | pass^n | pass@n |
| --- | ---: | ---: | ---: | ---: |
| gpt-4o retail | 115 × 4 | 0.6043 | 0.3826 | 0.8087 |
| gpt-4o airline | 50 × 4 | 0.4200 | 0.2000 | 0.7200 |
| sonnet-35-new retail | 115 × 8 | 0.6924 | 0.3565 | 0.9391 |
| sonnet-35-new airline | 50 × 8 | 0.4600 | 0.1600 | 0.8000 |

Every cell above has the full stated task × trial grid, no duplicate task/trial key, binary native reward, and a nonempty released trajectory. These are retrospective results for the four files only. No local pilot, oracle fixture, or synthetic result is mixed in. The equal-domain pass^1 averages are 0.5122 for released gpt-4o and 0.5762 for released sonnet-35-new; those are half retail plus half airline, not an average over all 165 tasks.

## Estimator and failure semantics

For task *t*, let *cₜ* of its *n* recorded trials succeed. The paper's reliability estimator is `pass^k = mean_t C(cₜ,k)/C(n,k)`. The distinct any-success estimator is `pass@k = 1 − mean_t C(n−cₜ,k)/C(n,k)`. The [paper](https://arxiv.org/html/2406.12045) distinguishes them. `reproduction/metrics.py` uses exact rational arithmetic and rejects missing trials, duplicates, nonbinary rewards, unplanned task IDs, and censored inputs. It never treats our own infra-censored runs as author failures.

The [original author `run.py`](../vendor/tau-bench-paper-era/run.py) catches an exception from `isolated_agent.act`, saves that attempted trial with `reward: 0`, and later averages **all** result rewards. That author-defined zero is different from an absent trial or our infrastructure censor. The four later files have zero `info` strings beginning `Error: `; the reporter counts such recorded exceptions if present and rejects any with reward one. Released `0.0`/`1.0` rewards are converted only after exact binary validation.

## Coverage of original paper experiments

| [Paper](https://arxiv.org/html/2406.12045) experiment | Original scope | Reproducible from pinned paper-era results? |
| --- | --- | --- |
| Main Table 2 | 12 agent-model profiles, retail 115 and airline 50 tasks, at least three runs per task | No original run files released in the pinned tree; two later labels have separate trajectories above |
| Figure 3 strategies | FC, ReAct, and Act on retail for four GPT models | No original strategy-specific run grid |
| Figure 4 reliability | Five retail models, curves including `k=16` and `k=32` | No original 32-trial files; the later files support only `k≤4` or `k≤8` |
| Table 3 policy ablation | Two model profiles over both domains | No paired policy-variant original run files |
| FC `think` ablation | Added think tool versus base FC | No paired original result files |
| Appendix per-task difficulty | More than 40 GPT-4-turbo retail attempts per task | No original high-repeat result grid |

The separate proposed 1,320-cell gate study, local bounded experiments, and this later author-release calculation are three different scopes. None is a full execution of the paper. [The paper](https://arxiv.org/html/2406.12045) is the primary reference for its experiment definitions.

## Recompute offline

From the repository root:

```bash
python3 -m reproduction.metrics --repo-root . --output reproduction/evidence/zero-cost-results.json
python3 -m unittest tests.test_zero_cost_metrics -v
```

The command reads only the four committed author files and writes only the report. It does not import the native environment, a model client, provider credentials, or our runtime ledger. The JSON contains no private filesystem paths or provider identifiers.
