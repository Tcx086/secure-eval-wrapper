"""PostgreSQL repositories for Phase 6 monitoring and simulated FIX records."""
from __future__ import annotations

from contextlib import contextmanager

from secure_eval_wrapper.storage.postgres.alpha_signal_base import _PostgresRepositoryBase, _json_param


class Phase6ConflictError(RuntimeError):
    pass


class PostgresPhase6Repository(_PostgresRepositoryBase):
    @contextmanager
    def _write_scope(self):
        if self.commit_on_write:
            with self.transaction():
                yield
        else:
            yield

    def _strict(self, table, id_column, row, *, hash_column="record_sha256"):
        columns = tuple(row)
        params = tuple(_json_param(row[name]) if name.endswith("_jsonb") else row[name] for name in columns)
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT DO NOTHING RETURNING {id_column}, {hash_column}",
                params,
            )
            stored = cursor.fetchone()
            if stored is None:
                cursor.execute(f"SELECT {id_column}, {hash_column} FROM {table} WHERE {id_column}=%s", (row[id_column],))
                stored = cursor.fetchone()
            if stored is None or str(stored[1]) != str(row[hash_column]):
                raise Phase6ConflictError(f"stored {table} content conflicts with deterministic identity")
            return stored[0]
        finally:
            cursor.close()

    def record_monitoring_run(self, value):
        row = {
            "monitoring_run_id": value.monitoring_run_id, "as_of_utc": value.as_of_utc,
            "monitored_identity": value.reference.monitored_identity, "monitored_run_id": value.reference.monitored_run_id,
            "configuration_sha256": value.configuration_sha256, "stable_input_sha256": value.stable_input_sha256,
            "implementation_code_sha256": value.provenance.implementation_code_sha256,
            "repository_commit_sha": value.provenance.repository_commit_sha,
            "overall_status": value.overall_status.value, "parent_ids": list(value.parent_ids),
            "public_provenance_jsonb": dict(value.provenance.operational_metadata), "record_sha256": value.record_sha256,
        }
        with self._write_scope():
            return self._strict("monitoring.monitoring_runs", "monitoring_run_id", row)

    def record_health_check_result(self, value):
        row = {
            "health_check_result_id": value.health_check_result_id, "monitoring_run_id": value.monitoring_run_id,
            "evaluation_at_utc": value.evaluation_at_utc, "category": value.category.value,
            "component": value.component, "check_name": value.check_name, "status": value.status.value,
            "health_status": value.health_status.value, "severity": value.severity.value,
            "reason_code": value.reason_code, "explanation": value.explanation,
            "observed_value_jsonb": value.observed_value, "configured_threshold_jsonb": value.configured_threshold,
            "configuration_sha256": value.configuration_sha256, "stable_input_sha256": value.stable_input_sha256,
            "implementation_code_sha256": value.provenance.implementation_code_sha256,
            "repository_commit_sha": value.provenance.repository_commit_sha, "parent_ids": list(value.parent_ids),
            "record_sha256": value.record_sha256,
        }
        with self._write_scope():
            return self._strict("monitoring.health_check_results", "health_check_result_id", row)

    def record_health_snapshot(self, value):
        row = {
            "health_snapshot_id": value.health_snapshot_id, "monitoring_run_id": value.monitoring_run_id,
            "evaluation_at_utc": value.evaluation_at_utc, "category": None if value.category is None else value.category.value,
            "component": value.component, "health_status": value.health_status.value,
            "causing_check_ids": list(value.causing_check_ids), "reason_code": value.reason_code,
            "explanation": value.explanation, "configuration_sha256": value.configuration_sha256,
            "stable_input_sha256": value.stable_input_sha256,
            "implementation_code_sha256": value.provenance.implementation_code_sha256,
            "repository_commit_sha": value.provenance.repository_commit_sha, "parent_ids": list(value.parent_ids),
            "record_sha256": value.record_sha256,
        }
        with self._write_scope():
            return self._strict("monitoring.health_snapshots", "health_snapshot_id", row)

    def record_monitoring_event(self, value):
        row = {
            "monitoring_event_id": value.monitoring_event_id, "run_id": None, "event_category": value.category.value,
            "severity": value.severity.value, "event_time_utc": value.evaluation_at_utc, "symbol": None,
            "message": value.explanation, "details_jsonb": dict(value.details), "monitoring_run_id": value.monitoring_run_id,
            "component": value.component, "event_type": value.event_type.value, "reason_code": value.reason_code,
            "configuration_sha256": value.configuration_sha256, "stable_input_sha256": value.stable_input_sha256,
            "implementation_code_sha256": value.provenance.implementation_code_sha256,
            "repository_commit_sha": value.provenance.repository_commit_sha, "parent_ids": list(value.parent_ids),
            "record_sha256": value.record_sha256,
        }
        with self._write_scope():
            return self._strict("monitoring.monitoring_events", "monitoring_event_id", row)

    def record_incident(self, value):
        params = (
            value.incident_id, value.category.value, value.component, value.check_name, value.reason_code,
            value.monitored_identity, value.state.value, value.severity.value, value.episode_started_at_utc,
            value.latest_at_utc, value.resolved_at_utc, value.occurrence_count, value.configuration_sha256,
            value.stable_input_sha256, value.record_sha256,
        )
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO monitoring.incidents (incident_id,category,component,check_name,reason_code,monitored_identity,state,severity,episode_started_at_utc,latest_at_utc,resolved_at_utc,occurrence_count,configuration_sha256,stable_input_sha256,record_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (incident_id) DO UPDATE SET state=EXCLUDED.state,severity=EXCLUDED.severity,latest_at_utc=EXCLUDED.latest_at_utc,resolved_at_utc=EXCLUDED.resolved_at_utc,occurrence_count=EXCLUDED.occurrence_count,configuration_sha256=EXCLUDED.configuration_sha256,stable_input_sha256=EXCLUDED.stable_input_sha256,record_sha256=EXCLUDED.record_sha256 RETURNING incident_id",
                params,
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def record_incident_occurrence(self, value):
        row = {
            "incident_occurrence_id": value.incident_occurrence_id, "incident_id": value.incident_id,
            "monitoring_run_id": value.monitoring_run_id, "health_check_result_id": value.health_check_result_id,
            "occurred_at_utc": value.occurred_at_utc, "record_sha256": value.record_sha256,
        }
        with self._write_scope():
            return self._strict("monitoring.incident_occurrences", "incident_occurrence_id", row)

    def record_fix_session(self, session, updated_at_utc, *, expected_state_version=None, expected_record_sha256=None, last_transition_event_id=None):
        current_hash = session_record_hash(session)
        last_event_id = last_transition_event_id
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT state_version,record_sha256 FROM monitoring.fix_sessions WHERE fix_session_id=%s FOR UPDATE", (session.fix_session_id,))
            stored = cursor.fetchone()
            if stored is None:
                if expected_state_version not in (None, 0) or expected_record_sha256 is not None:
                    raise Phase6ConflictError("stale writer expected an existing FIX session projection")
                cursor.execute(
                    "INSERT INTO monitoring.fix_sessions (fix_session_id,session_key,sender_comp_id,target_comp_id,state,next_inbound_seq_num,next_outbound_seq_num,heartbeat_interval_seconds,test_request_grace_seconds,disconnect_timeout_seconds,last_inbound_at_utc,last_outbound_at_utc,pending_test_request_id,pending_test_deadline_at_utc,test_request_grace_expired,configuration_sha256,record_sha256,state_version,previous_state_hash,last_transition_event_id,updated_at_utc) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,NULL,%s,%s) RETURNING state_version,record_sha256",
                    (session.fix_session_id, session.configuration.session_key, session.configuration.sender_comp_id,
                     session.configuration.target_comp_id, session.state.value, session.next_inbound_seq_num,
                     session.next_outbound_seq_num, session.configuration.heartbeat_interval_seconds,
                     session.configuration.test_request_grace_seconds, session.configuration.disconnect_timeout_seconds,
                     session.last_inbound_at_utc, session.last_outbound_at_utc, session.pending_test_request_id,
                     session.pending_test_deadline_at_utc, session.test_request_grace_expired,
                     session.configuration.config_sha256, current_hash,
                     last_event_id, updated_at_utc),
                )
            else:
                expected_version = session.persisted_state_version if expected_state_version is None else expected_state_version
                expected_hash = session.persisted_record_sha256 if expected_record_sha256 is None else expected_record_sha256
                if expected_version is None or expected_hash is None or int(stored[0]) != int(expected_version) or str(stored[1]) != str(expected_hash):
                    raise Phase6ConflictError("stale FIX session projection writer")
                cursor.execute(
                    "UPDATE monitoring.fix_sessions SET state=%s,next_inbound_seq_num=%s,next_outbound_seq_num=%s,last_inbound_at_utc=%s,last_outbound_at_utc=%s,pending_test_request_id=%s,pending_test_deadline_at_utc=%s,test_request_grace_expired=%s,record_sha256=%s,state_version=state_version+1,previous_state_hash=%s,last_transition_event_id=%s,updated_at_utc=%s WHERE fix_session_id=%s AND state_version=%s AND record_sha256=%s RETURNING state_version,record_sha256",
                    (session.state.value, session.next_inbound_seq_num, session.next_outbound_seq_num,
                     session.last_inbound_at_utc, session.last_outbound_at_utc, session.pending_test_request_id,
                     session.pending_test_deadline_at_utc, session.test_request_grace_expired, current_hash, stored[1],
                     last_event_id, updated_at_utc, session.fix_session_id,
                     expected_version, expected_hash),
                )
                if cursor.rowcount != 1:
                    raise Phase6ConflictError("stale FIX session projection writer")
            version, record_hash = cursor.fetchone()
            session.persisted_state_version = int(version)
            session.persisted_record_sha256 = str(record_hash)
            return session.fix_session_id
        finally:
            cursor.close()

    def record_fix_session_event(self, session, event):
        existing = self._fetchone("SELECT fix_session_event_id,record_sha256 FROM monitoring.fix_session_events WHERE fix_session_event_id=%s", (event.event_id,))
        if existing is not None:
            if str(existing["record_sha256"]) != event.record_sha256:
                raise Phase6ConflictError("stored FIX session event conflicts with deterministic identity")
            return existing["fix_session_event_id"]
        row = {
            "fix_session_event_id": event.event_id, "run_id": None, "session_id": session.configuration.session_key,
            "event_type": event.event_type.value, "sequence_number": event.sequence_number,
            "event_time_utc": event.event_at_utc, "message_type": None,
            "payload_jsonb": {"reason_code": event.reason_code}, "simulated": True,
            "fix_session_id": session.fix_session_id, "prior_state": event.prior_state.value,
            "new_state": event.new_state.value, "reason_code": event.reason_code,
            "parent_message_id": event.parent_message_id, "transition_sequence": event.transition_sequence,
            "previous_event_sha256": event.previous_event_sha256, "record_sha256": event.record_sha256,
        }
        with self._write_scope():
            return self._strict("monitoring.fix_session_events", "fix_session_event_id", row)

    def record_fix_message(self, fix_session_id, direction, message, processing_time_utc, raw_bytes):
        import hashlib
        from secure_eval_wrapper.data_collection.hashing import sha256_payload
        from secure_eval_wrapper.fix.codec import FixCodec

        encoded = FixCodec(preserve_unknown_tags=True).encode(message) if raw_bytes is None else raw_bytes
        body = message.body_length
        if body is None:
            second = encoded.find(b"\x01", encoded.find(b"\x01") + 1)
            body = encoded.rfind(b"10=") - (second + 1)
        checksum = message.checksum if message.checksum is not None else sum(encoded[:encoded.rfind(b"10=")]) % 256
        row = {
            "fix_message_id": message.fix_message_id, "fix_session_id": fix_session_id,
            "direction": getattr(direction, "value", direction), "msg_type": message.msg_type.value,
            "msg_seq_num": message.msg_seq_num, "sending_time_utc": message.sending_time_utc,
            "processing_time_utc": processing_time_utc, "validation_status": "valid",
            "rejection_reason": None, "rejection_code": None, "body_length": body, "checksum": checksum,
            "business_identity_sha256": message.business_identity_sha256,
            "replay_identity_sha256": message.replay_identity_sha256,
            "raw_message_sha256": hashlib.sha256(encoded).hexdigest(),
            "parsed_fields_jsonb": {str(k): v for k, v in {**message.fields, **message.extensions}.items()},
        }
        row["record_sha256"] = sha256_payload(row)
        columns = tuple(row)
        params = tuple(_json_param(row[name]) if name.endswith("_jsonb") else row[name] for name in columns)
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"INSERT INTO monitoring.fix_messages ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT DO NOTHING RETURNING fix_message_id", params)
            inserted = cursor.fetchone()
            if inserted:
                return inserted[0]
            cursor.execute("SELECT fix_message_id,replay_identity_sha256 FROM monitoring.fix_messages WHERE fix_session_id=%s AND direction=%s AND msg_seq_num=%s AND validation_status='valid'", (fix_session_id, row["direction"], message.msg_seq_num))
            stored = cursor.fetchone()
            if stored and str(stored[1]) == message.replay_identity_sha256:
                return stored[0]
            raise Phase6ConflictError("same FIX sequence contains changed economic or administrative content")
        finally:
            cursor.close()

    def record_rejected_fix_observation(self, observation):
        row = {
            "fix_message_id": observation.observation_id, "fix_session_id": observation.fix_session_id,
            "direction": observation.direction.value, "msg_type": observation.msg_type,
            "msg_seq_num": observation.msg_seq_num, "sending_time_utc": None,
            "processing_time_utc": observation.processing_time_utc, "validation_status": "rejected",
            "rejection_reason": observation.rejection_reason, "rejection_code": observation.rejection_code,
            "body_length": None, "checksum": None, "business_identity_sha256": None,
            "replay_identity_sha256": None, "raw_message_sha256": observation.raw_message_sha256,
            "parsed_fields_jsonb": {str(k): v for k, v in observation.parsed_header_fields.items()},
            "record_sha256": observation.record_sha256,
        }
        with self._write_scope():
            return self._strict("monitoring.fix_messages", "fix_message_id", row)

    def record_fix_order_link(self, value):
        row = {"fix_order_link_id": value.fix_order_link_id, "fix_session_id": value.fix_session_id, "cl_ord_id": value.cl_ord_id, "orig_cl_ord_id": value.orig_cl_ord_id, "order_intent_id": value.order_intent_id, "order_id": value.order_id, "fill_id": value.fill_id, "execution_report_message_id": value.execution_report_message_id, "business_identity_sha256": value.business_identity_sha256, "record_sha256": value.record_sha256}
        with self._write_scope():
            return self._strict("monitoring.fix_order_links", "fix_order_link_id", row)

    def record_latency_sample(self, value, monitoring_run_id=None):
        row = {"latency_sample_id": value.latency_sample_id, "monitoring_run_id": monitoring_run_id, "fix_session_id": value.fix_session_id, "fix_message_id": value.fix_message_id, "stage": value.stage.value, "simulated_start_utc": value.simulated_start_utc, "simulated_end_utc": value.simulated_end_utc, "duration_microseconds": value.duration_microseconds, "threshold_microseconds": value.threshold_microseconds, "breached": value.breached, "record_sha256": value.record_sha256}
        with self._write_scope():
            return self._strict("monitoring.latency_samples", "latency_sample_id", row)

    def record_connection_fault(self, value):
        row = (value.connection_fault_id, value.fix_session_id, value.fault_type.value, value.scheduled_at_utc, value.activated_at_utc, value.reason_code, _json_param(dict(value.configuration)), value.record_sha256)
        cursor = self.connection.cursor()
        try:
            cursor.execute("INSERT INTO monitoring.connection_faults (connection_fault_id,fix_session_id,fault_type,scheduled_at_utc,activated_at_utc,reason_code,configuration_jsonb,record_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (connection_fault_id) DO UPDATE SET activated_at_utc=COALESCE(monitoring.connection_faults.activated_at_utc,EXCLUDED.activated_at_utc),record_sha256=CASE WHEN monitoring.connection_faults.activated_at_utc IS NULL THEN EXCLUDED.record_sha256 ELSE monitoring.connection_faults.record_sha256 END WHERE monitoring.connection_faults.fix_session_id=EXCLUDED.fix_session_id AND monitoring.connection_faults.fault_type=EXCLUDED.fault_type AND monitoring.connection_faults.scheduled_at_utc=EXCLUDED.scheduled_at_utc AND monitoring.connection_faults.reason_code=EXCLUDED.reason_code AND monitoring.connection_faults.configuration_jsonb=EXCLUDED.configuration_jsonb RETURNING connection_fault_id", row)
            stored = cursor.fetchone()
            if stored is None:
                raise Phase6ConflictError("stored connection fault conflicts with deterministic identity")
            return stored[0]
        finally:
            cursor.close()

    def latest_health_by_component(self, component): return self._fetchone("SELECT * FROM monitoring.health_snapshots WHERE component=%s ORDER BY evaluation_at_utc DESC,health_snapshot_id DESC LIMIT 1", (component,))
    def list_health_history(self, component, start_utc, end_utc): return self._fetchall("SELECT * FROM monitoring.health_snapshots WHERE component=%s AND evaluation_at_utc>=%s AND evaluation_at_utc<%s ORDER BY evaluation_at_utc,health_snapshot_id", (component, start_utc, end_utc))
    def list_open_incidents(self): return self._fetchall("SELECT * FROM monitoring.incidents WHERE state IN ('open','acknowledged') ORDER BY severity DESC,episode_started_at_utc,incident_id")
    def list_incident_history(self, start_utc, end_utc): return self._fetchall("SELECT * FROM monitoring.incidents WHERE episode_started_at_utc>=%s AND episode_started_at_utc<%s ORDER BY episode_started_at_utc,incident_id", (start_utc, end_utc))
    def get_fix_session(self, fix_session_id): return self._fetchone("SELECT * FROM monitoring.fix_sessions WHERE fix_session_id=%s", (fix_session_id,))
    def list_fix_messages(self, fix_session_id, direction, begin_seq_num, end_seq_num): return self._fetchall("SELECT * FROM monitoring.fix_messages WHERE fix_session_id=%s AND direction=%s AND msg_seq_num>=%s AND msg_seq_num<%s ORDER BY msg_seq_num,fix_message_id", (fix_session_id, getattr(direction, 'value', direction), begin_seq_num, end_seq_num))
    def list_order_lifecycle(self, fix_session_id, cl_ord_id): return self._fetchall("SELECT * FROM monitoring.fix_order_links WHERE fix_session_id=%s AND cl_ord_id=%s ORDER BY fix_order_link_id", (fix_session_id, cl_ord_id))
    def list_latency_history(self, fix_session_id, start_utc, end_utc): return self._fetchall("SELECT * FROM monitoring.latency_samples WHERE fix_session_id=%s AND simulated_start_utc>=%s AND simulated_start_utc<%s ORDER BY simulated_start_utc,latency_sample_id", (fix_session_id, start_utc, end_utc))
    def list_connection_fault_history(self, fix_session_id, start_utc, end_utc): return self._fetchall("SELECT * FROM monitoring.connection_faults WHERE fix_session_id=%s AND scheduled_at_utc>=%s AND scheduled_at_utc<%s ORDER BY scheduled_at_utc,connection_fault_id", (fix_session_id, start_utc, end_utc))


def session_record_hash(session):
    from secure_eval_wrapper.data_collection.hashing import sha256_payload
    return sha256_payload({
        "fix_session_id": session.fix_session_id, "state": session.state,
        "next_inbound": session.next_inbound_seq_num, "next_outbound": session.next_outbound_seq_num,
        "last_inbound": session.last_inbound_at_utc, "last_outbound": session.last_outbound_at_utc,
        "pending_test": session.pending_test_request_id, "pending_deadline": session.pending_test_deadline_at_utc,
        "grace_expired": session.test_request_grace_expired, "configuration": session.configuration.config_sha256,
    })
