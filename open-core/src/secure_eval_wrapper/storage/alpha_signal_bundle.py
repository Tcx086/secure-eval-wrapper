"""One atomic persistence boundary for a complete public alpha-to-signal milestone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from secure_eval_wrapper.alpha.models import AlphaDefinition, AlphaEvaluationResult
from secure_eval_wrapper.signals.models import SignalPipelineResult


class AlphaSignalBundleRepository(Protocol):
    def transaction(self): ...
    def register_alpha(self, definition): ...
    def record_alpha_run(self, run): ...
    def record_alpha_value(self, value): ...
    def record_signal_run(self, run): ...
    def record_signal(self, signal): ...
    def record_signal_component(self, component): ...


@dataclass(frozen=True)
class AlphaSignalBundleSummary:
    alpha_definition_count: int
    alpha_run_count: int
    alpha_value_count: int
    signal_run_count: int
    signal_count: int
    signal_component_count: int


class AlphaSignalBundlePersistenceError(RuntimeError):
    pass


def persist_alpha_signal_bundle(
    repository: AlphaSignalBundleRepository,
    *,
    definitions: Sequence[AlphaDefinition],
    alpha_results: Sequence[AlphaEvaluationResult],
    signal_results: Sequence[SignalPipelineResult],
) -> AlphaSignalBundleSummary:
    """Persist every parent and child inside exactly one repository transaction."""

    if repository is None or not hasattr(repository, "transaction"):
        raise TypeError("bundled persistence requires a transactional PostgreSQL repository")
    definitions_by_id = {item.alpha_id: item for item in definitions}
    if len(definitions_by_id) != len(definitions):
        raise ValueError("bundled alpha definitions must be unique")
    for result in alpha_results:
        if result.run.alpha_id not in definitions_by_id:
            raise ValueError("bundled alpha run is missing its registry definition")
        if any(value.alpha_run_id != result.run.alpha_run_id for value in result.values):
            raise ValueError("bundled AlphaValue references the wrong AlphaRun")
    persisted_alpha_ids = {value.alpha_value_id for result in alpha_results for value in result.values}
    for result in signal_results:
        signal_ids = {signal.signal_id for signal in result.signals}
        if any(component.signal_id not in signal_ids for component in result.components):
            raise ValueError("bundled SignalComponent is missing its signal parent")
        if any(component.alpha_value_id not in persisted_alpha_ids for component in result.components):
            raise ValueError("bundled SignalComponent is missing its AlphaValue parent")
    try:
        with repository.transaction():
            for definition in definitions:
                repository.register_alpha(definition)
            for result in alpha_results:
                repository.record_alpha_run(result.run)
                for value in result.values:
                    repository.record_alpha_value(value)
            for result in signal_results:
                repository.record_signal_run(result.run)
                for signal in result.signals:
                    repository.record_signal(signal)
                for component in result.components:
                    repository.record_signal_component(component)
    except Exception as exc:
        raise AlphaSignalBundlePersistenceError(f"bundled alpha-to-signal persistence failed: {exc}") from exc
    return AlphaSignalBundleSummary(
        alpha_definition_count=len(definitions),
        alpha_run_count=len(alpha_results),
        alpha_value_count=sum(len(item.values) for item in alpha_results),
        signal_run_count=len(signal_results),
        signal_count=sum(len(item.signals) for item in signal_results),
        signal_component_count=sum(len(item.components) for item in signal_results),
    )


__all__ = [
    "AlphaSignalBundlePersistenceError",
    "AlphaSignalBundleRepository",
    "AlphaSignalBundleSummary",
    "persist_alpha_signal_bundle",
]
