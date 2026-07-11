"""Phase 7 paper-trading enum contracts."""
from enum import Enum

class PaperEnvironment(str, Enum):
    SIMULATED="simulated"; PAPER_INTERNAL="paper_internal"; PAPER_EXCHANGE_SANDBOX="paper_exchange_sandbox"; LIVE="live"
class PaperProvider(str, Enum):
    INTERNAL="internal"; OKX_DEMO="okx_demo"
class PaperRunState(str, Enum):
    CREATED="created"; APPROVED="approved"; RUNNING="running"; PAUSED="paused"; COMPLETED="completed"; FAILED="failed"; KILLED="killed"
class ApprovalState(str, Enum):
    VALID="valid"; CONSUMED="consumed"; EXPIRED="expired"; REVOKED="revoked"
class PreflightStatus(str, Enum):
    PASSED="passed"; FAILED="failed"
class PaperOrderState(str, Enum):
    SUBMITTED="submitted"; PENDING_ACK="pending_ack"; ACKNOWLEDGED="acknowledged"; PARTIALLY_FILLED="partially_filled"; FILLED="filled"; CANCEL_PENDING="cancel_pending"; CANCELLED="cancelled"; REJECTED="rejected"; EXPIRED="expired"; SUBMISSION_UNKNOWN="submission_unknown"; PENDING_RECOVERY="pending_recovery"
class VenueOrderState(str, Enum):
    PENDING_ACK="pending_ack"; ACKNOWLEDGED="acknowledged"; PARTIALLY_FILLED="partially_filled"; FILLED="filled"; CANCEL_PENDING="cancel_pending"; CANCELLED="cancelled"; REJECTED="rejected"; EXPIRED="expired"; UNKNOWN_PENDING_RECOVERY="unknown_pending_recovery"
class ReconciliationStatus(str, Enum):
    RECONCILED="reconciled"; WARNING="warning"; BLOCKED="blocked"; UNKNOWN="unknown"
class ReconciliationDifferenceType(str, Enum):
    LOCAL_ORDER_MISSING_AT_VENUE="local_order_missing_at_venue"; VENUE_ORDER_MISSING_LOCALLY="venue_order_missing_locally"; ORDER_STATUS_MISMATCH="order_status_mismatch"; QUANTITY_MISMATCH="quantity_mismatch"; FILL_MISSING_LOCALLY="fill_missing_locally"; FILL_MISSING_AT_VENUE="fill_missing_at_venue"; DUPLICATE_FILL="duplicate_fill"; BALANCE_MISMATCH="balance_mismatch"; POSITION_MISMATCH="position_mismatch"; FEE_MISMATCH="fee_mismatch"; CURRENCY_MISMATCH="currency_mismatch"; STALE_VENUE_SNAPSHOT="stale_venue_snapshot"; STALE_LOCAL_SNAPSHOT="stale_local_snapshot"; SEQUENCE_GAP="sequence_gap"; UNKNOWN_SUBMISSION="unknown_submission"; UNSUPPORTED_VENUE_FIELD="unsupported_venue_field"; ACCOUNT_MODE_MISMATCH="account_mode_mismatch"
class KillSwitchState(str, Enum):
    ARMED="armed"; TRIGGERED="triggered"; CANCELLING="cancelling"; KILLED="killed"; RESET_PENDING="reset_pending"; RESET="reset"
class KillSwitchReason(str, Enum):
    MANUAL="manual_local_trigger"; MAX_DAILY_LOSS="max_daily_loss"; MAX_DRAWDOWN="max_drawdown"; MAX_EXPOSURE="max_exposure"; NON_POSITIVE_EQUITY="non_positive_equity"; STALE_MARKET_DATA="stale_market_data"; STALE_ACCOUNT="stale_account_snapshot"; UNKNOWN_ORDER="unresolved_unknown_order"; RECONCILIATION="reconciliation_failure"; TRANSPORT_FAILURES="consecutive_transport_failures"; CLOCK_SKEW="clock_skew_breach"; CREDENTIALS="credential_verification_failure"; ENDPOINT="endpoint_mismatch"; SEQUENCE_GAP="sequence_gap"; CRITICAL_INCIDENT="monitoring_critical_incident"; PERSISTENCE="persistence_failure"; ACCOUNT_MODE="unexpected_venue_account_mode"; UNAPPROVED_STATE="existing_unapproved_position_or_order"; EMERGENCY="operator_defined_emergency_trigger"
class CredentialSourceType(str, Enum):
    ENVIRONMENT="environment"; INJECTED_TEST="injected_test"
class AccountSnapshotStatus(str, Enum):
    FRESH="fresh"; STALE="stale"; INCOMPLETE="incomplete"
class RecoveryStatus(str, Enum):
    STARTED="started"; RECOVERED="recovered"; PAUSED="paused"; KILLED="killed"; FAILED="failed"
class TransportRequestType(str, Enum):
    VERIFY_CREDENTIALS="verify_credentials"; ACCOUNT_MODE="account_mode"; INSTRUMENTS="instruments"; BALANCES="balances"; POSITIONS="positions"; SUBMIT="submit"; CANCEL="cancel"; QUERY_ORDER="query_order"; OPEN_ORDERS="open_orders"; RECENT_ORDERS="recent_orders"; FILLS="fills"
class TransportResultType(str, Enum):
    SUCCEEDED="succeeded"; REJECTED="rejected"; TIMEOUT="timeout"; UNKNOWN="unknown"; RATE_LIMITED="rate_limited"; AUTHENTICATION_FAILED="authentication_failed"; MALFORMED="malformed"
class InternalPaperFaultType(str, Enum):
    ACK_TIMEOUT="acknowledgement_timeout"; UNKNOWN_SUBMISSION="unknown_submission_result"; DUPLICATE_ACK="duplicate_acknowledgement"; DUPLICATE_FILL="duplicate_fill"; DELAYED_FILL="delayed_fill"; CANCEL_TIMEOUT="cancel_timeout"; BALANCE_LAG="balance_snapshot_lag"; POSITION_LAG="position_snapshot_lag"; SEQUENCE_GAP="venue_sequence_gap"; RECONNECT="reconnect"; STALE_SNAPSHOT="stale_account_snapshot"
