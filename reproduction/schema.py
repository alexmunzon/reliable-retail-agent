"""Typed offline identities; no provider credentials or paid execution path."""
from dataclasses import dataclass

@dataclass(frozen=True)
class OfflineCell:
    strategy: str
    scenario: str
    trial_index: int = 0

    def __post_init__(self):
        if self.strategy not in {'function_calling', 'react', 'act'}:
            raise ValueError('Unknown original strategy')
        if self.scenario not in {'success', 'missing_output', 'action_cutoff'}:
            raise ValueError('Unknown synthetic contract scenario')
        if type(self.trial_index) is not int or self.trial_index != 0:
            raise ValueError('Only the declared offline contract trial is supported')
