"""Deterministic local-versus-venue paper reconciliation."""
from .enums import AccountSnapshotStatus,PaperOrderState,ReconciliationDifferenceType as D,ReconciliationStatus,VenueOrderState
from .models import PaperReconciliation,PaperReconciliationDifference

_TERMINAL={VenueOrderState.FILLED,VenueOrderState.CANCELLED,VenueOrderState.REJECTED,VenueOrderState.EXPIRED}
class PaperReconciliationEngine:
    def reconcile(self,*,paper_run_id,local_snapshot,venue_snapshot,local_orders,venue_orders,local_fills,venue_fills,at_utc):
        provisional=PaperReconciliation(paper_run_id,local_snapshot.snapshot_id,venue_snapshot.snapshot_id,at_utc,ReconciliationStatus.RECONCILED,local_snapshot.venue_sequence,venue_snapshot.venue_sequence,0,0); raw=[]
        def diff(kind,identity,local,venue,explanation,material=True):raw.append((kind,material,identity,local,venue,explanation))
        lorders={o.client_order_id:o for o in local_orders}; vorders={o.client_order_id:o for o in venue_orders}
        for client,order in lorders.items():
            other=vorders.get(client)
            if other is None:diff(D.LOCAL_ORDER_MISSING_AT_VENUE,client,getattr(order,"state",None),None,"local paper order has no venue evidence")
            else:
                local_state=getattr(order,"state",None); mapped=getattr(local_state,"value",local_state)
                if mapped!=other.state.value and not (mapped=="submission_unknown" and other.state is VenueOrderState.UNKNOWN_PENDING_RECOVERY):diff(D.ORDER_STATUS_MISMATCH,client,mapped,other.state.value,"local and venue order status differ")
                if order.quantity!=other.quantity or getattr(order,"cumulative_filled_quantity",other.cumulative_filled_quantity)!=other.cumulative_filled_quantity:diff(D.QUANTITY_MISMATCH,client,str(order.quantity),str(other.quantity),"order quantity or cumulative fill differs")
        for client,order in vorders.items():
            if client not in lorders:diff(D.VENUE_ORDER_MISSING_LOCALLY,client,None,order.state.value,"venue order has no local audit record")
        lf={f.venue_fill_id:f for f in local_fills}; vf={f.venue_fill_id:f for f in venue_fills}
        for fid in vf:
            if fid not in lf:diff(D.FILL_MISSING_LOCALLY,fid,None,vf[fid].record_sha256,"venue fill missing from local accounting")
        for fid in lf:
            if fid not in vf:diff(D.FILL_MISSING_AT_VENUE,fid,lf[fid].record_sha256,None,"local fill missing from venue evidence")
            elif lf[fid].fee_amount!=vf[fid].fee_amount:diff(D.FEE_MISMATCH,fid,str(lf[fid].fee_amount),str(vf[fid].fee_amount),"fill fee differs")
            elif lf[fid].fee_currency!=vf[fid].fee_currency:diff(D.CURRENCY_MISMATCH,fid,lf[fid].fee_currency,vf[fid].fee_currency,"fill fee currency differs")
        lb={b.currency:b for b in local_snapshot.balances}; vb={b.currency:b for b in venue_snapshot.balances}
        for c in sorted(set(lb)|set(vb)):
            if c not in lb or c not in vb or lb[c].total!=vb[c].total:diff(D.BALANCE_MISMATCH,c,None if c not in lb else str(lb[c].total),None if c not in vb else str(vb[c].total),"local and venue balances differ")
        lp={p.series_identity.series_identity_sha256:p for p in local_snapshot.positions}; vp={p.series_identity.series_identity_sha256:p for p in venue_snapshot.positions}
        for key in sorted(set(lp)|set(vp)):
            if key not in lp or key not in vp or lp[key].quantity!=vp[key].quantity or lp[key].average_entry_price!=vp[key].average_entry_price:diff(D.POSITION_MISMATCH,key,None if key not in lp else str(lp[key].quantity),None if key not in vp else str(vp[key].quantity),"local and venue positions differ")
        if venue_snapshot.status is AccountSnapshotStatus.STALE:diff(D.STALE_VENUE_SNAPSHOT,"snapshot",None,venue_snapshot.venue_as_of_utc,"venue snapshot is stale")
        if local_snapshot.status is AccountSnapshotStatus.STALE:diff(D.STALE_LOCAL_SNAPSHOT,"snapshot",local_snapshot.venue_as_of_utc,None,"local snapshot is stale")
        if local_snapshot.account_mode!=venue_snapshot.account_mode:diff(D.ACCOUNT_MODE_MISMATCH,"account_mode",local_snapshot.account_mode,venue_snapshot.account_mode,"account modes differ")
        if local_snapshot.venue_sequence is not None and venue_snapshot.venue_sequence is not None and venue_snapshot.venue_sequence<local_snapshot.venue_sequence:diff(D.SEQUENCE_GAP,"venue_sequence",local_snapshot.venue_sequence,venue_snapshot.venue_sequence,"venue sequence moved backward")
        if any(getattr(o,"state",None) in (PaperOrderState.SUBMISSION_UNKNOWN,PaperOrderState.PENDING_RECOVERY) for o in local_orders):diff(D.UNKNOWN_SUBMISSION,"submission",None,None,"submission remains unknown")
        material=sum(1 for _,m,*_ in raw if m); status=ReconciliationStatus.UNKNOWN if any(x[0] is D.UNKNOWN_SUBMISSION for x in raw) else ReconciliationStatus.BLOCKED if material else ReconciliationStatus.WARNING if raw else ReconciliationStatus.RECONCILED
        result=PaperReconciliation(paper_run_id,local_snapshot.snapshot_id,venue_snapshot.snapshot_id,at_utc,status,local_snapshot.venue_sequence,venue_snapshot.venue_sequence,len(raw),material,reconciliation_id=provisional.reconciliation_id)
        differences=tuple(PaperReconciliationDifference(result.reconciliation_id,*row) for row in raw); return result,differences
