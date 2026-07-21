"""Immutable canonical migration identity shared by Phase 8 bootstrap and shadow assurance."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class MigrationCatalogEntry:
    migration_id: str
    filename: str
    sha256: str


_CATALOG_VALUES = (
    ("0001_initial_schema", "598486e6af2eed4559564593adc0b66deff9e21ea91dbda560980c208a2950c5"),
    ("0002_schema_migrations", "36c91efa851e10fcc6039ebd8715af1c985237af6ff556e6943e10329458f76f"),
    ("0003_data_quality_quarantine", "d0b32a72ad98a9d1361bfa57770a9b7d58ae2323816e8b3d77c3d05f66b35a9a"),
    ("0004_reconciliation_persistence", "efe77fa89b25f90dea3f49a70b22b8cc376c434333abbff6fd17cc9eb75fd7ba"),
    ("0005_trade_funding_instrument_hardening", "b18d66f37df55923a1e1cfba709784de55ab90d0c5ff250b8d683dc6029f9d48"),
    ("0006_phase2_final_hardening", "af507329f29e63ab260317b879da5e82917aafd7368d692b343a09ccafdace5d"),
    ("0007_alpha_signal_library", "0a355d3238afcf8691b5366e46332c3e1e6862a9ed574e740e3435479d8883a4"),
    ("0008_phase3_phase4_audit_repairs", "a59dff645009c117a5146d2bd4102a9ed048126ca77b61566f8d31bf1fcba64b"),
    ("0009_phase5_simulated_execution_backtesting", "9b49718ee48e45dda42916568f815723f94578eff814ffe0e0b236aa3523c0d5"),
    ("0010_phase5_second_audit_repairs", "1387ccf65a7a7ac8c2c7b4d93de8443e47963740dcefbf30a0ae248ea5e978a0"),
    ("0011_phase5_run_membership_repairs", "0c0a0ed26ec7419e773e69e8c1ab07d4e220377059e0bf2358b519055e6540a8"),
    ("0012_phase5_run_scoped_projection_repairs", "2a55979b6419bc3eb464d2374d68a40d8cc559fac1a984d2ebcada5974d82d4d"),
    ("0013_phase6_monitoring_simulated_fix", "5e7eb61540507ce4c0f7fb92b78fdebf2fb551a770c129b45d82d19cff592761"),
    ("0014_phase6_first_audit_repairs", "30971466069b6dbcb29f7b08568ebd7791097d996ccbc742cb7f3aa8096ba4fe"),
    ("0015_phase6_concurrency_and_audit_integrity", "5ae8bcfa8db52110978dddd4864700dac6a8e549000dde50065969910b24aec1"),
    ("0016_phase7_safe_paper_trading", "866179dc6a95bf65a416c62d891cd06ce34cf28bceaeb8f29223ad70ef863b0f"),
    ("0017_phase7_durable_paper_recovery", "c2a2e4ca347775898c11443da89552685b0d723a094335719ef07515e4639302"),
    ("0018_phase7_recovery_state_machine_integrity", "c49fad9ed9b5cf3eeee6ae071f6a8b6e4d73c67c80571b09030c4b21b519d59d"),
    ("0019_phase7_venue_event_and_accounting_integrity", "7a139eb65b7ed66fd16b2e7e20794e57f28dc59ab5992c468cb21bae22d68457"),
    ("0020_phase7_price_terminal_and_expiry_integrity", "ce24b36b2ff6e276ce69edeef3044ab7f891e154fa389d55e53619deab990ad5"),
    ("0021_phase7_cancel_terminal_accounting_integrity", "a9a088b497addb45353a3b906caafd5e3532bb389a8ddbfe18626d26597c7506"),
    ("0022_phase8_guarded_live_foundation", "b01c0c0c7801247594ee75009055f899c8902b6cfa1b44ed91ad8451e478e434"),
    ("0023_phase8a_authority_recovery_and_cli_integrity", "cd06abb25ef7a9c178b5aad8c6378f982c879b2e3b52eb4667e067554b987eef"),
    ("0024_phase8a_evidence_reconciliation_metadata_integrity", "3f5671e34d312770dd05763116ce0102da1534061df887dc0c6754f0cc48b214"),
    ("0025_phase8a_okx_credential_permission_authority", "773b2cc2cfb8fcdc9cd9ce022904c096e1f9520915ad8549c5a92d3067d7fc61"),
    ("0026_phase8b_authenticated_readonly_preflight", "698772fb68c5c4981256682d064c3be641193ab10c8dbf55e1a5b390ca7c504a"),
)

CANONICAL_MIGRATION_CATALOG = tuple(
    MigrationCatalogEntry(migration_id, migration_id + ".sql", digest)
    for migration_id, digest in _CATALOG_VALUES
)
CANONICAL_MIGRATION_ROWS = tuple(
    (entry.migration_id, entry.filename, entry.sha256)
    for entry in CANONICAL_MIGRATION_CATALOG
)
EXPECTED_MIGRATION_CATALOG: Mapping[str, str] = MappingProxyType({
    entry.migration_id: entry.sha256 for entry in CANONICAL_MIGRATION_CATALOG
})
LATEST_MIGRATION = CANONICAL_MIGRATION_CATALOG[-1].migration_id
MIGRATION_0026_SHA256 = CANONICAL_MIGRATION_CATALOG[-1].sha256

def validate_migration_catalog_rows(
    rows: tuple[tuple[str, str, str], ...],
) -> tuple[tuple[str, str, str], ...]:
    """Accept only the exact ordered immutable 0001-0026 catalog."""
    if type(rows) is not tuple or any(
        type(row) is not tuple
        or len(row) != 3
        or any(type(value) is not str for value in row)
        for row in rows
    ):
        raise PermissionError("migration catalog rows must be exact string tuples")
    if rows != CANONICAL_MIGRATION_ROWS:
        raise PermissionError(
            "shadow database must expose the exact ordered immutable 0001-0026 catalog"
        )
    return rows


__all__ = [
    "CANONICAL_MIGRATION_CATALOG",
    "CANONICAL_MIGRATION_ROWS",
    "EXPECTED_MIGRATION_CATALOG",
    "LATEST_MIGRATION",
    "MIGRATION_0026_SHA256",
    "MigrationCatalogEntry",
    "validate_migration_catalog_rows",
]
