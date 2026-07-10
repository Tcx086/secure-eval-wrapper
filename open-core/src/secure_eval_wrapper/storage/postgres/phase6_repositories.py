"""PostgreSQL repositories for Phase 6 monitoring and simulated FIX records."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID
from secure_eval_wrapper.storage.postgres.alpha_signal_base import _PostgresRepositoryBase,_json_param

class Phase6ConflictError(RuntimeError): pass

class PostgresPhase6Repository(_PostgresRepositoryBase):
 @contextmanager
 def _write_scope(self):
  if self.commit_on_write:
   with self.transaction(): yield
  else: yield
 def _strict(self,table,id_column,row,*,hash_column="record_sha256"):
  columns=tuple(row); params=tuple(_json_param(row[n]) if n.endswith("_jsonb") else row[n] for n in columns); cur=self.connection.cursor()
  try:
   cur.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s']*len(columns))}) ON CONFLICT DO NOTHING RETURNING {id_column}, {hash_column}",params); stored=cur.fetchone()
   if stored is None: cur.execute(f"SELECT {id_column}, {hash_column} FROM {table} WHERE {id_column}=%s",(row[id_column],)); stored=cur.fetchone()
   if stored is None or str(stored[1])!=str(row[hash_column]): raise Phase6ConflictError(f"stored {table} content conflicts with deterministic identity")
   return stored[0]
  finally:
   close=getattr(cur,"close",None)
   if close: close()
 def record_monitoring_run(self,v):
  row={"monitoring_run_id":v.monitoring_run_id,"as_of_utc":v.as_of_utc,"monitored_identity":v.reference.monitored_identity,"monitored_run_id":v.reference.monitored_run_id,"configuration_sha256":v.configuration_sha256,"stable_input_sha256":v.stable_input_sha256,"implementation_code_sha256":v.provenance.implementation_code_sha256,"repository_commit_sha":v.provenance.repository_commit_sha,"overall_status":v.overall_status.value,"parent_ids":list(v.parent_ids),"public_provenance_jsonb":dict(v.provenance.operational_metadata),"record_sha256":v.record_sha256}
  with self._write_scope(): return self._strict("monitoring.monitoring_runs","monitoring_run_id",row)
 def record_health_check_result(self,v):
  row={"health_check_result_id":v.health_check_result_id,"monitoring_run_id":v.monitoring_run_id,"evaluation_at_utc":v.evaluation_at_utc,"category":v.category.value,"component":v.component,"check_name":v.check_name,"status":v.status.value,"health_status":v.health_status.value,"severity":v.severity.value,"reason_code":v.reason_code,"explanation":v.explanation,"observed_value_jsonb":v.observed_value,"configured_threshold_jsonb":v.configured_threshold,"configuration_sha256":v.configuration_sha256,"stable_input_sha256":v.stable_input_sha256,"implementation_code_sha256":v.provenance.implementation_code_sha256,"repository_commit_sha":v.provenance.repository_commit_sha,"parent_ids":list(v.parent_ids),"record_sha256":v.record_sha256}
  with self._write_scope(): return self._strict("monitoring.health_check_results","health_check_result_id",row)
 def record_health_snapshot(self,v):
  row={"health_snapshot_id":v.health_snapshot_id,"monitoring_run_id":v.monitoring_run_id,"evaluation_at_utc":v.evaluation_at_utc,"category":None if v.category is None else v.category.value,"component":v.component,"health_status":v.health_status.value,"causing_check_ids":list(v.causing_check_ids),"reason_code":v.reason_code,"explanation":v.explanation,"configuration_sha256":v.configuration_sha256,"stable_input_sha256":v.stable_input_sha256,"implementation_code_sha256":v.provenance.implementation_code_sha256,"repository_commit_sha":v.provenance.repository_commit_sha,"parent_ids":list(v.parent_ids),"record_sha256":v.record_sha256}
  with self._write_scope(): return self._strict("monitoring.health_snapshots","health_snapshot_id",row)
 def record_monitoring_event(self,v):
  row={"monitoring_event_id":v.monitoring_event_id,"run_id":None,"event_category":v.category.value,"severity":v.severity.value,"event_time_utc":v.evaluation_at_utc,"symbol":None,"message":v.explanation,"details_jsonb":dict(v.details),"monitoring_run_id":v.monitoring_run_id,"component":v.component,"event_type":v.event_type.value,"reason_code":v.reason_code,"configuration_sha256":v.configuration_sha256,"stable_input_sha256":v.stable_input_sha256,"implementation_code_sha256":v.provenance.implementation_code_sha256,"repository_commit_sha":v.provenance.repository_commit_sha,"parent_ids":list(v.parent_ids),"record_sha256":v.record_sha256}
  with self._write_scope(): return self._strict("monitoring.monitoring_events","monitoring_event_id",row)
 def record_incident(self,v):
  row=(v.incident_id,v.category.value,v.component,v.reason_code,v.monitored_identity,v.state.value,v.severity.value,v.episode_started_at_utc,v.latest_at_utc,v.resolved_at_utc,v.occurrence_count,v.configuration_sha256,v.stable_input_sha256,v.record_sha256); cur=self.connection.cursor()
  try:
   cur.execute("INSERT INTO monitoring.incidents (incident_id,category,component,reason_code,monitored_identity,state,severity,episode_started_at_utc,latest_at_utc,resolved_at_utc,occurrence_count,configuration_sha256,stable_input_sha256,record_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (incident_id) DO UPDATE SET state=EXCLUDED.state,severity=EXCLUDED.severity,latest_at_utc=EXCLUDED.latest_at_utc,resolved_at_utc=EXCLUDED.resolved_at_utc,occurrence_count=EXCLUDED.occurrence_count,configuration_sha256=EXCLUDED.configuration_sha256,stable_input_sha256=EXCLUDED.stable_input_sha256,record_sha256=EXCLUDED.record_sha256 RETURNING incident_id",row); return cur.fetchone()[0]
  finally:
   close=getattr(cur,"close",None)
   if close: close()
 def record_incident_occurrence(self,v):
  row={"incident_occurrence_id":v.incident_occurrence_id,"incident_id":v.incident_id,"monitoring_run_id":v.monitoring_run_id,"health_check_result_id":v.health_check_result_id,"occurred_at_utc":v.occurred_at_utc,"record_sha256":v.record_sha256}
  with self._write_scope(): return self._strict("monitoring.incident_occurrences","incident_occurrence_id",row)
 def record_fix_session(self,session,updated_at_utc):
  row=(session.fix_session_id,session.configuration.session_key,session.configuration.sender_comp_id,session.configuration.target_comp_id,session.state.value,session.next_inbound_seq_num,session.next_outbound_seq_num,session.configuration.heartbeat_interval_seconds,session.last_inbound_at_utc,session.last_outbound_at_utc,session.pending_test_request_id,session.configuration.config_sha256,session_record_hash(session),updated_at_utc); cur=self.connection.cursor()
  try:
   cur.execute("INSERT INTO monitoring.fix_sessions (fix_session_id,session_key,sender_comp_id,target_comp_id,state,next_inbound_seq_num,next_outbound_seq_num,heartbeat_interval_seconds,last_inbound_at_utc,last_outbound_at_utc,pending_test_request_id,configuration_sha256,record_sha256,updated_at_utc) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (fix_session_id) DO UPDATE SET state=EXCLUDED.state,next_inbound_seq_num=EXCLUDED.next_inbound_seq_num,next_outbound_seq_num=EXCLUDED.next_outbound_seq_num,last_inbound_at_utc=EXCLUDED.last_inbound_at_utc,last_outbound_at_utc=EXCLUDED.last_outbound_at_utc,pending_test_request_id=EXCLUDED.pending_test_request_id,record_sha256=EXCLUDED.record_sha256,updated_at_utc=EXCLUDED.updated_at_utc RETURNING fix_session_id",row); return cur.fetchone()[0]
  finally:
   close=getattr(cur,"close",None)
   if close: close()
 def record_fix_session_event(self,session,event):
  row={"fix_session_event_id":event.event_id,"run_id":None,"session_id":session.configuration.session_key,"event_type":event.event_type.value,"sequence_number":event.sequence_number,"event_time_utc":event.event_at_utc,"message_type":None,"payload_jsonb":{"reason_code":event.reason_code},"simulated":True,"fix_session_id":session.fix_session_id,"prior_state":event.prior_state.value,"new_state":event.new_state.value,"reason_code":event.reason_code,"parent_message_id":event.parent_message_id,"record_sha256":event.record_sha256}
  with self._write_scope(): return self._strict("monitoring.fix_session_events","fix_session_event_id",row)
 def record_fix_message(self,fix_session_id,direction,message,processing_time_utc,raw_bytes):
  import hashlib
  from secure_eval_wrapper.fix.codec import FixCodec
  encoded=FixCodec(preserve_unknown_tags=True).encode(message) if raw_bytes is None else raw_bytes; body=message.body_length
  if body is None:
   second=encoded.find(b"\x01",encoded.find(b"\x01")+1); body=encoded.rfind(b"10=")-(second+1)
  checksum=message.checksum if message.checksum is not None else sum(encoded[:encoded.rfind(b"10=")])%256
  row={"fix_message_id":message.fix_message_id,"fix_session_id":fix_session_id,"direction":getattr(direction,"value",direction),"msg_type":message.msg_type.value,"msg_seq_num":message.msg_seq_num,"sending_time_utc":message.sending_time_utc,"processing_time_utc":processing_time_utc,"validation_status":"valid","rejection_reason":None,"body_length":body,"checksum":checksum,"business_identity_sha256":message.business_identity_sha256,"raw_message_sha256":hashlib.sha256(encoded).hexdigest(),"parsed_fields_jsonb":{str(k):v for k,v in {**message.fields,**message.extensions}.items()}}
  from secure_eval_wrapper.data_collection.hashing import sha256_payload
  row["record_sha256"]=sha256_payload(row)
  with self._write_scope(): return self._strict("monitoring.fix_messages","fix_message_id",row)
 def record_fix_order_link(self,v):
  row={"fix_order_link_id":v.fix_order_link_id,"fix_session_id":v.fix_session_id,"cl_ord_id":v.cl_ord_id,"orig_cl_ord_id":v.orig_cl_ord_id,"order_intent_id":v.order_intent_id,"order_id":v.order_id,"fill_id":v.fill_id,"execution_report_message_id":v.execution_report_message_id,"business_identity_sha256":v.business_identity_sha256,"record_sha256":v.record_sha256}
  with self._write_scope(): return self._strict("monitoring.fix_order_links","fix_order_link_id",row)
 def record_latency_sample(self,v,monitoring_run_id=None):
  row={"latency_sample_id":v.latency_sample_id,"monitoring_run_id":monitoring_run_id,"fix_session_id":v.fix_session_id,"fix_message_id":v.fix_message_id,"stage":v.stage.value,"simulated_start_utc":v.simulated_start_utc,"simulated_end_utc":v.simulated_end_utc,"duration_microseconds":v.duration_microseconds,"threshold_microseconds":v.threshold_microseconds,"breached":v.breached,"record_sha256":v.record_sha256}
  with self._write_scope(): return self._strict("monitoring.latency_samples","latency_sample_id",row)
 def record_connection_fault(self,v):
  row={"connection_fault_id":v.connection_fault_id,"fix_session_id":v.fix_session_id,"fault_type":v.fault_type.value,"scheduled_at_utc":v.scheduled_at_utc,"activated_at_utc":v.activated_at_utc,"reason_code":v.reason_code,"configuration_jsonb":dict(v.configuration),"record_sha256":v.record_sha256}
  with self._write_scope(): return self._strict("monitoring.connection_faults","connection_fault_id",row)
 def latest_health_by_component(self,component): return self._fetchone("SELECT * FROM monitoring.health_snapshots WHERE component=%s ORDER BY evaluation_at_utc DESC,health_snapshot_id DESC LIMIT 1",(component,))
 def list_health_history(self,component,start_utc,end_utc): return self._fetchall("SELECT * FROM monitoring.health_snapshots WHERE component=%s AND evaluation_at_utc>=%s AND evaluation_at_utc<%s ORDER BY evaluation_at_utc,health_snapshot_id",(component,start_utc,end_utc))
 def list_open_incidents(self): return self._fetchall("SELECT * FROM monitoring.incidents WHERE state IN ('open','acknowledged') ORDER BY severity DESC,episode_started_at_utc,incident_id")
 def list_incident_history(self,start_utc,end_utc): return self._fetchall("SELECT * FROM monitoring.incidents WHERE episode_started_at_utc>=%s AND episode_started_at_utc<%s ORDER BY episode_started_at_utc,incident_id",(start_utc,end_utc))
 def get_fix_session(self,fix_session_id): return self._fetchone("SELECT * FROM monitoring.fix_sessions WHERE fix_session_id=%s",(fix_session_id,))
 def list_fix_messages(self,fix_session_id,direction,begin_seq_num,end_seq_num): return self._fetchall("SELECT * FROM monitoring.fix_messages WHERE fix_session_id=%s AND direction=%s AND msg_seq_num>=%s AND msg_seq_num<%s ORDER BY msg_seq_num,fix_message_id",(fix_session_id,getattr(direction,'value',direction),begin_seq_num,end_seq_num))
 def list_order_lifecycle(self,fix_session_id,cl_ord_id): return self._fetchall("SELECT * FROM monitoring.fix_order_links WHERE fix_session_id=%s AND cl_ord_id=%s ORDER BY fix_order_link_id",(fix_session_id,cl_ord_id))
 def list_latency_history(self,fix_session_id,start_utc,end_utc): return self._fetchall("SELECT * FROM monitoring.latency_samples WHERE fix_session_id=%s AND simulated_start_utc>=%s AND simulated_start_utc<%s ORDER BY simulated_start_utc,latency_sample_id",(fix_session_id,start_utc,end_utc))
 def list_connection_fault_history(self,fix_session_id,start_utc,end_utc): return self._fetchall("SELECT * FROM monitoring.connection_faults WHERE fix_session_id=%s AND scheduled_at_utc>=%s AND scheduled_at_utc<%s ORDER BY scheduled_at_utc,connection_fault_id",(fix_session_id,start_utc,end_utc))

def session_record_hash(session):
 from secure_eval_wrapper.data_collection.hashing import sha256_payload
 return sha256_payload({"fix_session_id":session.fix_session_id,"state":session.state,"next_inbound":session.next_inbound_seq_num,"next_outbound":session.next_outbound_seq_num,"last_inbound":session.last_inbound_at_utc,"last_outbound":session.last_outbound_at_utc,"pending_test":session.pending_test_request_id,"configuration":session.configuration.config_sha256})