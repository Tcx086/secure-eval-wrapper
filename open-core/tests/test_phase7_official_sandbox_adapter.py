import base64,hashlib,hmac,unittest
from decimal import Decimal
from secure_eval_wrapper.paper.credentials import InjectedCredentialProvider
from secure_eval_wrapper.paper.endpoints import validate_endpoint_request,validate_redirect
from secure_eval_wrapper.paper.enums import TransportRequestType,TransportResultType,VenueOrderState
from secure_eval_wrapper.paper.models import TransportRequest,TransportResult
from secure_eval_wrapper.paper.transport import FakeTransport,StandardLibraryOkxDemoTransport
from secure_eval_wrapper.paper.venue import UnknownSubmissionResult
from secure_eval_wrapper.paper.venues.official_sandbox import OkxDemoVenue
from phase7_test_support import T0
from test_phase7_internal_accounting_reconciliation_kill import submission

def ok(payload):return lambda req:TransportResult(req.request_id,TransportResultType.SUCCEEDED,200,T0,hashlib.sha256(b"response").hexdigest(),payload,False)
def unknown(req):return TransportResult(req.request_id,TransportResultType.UNKNOWN,None,T0,None,{},True)
class CredentialTransportTests(unittest.TestCase):
    def test_credentials_are_lazy_and_all_gates_required(self):
        provider=InjectedCredentialProvider(); self.assertEqual(provider.load_count,0)
        with self.assertRaises(PermissionError):provider.load(gates={})
        gates={k:True for k in ("cli_external_sandbox","paper_enabled","provider_selected","sandbox_environment","endpoint_validated","configuration_valid","live_false","kill_switch_inactive","limits_configured")}; material=provider.load(gates=gates); self.assertEqual(provider.load_count,1); self.assertNotIn("injected-secret",repr(material))
    def test_unapproved_endpoint_is_rejected_before_credentials_load(self):
        provider=InjectedCredentialProvider()
        gates={k:True for k in ("cli_external_sandbox","paper_enabled","provider_selected","sandbox_environment","endpoint_validated","configuration_valid","live_false","kill_switch_inactive","limits_configured")}
        transport=StandardLibraryOkxDemoTransport(provider,gates=gates,clock=lambda:T0)
        request=TransportRequest(TransportRequestType.BALANCES,"GET","https://www.okx.com/api/v5/account/balance","/api/v5/account/balance",b"",None,T0)
        with self.assertRaises(ValueError):transport.execute(request)
        self.assertEqual(provider.load_count,0)
    def test_okx_signature_contract(self):
        secret="secret"; ts="2026-01-01T00:00:00.000Z"; path="/api/v5/trade/order"; body=b'{"clOrdId":"abc"}'; expected=base64.b64encode(hmac.new(secret.encode(),(ts+"POST"+path+body.decode()).encode(),hashlib.sha256).digest()).decode(); self.assertEqual(StandardLibraryOkxDemoTransport.signature(secret,ts,"POST",path,body),expected)
    def test_redirect_always_rejected(self):
        with self.assertRaises(ValueError):validate_redirect("okx_demo","paper_exchange_sandbox","https://openapi.okx.com/api/v5/account/balance",{"x-simulated-trading":"1"})
    def test_query_environment_override_rejected(self):
        with self.assertRaises(ValueError):validate_endpoint_request("okx_demo","paper_exchange_sandbox","GET","https://openapi.okx.com/api/v5/account/balance?x-simulated-trading=0",{"x-simulated-trading":"1"})
class OkxAdapterTests(unittest.TestCase):
    def test_submit_exact_body_and_unknown_is_not_rejection(self):
        fake=FakeTransport([ok({"code":"0","data":[{"ordId":"123","clOrdId":"c1","sCode":"0","sMsg":""}]})]); venue=OkxDemoVenue(fake,clock=lambda:T0); order=venue.submit_order(submission()); self.assertEqual(order.state,VenueOrderState.PENDING_ACK); req=fake.requests[0]; self.assertEqual((req.method,req.path_with_query),("POST","/api/v5/trade/order")); self.assertIn(b'"clOrdId":"c1"',req.body); self.assertNotIn(b"secret",req.body)
        with self.assertRaises(UnknownSubmissionResult):OkxDemoVenue(FakeTransport([unknown]),clock=lambda:T0).submit_order(submission())
    def test_changed_retry_economics_conflicts(self):
        fake=FakeTransport([ok({"code":"0","data":[{"ordId":"123","sCode":"0"}]})]); venue=OkxDemoVenue(fake,clock=lambda:T0); venue.submit_order(submission())
        with self.assertRaises(Exception):venue.submit_order(submission(qty="2"))
    def test_order_balance_position_fill_parsing(self):
        responses=[ok({"code":"0","data":[{"ordId":"123","sCode":"0"}]}),ok({"code":"0","data":[{"ordId":"123","clOrdId":"c1","state":"partially_filled","accFillSz":"0.4","avgPx":"100","cTime":"1767225600000","uTime":"1767225601000"}]}),ok({"code":"0","data":[{"details":[{"ccy":"USDT","cashBal":"1000","availBal":"900"}]}]}),ok({"code":"0","data":[]}),ok({"code":"0","data":[{"tradeId":"f1","ordId":"123","clOrdId":"c1","fillSz":"0.4","fillPx":"100","fee":"-0.04","feeCcy":"USDT","fillTime":"1767225601000"}]})]
        venue=OkxDemoVenue(FakeTransport(responses),clock=lambda:T0); venue.submit_order(submission()); self.assertEqual(venue.query_order("c1").state,VenueOrderState.PARTIALLY_FILLED); self.assertEqual(venue.fetch_balances()[0].reserved,Decimal("100")); self.assertEqual(venue.fetch_positions(),()); self.assertEqual(venue.fetch_fills()[0].fee_amount,Decimal("0.04"))
    def test_account_config_and_instrument_routes(self):
        fake=FakeTransport([ok({"code":"0","data":[{"acctLv":"1"}]}),ok({"code":"0","data":[{"instId":"BTC-USDT"}]})]); venue=OkxDemoVenue(fake,clock=lambda:T0); self.assertEqual(venue.fetch_account_mode(),"1"); self.assertEqual(venue.fetch_instruments()[0]["instId"],"BTC-USDT"); self.assertEqual(fake.requests[1].path_with_query,"/api/v5/account/instruments?instType=SPOT")
    def test_error_payload_and_unsupported_order_rejected(self):
        venue=OkxDemoVenue(FakeTransport([ok({"code":"51000","msg":"bad","data":[]})]),clock=lambda:T0)
        with self.assertRaises(RuntimeError):venue.fetch_account_mode()
        with self.assertRaises(ValueError):OkxDemoVenue(FakeTransport([]),clock=lambda:T0).submit_order(submission(kind=__import__('secure_eval_wrapper.execution.models',fromlist=['OrderType']).OrderType.STOP,stop="101"))
if __name__=="__main__":unittest.main()
