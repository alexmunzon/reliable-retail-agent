"""Read-only trace-based latency and cost review (namespaced; owns no business logic).

trace-v1 (contracts/, owned by the evaluation coordinator) is consumed unchanged. Timing,
token usage and charges live in the separately versioned obs-sidecar-v1 records defined here.
Nothing in this package can approve, dispatch, retry or cancel a business operation.
"""
SIDECAR_VERSION = "obs-sidecar-v1"
PRICING_VERSION = "obs-pricing-v1"
REPORT_VERSION = "obs-report-v1"
FIXTURE_SET_VERSION = "obs-fixtures-v1"
