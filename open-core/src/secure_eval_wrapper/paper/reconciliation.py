"""Deterministic local-versus-venue paper reconciliation."""
from .enums import AccountSnapshotStatus,PaperOrderState,ReconciliationDifferenceType as D,ReconciliationStatus,VenueOrderState
from .models import PaperReconciliation,PaperReconciliationDifference

_TERMINAL={VenueOrderState.FILLED,VenueOrderState.CANCELLED,VenueOrderState.REJECTED,VenueOrderState.EXPIRED}
class PaperReconciliationEngine:
    def reconcile(self,*,paper_run_id,local_snapshot,venue_snapshot,local_orders,venue_orders,local_fills,venue_fills,at_utc,maximum_snapshot_age_seconds=None,authority_checks=()):
        provisional=PaperReconciliation(paper_run_id,local_snapshot.snapshot_id,venue_snapshot.snapshot_id,at_utc,ReconciliationStatus.RECONCILED,local_snapshot.venue_sequence,venue_snapshot.venue_sequence,0,0); raw=[]
        def diff(kind,identity,local,venue,explanation,material=True):raw.append((kind,material,identity,local,venue,explanation))
        lorders={o.client_order_id:o for o in local_orders}; vorders={o.client_order_id:o for o in venue_orders}
        for client,order in lorders.items():
            other=vorders.get(client)
            if other is None:diff(D.LOCAL_ORDER_MISSING_AT_VENUE,client,getattr(order,"state",None),None,"local paper order has no venue evidence")
            else:
                local_state=getattr(order,"state",None); mapped=getattr(local_state,"value",local_state)
                if mapped!=other.state.value and not (mapped=="submission_unknown" and other.state is VenueOrderState.UNKNOWN_PENDING_RECOVERY):diff(D.ORDER_STATUS_MISMATCH,client,mapped,other.state.value,"local and venue order status differ")
                if getattr(order,"venue_order_id",None)!=other.venue_order_id:diff(D.VENUE_ORDER_ID_MISMATCH,client,getattr(order,"venue_order_id",None),other.venue_order_id,"venue order identity differs")
                if (order.quantity,getattr(order,"cumulative_filled_quantity",None),getattr(order,"remaining_quantity",None))!=(other.quantity,other.cumulative_filled_quantity,other.remaining_quantity):diff(D.QUANTITY_MISMATCH,client,{"original":str(order.quantity),"cumulative":str(getattr(order,"cumulative_filled_quantity",0)),"remaining":str(getattr(order,"remaining_quantity",order.quantity))},{"original":str(other.quantity),"cumulative":str(other.cumulative_filled_quantity),"remaining":str(other.remaining_quantity)},"original, cumulative, or remaining quantity differs")
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
            if c not in lb or c not in vb:diff(D.BALANCE_MISMATCH,c,None if c not in lb else str(lb[c].total),None if c not in vb else str(vb[c].total),"balance currency presence differs")
            else:
                if lb[c].total!=vb[c].total:diff(D.BALANCE_MISMATCH,c,str(lb[c].total),str(vb[c].total),"balance total differs")
                if lb[c].available!=vb[c].available:diff(D.BALANCE_AVAILABILITY_MISMATCH,c,str(lb[c].available),str(vb[c].available),"balance available differs")
                if lb[c].reserved!=vb[c].reserved:diff(D.RESERVATION_MISMATCH,c,str(lb[c].reserved),str(vb[c].reserved),"balance reservation differs")
        lp={p.series_identity.series_identity_sha256:p for p in local_snapshot.positions}; vp={p.series_identity.series_identity_sha256:p for p in venue_snapshot.positions}
        for key in sorted(set(lp)|set(vp)):
            if key not in lp or key not in vp:diff(D.POSITION_MISMATCH,key,None if key not in lp else str(lp[key].quantity),None if key not in vp else str(vp[key].quantity),"position presence differs")
            else:
                if lp[key].quantity!=vp[key].quantity or lp[key].average_entry_price!=vp[key].average_entry_price:diff(D.POSITION_MISMATCH,key,{"quantity":str(lp[key].quantity),"average":str(lp[key].average_entry_price)},{"quantity":str(vp[key].quantity),"average":str(vp[key].average_entry_price)},"position quantity or average entry differs")
                if lp[key].realized_pnl!=vp[key].realized_pnl:diff(D.REALIZED_PNL_MISMATCH,key,str(lp[key].realized_pnl),str(vp[key].realized_pnl),"realized PnL differs")
                if lp[key].funding!=vp[key].funding:diff(D.POSITION_MISMATCH,key,str(lp[key].funding),str(vp[key].funding),"funding differs")
        if venue_snapshot.status is AccountSnapshotStatus.STALE:diff(D.STALE_VENUE_SNAPSHOT,"snapshot",None,venue_snapshot.venue_as_of_utc,"venue snapshot is stale")
        if local_snapshot.status is AccountSnapshotStatus.STALE:diff(D.STALE_LOCAL_SNAPSHOT,"snapshot",local_snapshot.venue_as_of_utc,None,"local snapshot is stale")
        if maximum_snapshot_age_seconds is not None and (at_utc-venue_snapshot.venue_as_of_utc).total_seconds()>maximum_snapshot_age_seconds:diff(D.STALE_VENUE_SNAPSHOT,"snapshot_age",None,str((at_utc-venue_snapshot.venue_as_of_utc).total_seconds()),"venue snapshot age exceeds configured maximum")
        if maximum_snapshot_age_seconds is not None and (at_utc-local_snapshot.venue_as_of_utc).total_seconds()>maximum_snapshot_age_seconds:diff(D.STALE_LOCAL_SNAPSHOT,"snapshot_age",str((at_utc-local_snapshot.venue_as_of_utc).total_seconds()),None,"local snapshot age exceeds configured maximum")
        if local_snapshot.account_mode!=venue_snapshot.account_mode:diff(D.ACCOUNT_MODE_MISMATCH,"account_mode",local_snapshot.account_mode,venue_snapshot.account_mode,"account modes differ")
        if local_snapshot.venue_sequence is not None and venue_snapshot.venue_sequence is not None and venue_snapshot.venue_sequence<local_snapshot.venue_sequence:diff(D.SEQUENCE_GAP,"venue_sequence",local_snapshot.venue_sequence,venue_snapshot.venue_sequence,"venue sequence moved backward")
        if any(getattr(o,"state",None) in (PaperOrderState.SUBMISSION_UNKNOWN,PaperOrderState.PENDING_RECOVERY) for o in local_orders):diff(D.UNKNOWN_SUBMISSION,"submission",None,None,"submission remains unknown")
        if any(getattr(o,"state",None) in (PaperOrderState.CANCEL_REQUESTED,PaperOrderState.CANCEL_PENDING,PaperOrderState.CANCEL_UNKNOWN,VenueOrderState.CANCEL_PENDING) for o in local_orders):diff(D.CANCEL_PENDING,"cancel",None,None,"cancellation remains unconfirmed")
        for item in authority_checks:diff(D(item["type"]),str(item["identity"]),item.get("local"),item.get("venue"),str(item["explanation"]))
        material=sum(1 for _,m,*_ in raw if m); status=ReconciliationStatus.UNKNOWN if any(x[0] in (D.UNKNOWN_SUBMISSION,D.CANCEL_PENDING) for x in raw) else ReconciliationStatus.BLOCKED if material else ReconciliationStatus.WARNING if raw else ReconciliationStatus.RECONCILED
        result=PaperReconciliation(paper_run_id,local_snapshot.snapshot_id,venue_snapshot.snapshot_id,at_utc,status,local_snapshot.venue_sequence,venue_snapshot.venue_sequence,len(raw),material,reconciliation_id=provisional.reconciliation_id)
        differences=tuple(PaperReconciliationDifference(result.reconciliation_id,*row) for row in raw); return result,differences
