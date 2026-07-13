-- Phase 7 fifth-audit cancellation terminal-accounting repair.
-- Migrations 0001 through 0020 remain immutable. PostgreSQL remains the sole runtime authority.

ALTER TABLE execution.paper_cancel_outbox
    ADD COLUMN IF NOT EXISTS terminal_evidence_sha256 text
        CHECK (terminal_evidence_sha256 IS NULL OR terminal_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN IF NOT EXISTS terminal_order_observation_id uuid
        REFERENCES execution.paper_venue_order_observations(venue_order_observation_id),
    ADD COLUMN IF NOT EXISTS accounting_complete_at_confirmation boolean;

COMMENT ON COLUMN execution.paper_cancel_outbox.terminal_evidence_sha256 IS
    'Exact terminal venue query or response evidence. Cancel confirmation does not imply fill accounting completeness.';
COMMENT ON COLUMN execution.paper_cancel_outbox.terminal_order_observation_id IS
    'Exact append-only terminal venue order observation supporting cancellation or supersession.';
COMMENT ON COLUMN execution.paper_cancel_outbox.accounting_complete_at_confirmation IS
    'Snapshot of terminal fill, fee, and accounting completeness when terminal cancellation evidence was recorded.';
