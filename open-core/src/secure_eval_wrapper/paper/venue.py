"""Provider-neutral paper venue contract."""
from abc import ABC,abstractmethod
class PaperVenue(ABC):
    @abstractmethod
    def submit_order(self,submission):raise NotImplementedError
    @abstractmethod
    def cancel_order(self,client_order_id,at_utc):raise NotImplementedError
    @abstractmethod
    def query_order(self,client_order_id):raise NotImplementedError
    @abstractmethod
    def list_open_orders(self):raise NotImplementedError
    def list_recent_orders(self):return self.list_open_orders()
    @abstractmethod
    def fetch_balances(self):raise NotImplementedError
    @abstractmethod
    def fetch_positions(self):raise NotImplementedError
    @abstractmethod
    def fetch_fills(self):raise NotImplementedError
    @abstractmethod
    def fetch_account_snapshot(self,paper_run_id,at_utc):raise NotImplementedError
class VenueTimeout(RuntimeError):pass
class UnknownSubmissionResult(RuntimeError):pass
class EconomicConflictError(RuntimeError):pass
class ExplicitVenueRejection(RuntimeError):pass
