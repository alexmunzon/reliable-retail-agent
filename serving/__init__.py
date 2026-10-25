"""Local-pilot FastAPI serving boundary for the synthetic retail consent gate.

One service process, one SQLite database, one enforcing consent policy reused from
`rra.gate` / `study.consent_current`. See docs/SERVING.md for supported guarantees.
"""
