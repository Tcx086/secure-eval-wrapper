"""Deterministic, executable assurance verification for Phase 8B shadow evidence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Mapping

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .identity import RuntimeRepositoryIdentity, validate_git_commit_sha
from .shadow_repository import (
    MemoryShadowRepository,
    ShadowMemoryStore,
    ShadowPostCommitCrash,
)
from .shadow_runtime import (
    FixtureShadowMarketSource,
    RUNTIME_CRASH_POINTS,
    ShadowAssuranceRuntime,
)
from .shadow_scenarios import all_shadow_scenarios
from .shadow_models import shadow_uuid


SHADOW_VERIFIER_VERSION = "phase8b-shadow-assurance-verifier-v3"
POSTGRESQL_VERIFIER_NOT_EXECUTED = "POSTGRESQL_VERIFIER_NOT_EXECUTED"
_RESTART_CASES = ("clean_flat_account", "existing_long_spot_position", "stale_data")
_REPLAY_CASES = (
    "clean_flat_account",
    "existing_long_spot_position",
    "pending_buy_order",
    "stale_data",
    "delisted_instrument",
    "insufficient_quote_balance",
)
_CONCURRENCY_CASES = tuple(f"shadow_concurrency_{index:02d}" for index in range(1, 8))


def _runtime(repository, repository_sha: str) -> ShadowAssuranceRuntime:
    return ShadowAssuranceRuntime(
        repository=repository,
        market_source=FixtureShadowMarketSource(),
        identity_resolver=lambda: RuntimeRepositoryIdentity(repository_sha, "git_checkout"),
    )


def _case_result(case_id: str, passed: bool, evidence: object) -> dict[str, object]:
    core = {
        "case_id": case_id,
        "passed": bool(passed),
        "evidence_hash": sha256_payload(evidence),
    }
    return {**core, "result_hash": sha256_payload(core)}


def _scenario_catalog_hash() -> str:
    return sha256_payload([
        {
            "scenario_id": scenario.scenario_id,
            "input_hash": scenario.input_hash,
            "expected_result": scenario.expected_result,
            "expected_blockers": scenario.expected_blockers,
            "expected_shadow_intent_count": scenario.expected_shadow_intent_count,
            "expected_network_read_count": scenario.expected_network_reads,
            "expected_network_write_count": scenario.expected_network_writes,
            "expected_persistence_result": scenario.expected_persistence_result,
        }
        for scenario in all_shadow_scenarios()
    ])


def _runtime_implementation_hash() -> str:
    root = Path(__file__).resolve().parent
    names = (
        "shadow_models.py",
        "shadow_scenarios.py",
        "shadow_runtime.py",
        "shadow_repository.py",
        "shadow_verifier.py",
    )
    return sha256_payload({
        name: sha256_payload({
            "source": (root / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        })
        for name in names
    })


def run_offline_assurance_verifier(repository_sha: str) -> dict[str, object]:
    """Execute the catalog, restart, replay, concurrency, and crash cases in memory."""
    repository_sha = validate_git_commit_sha(repository_sha)
    scenario_results: list[dict[str, object]] = []
    blocker_frequencies: dict[str, int] = {}
    accepted = 0
    summaries = []
    for scenario in all_shadow_scenarios():
        summary = _runtime(MemoryShadowRepository(), repository_sha)._run_fixture_scenario_for_test(
            scenario
        )
        summaries.append(summary)
        passed = (
            ("accepted" if summary.accepted else "blocked") == scenario.expected_result
            and summary.blockers == scenario.expected_blockers
            and summary.shadow_intent_count == scenario.expected_shadow_intent_count
            and summary.safety_facts.network_read_count == scenario.expected_network_reads
            and summary.safety_facts.network_write_count == scenario.expected_network_writes
            and summary.persistence_result == scenario.expected_persistence_result
        )
        scenario_results.append(_case_result(scenario.scenario_id, passed, summary.public_payload()))
        accepted += int(summary.accepted)
        for blocker in summary.blockers:
            blocker_frequencies[blocker] = blocker_frequencies.get(blocker, 0) + 1

    restart_results = []
    for case_id in _RESTART_CASES:
        store = ShadowMemoryStore()
        summary = _runtime(MemoryShadowRepository(store), repository_sha).run_fixture(case_id)
        loaded = MemoryShadowRepository(store).load_bundle(summary.shadow_run_id)
        summaries.append(summary)
        passed = bool(
            loaded
            and loaded.get("status") == "complete"
            and loaded.get("decision", {}).get("decision_hash") == summary.decision_hash
        )
        restart_results.append(_case_result(case_id, passed, loaded))

    replay_results = []
    for case_id in _REPLAY_CASES:
        store = ShadowMemoryStore()
        runtime = _runtime(MemoryShadowRepository(store), repository_sha)
        first = runtime.run_fixture(case_id)
        second = runtime.run_fixture(case_id)
        summaries.extend((first, second))
        passed = (
            not first.replayed
            and second.replayed
            and first.decision_hash == second.decision_hash
            and first.summary_hash != second.summary_hash
        )
        replay_results.append(_case_result(case_id, passed, {
            "first": first.public_payload(), "second": second.public_payload()
        }))

    concurrency_results = []
    concurrency_scenarios = tuple(scenario.scenario_id for scenario in all_shadow_scenarios()[:7])
    for case_id, scenario_id in zip(_CONCURRENCY_CASES, concurrency_scenarios, strict=True):
        store = ShadowMemoryStore()
        run_id = shadow_uuid("verifier-concurrency", {
            "repository_sha": repository_sha, "case_id": case_id
        })
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = tuple(
                pool.submit(
                    _runtime(MemoryShadowRepository(store), repository_sha).run_fixture,
                    scenario_id,
                    shadow_run_id=run_id,
                )
                for _ in range(2)
            )
            concurrent = tuple(sorted(
                (future.result() for future in futures),
                key=lambda item: (item.persistence_result, item.summary_hash),
            ))
        summaries.extend(concurrent)
        passed = (
            len({item.decision_hash for item in concurrent}) == 1
            and sorted(item.persistence_result for item in concurrent)
            == ["idempotent_replay", "persisted"]
        )
        concurrency_results.append(_case_result(case_id, passed, [
            item.public_payload() for item in concurrent
        ]))

    crash_results = []
    for crash_point in sorted(RUNTIME_CRASH_POINTS):
        store = ShadowMemoryStore()
        run_id = shadow_uuid("verifier-crash", {
            "repository_sha": repository_sha, "crash_point": crash_point
        })
        observed_exception = None
        try:
            _runtime(MemoryShadowRepository(store), repository_sha).run_fixture(
                "clean_flat_account", shadow_run_id=run_id, crash_at=crash_point
            )
        except Exception as exc:  # the verifier records only type/fact, never the message
            observed_exception = type(exc).__name__
        loaded = MemoryShadowRepository(store).load_bundle(run_id)
        committed = crash_point == "after_transaction_commit_before_response"
        passed = (
            observed_exception is not None
            and (loaded is not None) is committed
            and (not committed or loaded.get("status") == "complete")
            and (not committed or observed_exception == ShadowPostCommitCrash.__name__)
        )
        crash_results.append(_case_result(crash_point, passed, {
            "exception_type": observed_exception,
            "complete_bundle_recoverable": bool(loaded and loaded.get("status") == "complete"),
        }))

    zero_write_facts = {
        "network_write_count": sum(item.safety_facts.network_write_count for item in summaries),
        "production_transport_call_count": sum(
            item.safety_facts.production_transport_call_count for item in summaries
        ),
        "authenticated_endpoint_call_count": sum(
            item.safety_facts.authenticated_endpoint_call_count for item in summaries
        ),
        "credential_read_count": sum(item.safety_facts.credential_read_count for item in summaries),
        "production_write_count": sum(item.safety_facts.production_write_count for item in summaries),
    }
    core: dict[str, object] = {
        "verifier_version": SHADOW_VERIFIER_VERSION,
        "repository_sha": repository_sha,
        "scenario_catalog_hash": _scenario_catalog_hash(),
        "runtime_implementation_hash": _runtime_implementation_hash(),
        "scenario_results": scenario_results,
        "restart_results": restart_results,
        "replay_results": replay_results,
        "concurrency_results": concurrency_results,
        "crash_results": crash_results,
        "postgresql_verification": {
            "classification": POSTGRESQL_VERIFIER_NOT_EXECUTED,
            "restart_results": [],
            "replay_results": [],
            "concurrency_results": [],
            "crash_results": [],
        },
        "accepted_shadow_decision_count": accepted,
        "blocked_shadow_decision_count": len(scenario_results) - accepted,
        "blocker_frequencies": dict(sorted(blocker_frequencies.items())),
        "zero_write_facts": zero_write_facts,
    }
    if not all(item["passed"] for key in (
        "scenario_results", "restart_results", "replay_results",
        "concurrency_results", "crash_results",
    ) for item in core[key]):
        raise AssertionError("deterministic shadow assurance verifier observed a failed case")
    if any(zero_write_facts.values()):
        raise PermissionError("deterministic shadow assurance verifier observed forbidden authority")
    return {**core, "verifier_result_sha256": sha256_payload(core)}


def validate_offline_assurance_verifier_result(
    result: Mapping[str, object],
    *,
    repository_sha: str | None = None,
) -> None:
    expected_sha = str(result.get("repository_sha")) if repository_sha is None else repository_sha
    expected = run_offline_assurance_verifier(expected_sha)
    if dict(result) != expected:
        raise ValueError("shadow assurance verifier result does not match executable verification")


def passed_case_count(result: Mapping[str, object], key: str) -> int:
    rows = result.get(key)
    if not isinstance(rows, (tuple, list)):
        raise ValueError(f"verifier {key} must be a case sequence")
    case_ids = [row.get("case_id") for row in rows if isinstance(row, Mapping)]
    if len(case_ids) != len(rows) or len(set(case_ids)) != len(case_ids):
        raise ValueError(f"verifier {key} contains missing or duplicate case IDs")
    return sum(1 for row in rows if row.get("passed") is True)


__all__ = [
    "POSTGRESQL_VERIFIER_NOT_EXECUTED",
    "SHADOW_VERIFIER_VERSION",
    "passed_case_count",
    "run_offline_assurance_verifier",
    "validate_offline_assurance_verifier_result",
]
