from __future__ import annotations

import dataclasses
import hashlib
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.models import AccountingMode, OrderSide, OrderType, TimeInForce
from secure_eval_wrapper.paper.enums import PaperOrderState
from secure_eval_wrapper.paper.models import PaperMarketDataEvidence, PaperOrderSubmission
from secure_eval_wrapper.paper.reservations import PRICE_CALCULATOR_VERSION, calculate_reservation, select_risk_price
from secure_eval_wrapper.paper.venues.internal import InternalPaperVenue
from phase7_test_support import ID, run_id

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
H = sha256_payload("phase7-fifth-audit")


def evidence(*, price: str = "50000", source_kind: str = "fixture") -> PaperMarketDataEvidence:
    value = ID
    return PaperMarketDataEvidence(
        value,
        "internal",
        value.provider_instrument_id,
        "bar_close",
        "fifth-audit-price",
        T0,
        T0,
        True,
        "accepted",
        H,
        sha256_payload({"normalized": "fifth-audit-price"}),
        price=Decimal(price),
        price_type="close",
        quote_currency="USDT",
        source_kind=source_kind,
    )


def submission(*, order_type: OrderType = OrderType.MARKET, reference: str = "100", limit: str | None = None) -> PaperOrderSubmission:
    value = ID
    economics = sha256_payload({
        "series_identity": value.as_dict(),
        "side": OrderSide.BUY,
        "order_type": order_type,
        "time_in_force": TimeInForce.GTC,
        "accounting_mode": AccountingMode.SPOT,
        "quantity": Decimal("1"),
        "limit_price": None if limit is None else Decimal(limit),
        "stop_price": None,
    })
    return PaperOrderSubmission(
        run_id(), run_id(), run_id(), run_id(), "fifth-client", "fifth-client", value,
        OrderSide.BUY, order_type, TimeInForce.GTC, AccountingMode.SPOT,
        Decimal("1"), Decimal(reference), Decimal(reference), T0, economics,
        state=PaperOrderState.PREPARED,
        limit_price=None if limit is None else Decimal(limit),
    )


class AuthoritativePriceTests(unittest.TestCase):
    def test_low_reference_high_limit_attack_uses_limit_notional(self):
        intent = submission(order_type=OrderType.LIMIT, reference="100", limit="50000")
        selection = select_risk_price(intent, evidence(price="100"), maximum_adverse_slippage_bps=Decimal("200"))
        self.assertEqual(selection.worst_case_order_price, Decimal("50000"))
        self.assertEqual(selection.risk_notional, Decimal("50000"))
        self.assertGreater(selection.risk_notional, Decimal("1000"))
        self.assertEqual(selection.price_calculator_version, PRICE_CALCULATOR_VERSION)

    def test_market_evidence_overrides_low_intent_reference(self):
        intent = submission(reference="100")
        selection = select_risk_price(intent, evidence(price="50000"), maximum_adverse_slippage_bps=Decimal("200"))
        self.assertEqual(selection.market_evidence_price, Decimal("50000"))
        self.assertEqual(selection.risk_reference_price, Decimal("51000"))
        self.assertEqual(selection.risk_notional, Decimal("51000"))
        self.assertEqual(selection.price_deviation_bps, Decimal("9980"))

    def test_reservation_consumes_persisted_worst_case_price(self):
        raw = submission(order_type=OrderType.LIMIT, reference="100", limit="50000")
        selection = select_risk_price(raw, evidence(price="100"))
        bound = dataclasses.replace(
            raw,
            submitted_notional=selection.risk_notional,
            market_evidence_price=selection.market_evidence_price,
            risk_reference_price=selection.risk_reference_price,
            worst_case_order_price=selection.worst_case_order_price,
            risk_notional=selection.risk_notional,
            reservation_notional=selection.reservation_notional,
            price_deviation_bps=selection.price_deviation_bps,
            price_source_sha256=selection.price_source_sha256,
            price_calculator_version=selection.price_calculator_version,
        )
        requirement = calculate_reservation(bound, maximum_fee_bps=Decimal("20"))
        self.assertEqual(requirement.risk_notional, bound.risk_notional)
        self.assertEqual(requirement.reserve_price, Decimal("50000"))
        self.assertEqual(requirement.amount, Decimal("50100"))


class ExactInternalEconomicsTests(unittest.TestCase):
    def test_custom_fee_event_persists_exact_replay_economics(self):
        order = submission(reference="100")
        venue = InternalPaperVenue(initial_balances={"USDT": Decimal("1000")}, fee_bps=Decimal("20"))
        venue.submit_order(order)
        venue.acknowledge(order.client_order_id, T0)
        _, fill, _ = venue.fill(order.client_order_id, Decimal("1"), Decimal("100"), T0, venue_fill_id="fee-20")
        event = next(value for value in venue.events if value["kind"] == "fill")
        self.assertEqual(fill.fee_amount, Decimal("0.2"))
        self.assertEqual(event["details"]["fee"], "0.2")
        self.assertEqual(event["details"]["reservation_consumed"], "100.2")
        self.assertIn("balance_deltas", event["details"])
        self.assertEqual(len(venue.IMPLEMENTATION_SHA256), 64)


class MigrationIntegrityTests(unittest.TestCase):
    def test_0020_contains_required_database_authorities(self):
        root = Path(__file__).resolve().parents[2]
        path = root / "open-core" / "db" / "migrations" / "0020_phase7_price_terminal_and_expiry_integrity.sql"
        payload = path.read_bytes()
        text = payload.decode("utf-8-sig")
        for required in (
            "risk_notional", "terminal_disposition", "paper_expiry_recovery_records",
            "paper_internal_venue_economics", "closed paper order budget cannot reopen",
            "phase7_guard_order_projection_update", "normalized_record_sha256",
        ):
            self.assertIn(required, text)
        self.assertEqual(len(hashlib.sha256(payload).hexdigest()), 64)


if __name__ == "__main__":
    unittest.main()
