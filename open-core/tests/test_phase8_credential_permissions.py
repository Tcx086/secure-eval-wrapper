from __future__ import annotations

import unittest
from uuid import uuid4

from secure_eval_wrapper.live.preflight import (
    OperationalPreflightError,
    collect_operational_preflight_evidence,
)
from secure_eval_wrapper.live.venues.okx_live import OkxProductionSpotAdapter

from test_phase8_guarded_live import (
    COMMIT,
    OKX_UID,
    T0,
    account,
    config,
    credential,
    exact_okx_bundle,
)


class NoDatabaseAccess:
    def cursor(self):
        raise AssertionError("permission rejection must happen before PostgreSQL access")


def account_config_payload(permission: str | None = "read_only") -> dict:
    row = {
        "uid": OKX_UID,
        "mainUid": OKX_UID,
        "acctLv": "1",
        "posMode": "long_short_mode",
        "autoLoan": "false",
        "enableSpotBorrow": "false",
    }
    if permission is not None:
        row["perm"] = permission
    return {"code": "0", "data": [row]}


def collect_before_database(*, response_permission: str, expected_permissions: tuple[str, ...]):
    run = uuid4()
    return collect_operational_preflight_evidence(
        connection=NoDatabaseAccess(),
        live_run_id=run,
        configuration=config(),
        credential_reference=credential(permissions=expected_permissions),
        account_snapshot=account(run),
        market_evidence=None,
        reconciliation=None,
        kill_switch=None,
        okx_bundle=exact_okx_bundle(run, "preflight", perm=response_permission),
        expected_repository_commit_sha=COMMIT,
        collected_at_utc=T0,
    )


class ExactOkxCredentialPermissionTests(unittest.TestCase):
    def test_read_only_response_parses_to_exact_provider_and_normalized_sets(self):
        parsed = OkxProductionSpotAdapter.parse_account_config(account_config_payload())
        self.assertEqual(parsed["provider_permissions"], ("read_only",))
        self.assertEqual(parsed["normalized_permissions"], ("read",))

    def test_parser_accepts_recognized_sets_without_declaring_them_phase8a_safe(self):
        parsed = OkxProductionSpotAdapter.parse_account_config(
            account_config_payload("read_only,withdraw")
        )
        self.assertEqual(parsed["provider_permissions"], ("read_only", "withdraw"))
        self.assertEqual(parsed["normalized_permissions"], ("read", "withdraw"))

    def test_missing_unknown_malformed_whitespace_and_duplicate_permissions_fail(self):
        attacked = (
            None,
            "",
            "mystery",
            "read_only,",
            ",read_only",
            "read_only,,trade",
            " read_only",
            "read_only ",
            "read_only, trade",
            "read_only,read_only",
            "READ_ONLY",
        )
        for permission in attacked:
            with self.subTest(permission=permission), self.assertRaises(ValueError):
                OkxProductionSpotAdapter.parse_account_config(
                    account_config_payload(permission)
                )

    def test_phase8a_rejects_every_actual_trade_or_withdraw_set_before_database(self):
        for permission in ("read_only,withdraw", "read_only,trade", "trade", "withdraw"):
            with self.subTest(permission=permission), self.assertRaises((PermissionError, OperationalPreflightError)):
                collect_before_database(
                    response_permission=permission,
                    expected_permissions=("read",),
                )

    def test_caller_read_only_claim_cannot_hide_actual_withdraw(self):
        with self.assertRaises((PermissionError, OperationalPreflightError)):
            collect_before_database(
                response_permission="read_only,withdraw",
                expected_permissions=("read",),
            )

    def test_caller_trade_claim_must_not_match_actual_read_only(self):
        with self.assertRaises(OperationalPreflightError):
            collect_before_database(
                response_permission="read_only",
                expected_permissions=("trade",),
            )

    def test_internal_spot_trade_label_is_not_provider_permission_proof(self):
        with self.assertRaises(OperationalPreflightError):
            collect_before_database(
                response_permission="read_only",
                expected_permissions=("spot_trade",),
            )


if __name__ == "__main__":
    unittest.main()