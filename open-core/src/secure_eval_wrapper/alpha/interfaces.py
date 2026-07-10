"""Provider- and storage-neutral public alpha interfaces."""

from __future__ import annotations

from typing import Mapping, Protocol

from secure_eval_wrapper.alpha.input_validation import PointInTimeSeries
from secure_eval_wrapper.alpha.models import AlphaComputationPoint, AlphaDefinition


class PublicAlpha(Protocol):
    @property
    def definition(self) -> AlphaDefinition:
        """Return immutable public metadata for this implementation."""

    def validate_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        """Return a complete validated parameter mapping."""

    def evaluate(
        self,
        series: PointInTimeSeries,
        parameters: Mapping[str, object],
    ) -> tuple[AlphaComputationPoint, ...]:
        """Evaluate pure calculations without I/O or mutable global state."""


class AlphaPersistenceRepository(Protocol):
    def transaction(self): ...
    def register_alpha(self, definition: AlphaDefinition): ...
    def record_alpha_run(self, run): ...
    def record_alpha_value(self, value): ...


__all__ = ["AlphaPersistenceRepository", "PublicAlpha"]
