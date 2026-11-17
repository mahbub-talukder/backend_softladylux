"""A fake Steadfast for every courier test (SRS v1.7 test plan: all Steadfast HTTP is mocked)."""

import re

import pytest

from apps.core.models import SiteSettings
from apps.shop import orders
from apps.shop.checkout import place_order
from apps.shop.tests.helpers import bkash, configure_site, one_variant, payload

TOKEN = "whsec-test.token_1="


class FakeSteadfast:
    """Answers by path. `routes[(METHOD, regex)] = (status, body)` or a callable(body) -> tuple.
    Every call is recorded as (method, path, json_body, headers)."""

    def __init__(self):
        self.calls = []
        self.next_cid = 1424107
        self.routes = {
            ("GET", r"^/ping$"): (200, {"status": 200, "message": "pong"}),
            ("GET", r"^/get_balance$"): (200, {"status": 200, "current_balance": 12450}),
            ("POST", r"^/create_order$"): self._create,
            ("POST", r"^/create_order/bulk-order/extended$"): self._bulk,
            ("GET", r"^/parcel_states$"): (200, {"status": 200, "states": {
                "in_review": {"count": 1, "cod_amount": 1060},
                "delivery_processing": {"count": 2, "cod_amount": 2000},
                "cod_processing": {"count": 0, "cod_amount": 0},
                "return_requests": {"count": 0}}}),
            ("GET", r"^/parcel_states/[a-z_]+$"): (200, {"status": 200, "data": [],
                                                       "meta": {"current_page": 1,
                                                                "last_page": 1}}),
            ("GET", r"^/stats$"): (200, {"status": 200, "total": {"count": 3, "cod_amount": 3000},
                                         "success_rate": 67, "cancel_rate": 33}),
            ("GET", r"^/status_by_cid/\d+$"): (200, {"status": 200, "delivery_status": "pending"}),
            ("GET", r"^/status_with_return_status_by_cid/\d+$"): (
                200, {"status": 200, "delivery_status": "cancelled_return_processing"}),
            ("GET", r"^/trackings_by_invoice/.+$"): (200, {"status": 200, "tracking": []}),
            ("GET", r"^/police_stations$"): (200, {"police_stations": [
                {"id": 17, "name": "Dhanmondi", "district": {"name": "Dhaka"}}]}),
            ("POST", r"^/create_pickup_request$"): (201, {"data": {"id": 9081, "req_status": 0}}),
            ("POST", r"^/create_return_request$"): (201, {"id": 1, "status": "pending"}),
            ("GET", r"^/get_return_requests$"): (200, {"data": [{"id": 1, "status": "pending"}]}),
            ("GET", r"^/get_return_request/\d+$"): (200, {"id": 1, "status": "pending"}),
            ("GET", r"^/fraud_check/score/\d+$"): (200, {"status": 200, "delivery_ratio": 92,
                                                       "cancellation_ratio": 7,
                                                       "volume_band": "high"}),
        }

    def _consignment(self, body):
        self.next_cid += 1
        return {"consignment_id": self.next_cid, "invoice": body["invoice"],
                "tracking_code": f"TC{self.next_cid}",
                "tracking_link": f"https://steadfast.com.bd/tl/{self.next_cid}",
                "status": "in_review"}

    def _create(self, body):
        return 200, {"status": 200, "message": "Consignment has been created successfully.",
                     "consignment": self._consignment(body)}

    def _bulk(self, body):
        return 200, {"status": 200, "data": [
            {**self._consignment(o), "status": "success"} for o in body["data"]]}

    def request(self, method, url, headers, body=None, timeout=None):
        path = re.sub(r"^https?://[^/]+/api/v1", "", url).split("?")[0]
        self.calls.append((method, path, body, headers))
        for (m, rx), answer in self.routes.items():
            if m == method and re.match(rx, path):
                return answer(body) if callable(answer) else answer
        raise AssertionError(f"Unexpected Steadfast call {method} {path}")

    def paths(self, method=None):
        return [p for m, p, _b, _h in self.calls if method in (None, m)]


@pytest.fixture
def steadfast(monkeypatch, db):
    fake = FakeSteadfast()
    monkeypatch.setattr("apps.courier.steadfast.request", fake.request)
    configure_site(steadfast_api_key="key-123", steadfast_secret_key="secret-456",
                   steadfast_webhook_token=TOKEN, steadfast_auto_book=True)
    return fake


@pytest.fixture
def confirmed_order(steadfast, owner, django_capture_on_commit_callbacks):
    """A COD order confirmed by staff; with auto-booking on, Steadfast books it on commit
    (CRR-03). Django-Q runs synchronously in tests."""

    seq = {"n": 0}

    def make(method="cod", qty=2, **kw):
        seq["n"] += 1
        v = one_variant(name=f"Plunge Bra {seq['n']}", on_hand=10)
        body = payload(v, qty, **kw) if method == "cod" else bkash(v, qty, **kw)
        order, _ = place_order(body)
        if method != "cod":
            orders.verify_payment(order.id, owner)
        with django_capture_on_commit_callbacks(execute=True):
            orders.transition(order.id, "confirmed", owner)
        order.refresh_from_db()
        return order

    return make


def site():
    return SiteSettings.load()
