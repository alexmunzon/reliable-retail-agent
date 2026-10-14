"""Offline publication compatibility: only the typed stop exception is retained.

No accounting, provider integration, or paid-run capability is included.
"""
class BudgetStop(BaseException):
    """External abort must bypass the author's Exception-only retry machinery."""
    def __init__(self, message, *, stop_reason='controller_budget'):
        if stop_reason not in {'controller_budget','model_error','evidence_error'}:
            raise ValueError('Unknown external stop reason')
        super().__init__(message)
        self.stop_reason = stop_reason
