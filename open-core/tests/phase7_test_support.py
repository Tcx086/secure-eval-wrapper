from datetime import datetime,timezone,timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL,uuid5
from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.models import AccountingMode,OrderIntent,OrderSide,OrderType,RiskDecision,RiskDecisionStatus,RiskStage,TimeInForce
from secure_eval_wrapper.paper.configuration import internal_demo_configuration
from secure_eval_wrapper.paper.enums import CredentialSourceType,PaperProvider
from secure_eval_wrapper.paper.models import CredentialReference,deterministic_paper_uuid
T0=datetime(2026,1,1,tzinfo=timezone.utc); H=sha256_payload("phase7-test"); ID=SeriesIdentity("internal","internal-paper","BTC-USDT","BTC-USDT",InstrumentType.SPOT,"paper","USDT")
def config(persist=False):return internal_demo_configuration(persistence_required=persist)
def run_id(c=None):return deterministic_paper_uuid("test-run",{"config":(c or config()).config_sha256})
def credential():return CredentialReference(PaperProvider.INTERNAL,"internal-none",CredentialSourceType.INJECTED_TEST,sha256_payload("internal-none")[:16])
def intent(*,run=None,side=OrderSide.BUY,quantity="1",current="0",target="1",kind=OrderType.MARKET,limit=None,stop=None,signal="x",at=T0):
    run=run or run_id(); q=Decimal(quantity); delta=q*side.sign
    return OrderIntent(run,uuid5(NAMESPACE_URL,signal),ID,at,side,kind,q,Decimal(target),Decimal(current),delta,Decimal("100"),AccountingMode.SPOT,TimeInForce.GTC,H,H,H,"test",None if limit is None else Decimal(limit),None if stop is None else Decimal(stop))
def risk(i,at=T0):return RiskDecision(i.run_id,i.order_intent_id,i.series_identity,at,RiskStage.PRE_SUBMIT,RiskDecisionStatus.ACCEPTED,"accepted","accepted paper test risk",H)
