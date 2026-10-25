# Offline continuous integration

The `Offline tests` GitHub Actions workflow runs the published demo/metrics suite and original-author synthetic integration suite on pushes to main and on every pull request. A manual dispatch is also available. There are no path filters: a required check must run for Markdown-only policy or prompt changes too, so those now use roughly one runner-minute each.

The separate `Evaluation gate` workflow (job `evaluation-gate`) compares each PR's merge revision with the pinned approved baseline using the base revision's trusted harness. See [EVALUATION_GATE.md](EVALUATION_GATE.md).

One standard Ubuntu 24.04 job has a five-minute hard limit; dependency installation is limited to two minutes. Runs for the same ref cancel older in-progress runs. The workflow has read-only repository permissions, pins checkout to an immutable official release commit, and disables persisted Git credentials. It requests no repository secrets and uploads no Actions artifacts or caches.

Dependencies come from the pinned optional integration requirements. Test workers receive a minimal environment without inherited credentials. Python socket connections outside loopback are blocked; loopback remains available for real HTTP tests. The original-author runner additionally blocks sockets inside its synthetic processes. No model/provider calls are made. This is test isolation, not a general sandbox for untrusted contributors.

Run the same checks locally:

```bash
python3 scripts/ci.py
# With optional integration packages installed:
python3 scripts/ci.py --native
# The regression gate:
python3 -m evalgate check
```

The account's included allowance was checked before the initial run. These controls bound runtime and storage use; future GitHub-hosted runs still consume the repository owner's included minutes. GitHub billing settings were not changed. See [GitHub's billing documentation](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
