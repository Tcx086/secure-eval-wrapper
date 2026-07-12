"""Paper-only fill-confirmed accounting and active-order reservations."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from .enums import AccountSnapshotStatus
from .models import PaperAccountSnapshot,VenueBalance,VenueFill,VenuePosition
from secure_eval_wrapper.execution.models import AccountingMode,OrderSide
from .reservations import calculate_reservation,reduce_reservation

@dataclass
class Reservation:
    currency:str; amount:Decimal; original_quantity:Decimal; remaining_quantity:Decimal

class PaperAccounting:
    def __init__(self,*,paper_run_id,account_reference,balances,fee_bps=Decimal("10")):
        self.paper_run_id=paper_run_id; self.account_reference=account_reference; self.fee_bps=Decimal(fee_bps); self.balances={k.upper():Decimal(v) for k,v in balances.items()}; self.positions={}; self.reservations={}; self.applied_fill_ids=set(); self.total_fees=Decimal(0)
    def _assets(self,identity):
        parts=identity.canonical_symbol.replace("/","-").split("-")
        if len(parts)<2:raise ValueError("Spot symbol must provide base and quote assets")
        return parts[0].upper(),parts[1].upper()
    def reserved(self,currency):return sum((r.amount for r in self.reservations.values() if r.currency==currency.upper()),Decimal(0))
    def available(self,currency):return self.balances.get(currency.upper(),Decimal(0))-self.reserved(currency)
    def reserve(self,submission):
        if submission.client_order_id in self.reservations:return self.reservations[submission.client_order_id]
        required=calculate_reservation(submission,maximum_fee_bps=self.fee_bps)
        if self.available(required.currency)<required.amount:raise ValueError("paper reservation exceeds available balance or inventory")
        row=Reservation(required.currency,required.amount,submission.quantity,submission.quantity); self.reservations[submission.client_order_id]=row; return row
    def release(self,client_order_id):return self.reservations.pop(client_order_id,None)
    def apply_fill(self,fill:VenueFill):
        if fill.fill_id in self.applied_fill_ids:return False
        base,quote=self._assets(fill.series_identity); notional=fill.quantity*fill.price; old=self.positions.get(fill.series_identity.series_identity_sha256,VenuePosition(fill.series_identity,fill.accounting_mode,Decimal(0),None))
        if fill.accounting_mode is AccountingMode.SPOT:
            if fill.fee_currency!=quote:raise ValueError("paper Spot accounting supports quote-currency fees only")
            if fill.side is OrderSide.BUY:
                cost=notional+fill.fee_amount
                if self.balances.get(quote,Decimal(0))<cost:raise ValueError("confirmed fill would create negative cash")
                self.balances[quote]=self.balances.get(quote,Decimal(0))-cost; self.balances[base]=self.balances.get(base,Decimal(0))+fill.quantity
                qty=old.quantity+fill.quantity; avg=(old.quantity*(old.average_entry_price or Decimal(0))+fill.quantity*fill.price)/qty; realized=old.realized_pnl
            else:
                if self.balances.get(base,Decimal(0))<fill.quantity or old.quantity<fill.quantity:raise ValueError("confirmed fill would create negative inventory")
                self.balances[base]-=fill.quantity; self.balances[quote]=self.balances.get(quote,Decimal(0))+notional-fill.fee_amount; qty=old.quantity-fill.quantity; avg=None if qty==0 else old.average_entry_price; realized=old.realized_pnl+fill.quantity*(fill.price-(old.average_entry_price or fill.price))
            self.positions[fill.series_identity.series_identity_sha256]=VenuePosition(fill.series_identity,AccountingMode.SPOT,qty,avg,realized)
        else:
            signed=fill.quantity*fill.side.sign; qty=old.quantity+signed; avg=fill.price if old.quantity==0 or old.quantity*qty<0 else old.average_entry_price; self.positions[fill.series_identity.series_identity_sha256]=VenuePosition(fill.series_identity,AccountingMode.LINEAR_PERPETUAL,qty,None if qty==0 else avg,old.realized_pnl)
            if fill.fee_currency not in self.balances or self.balances[fill.fee_currency]<fill.fee_amount:raise ValueError("confirmed perpetual fill fee would create negative cash")
            self.balances[fill.fee_currency]-=fill.fee_amount
        reservation=self.reservations.get(fill.client_order_id)
        if reservation:
            reduced=reduce_reservation(current_amount=reservation.amount,current_quantity=reservation.remaining_quantity,fill_quantity=fill.quantity,fill_price=fill.price,fill_fee=fill.fee_amount,fee_currency=fill.fee_currency,reservation_currency=reservation.currency,side=fill.side,accounting_mode=fill.accounting_mode)
            if reduced.quantity==0:self.release(fill.client_order_id)
            else:self.reservations[fill.client_order_id]=Reservation(reservation.currency,reduced.amount,reservation.original_quantity,reduced.quantity)
        self.total_fees+=fill.fee_amount; self.applied_fill_ids.add(fill.fill_id); return True
    def snapshot(self,*,at_utc,venue_sequence,source="local_paper_accounting"):
        balances=tuple(VenueBalance(k,v,self.available(k),self.reserved(k)) for k,v in sorted(self.balances.items()))
        return PaperAccountSnapshot(self.paper_run_id,self.account_reference,AccountSnapshotStatus.FRESH,at_utc,at_utc,"spot",balances,tuple(sorted(self.positions.values(),key=lambda p:p.series_identity.series_identity_sha256)),tuple(sorted(self.reservations)),venue_sequence,source)
