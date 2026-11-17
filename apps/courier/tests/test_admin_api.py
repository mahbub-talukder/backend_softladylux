# Covers SRS v1.7 CRR-01, CRR-02, CRR-06, CRR-12..CRR-15, PRM-03/05/08; scenarios 34, 40.
import pytest

from apps.core.models import AuditLog, SiteSettings
from apps.courier import steadfast as sf
from apps.courier.models import PickupRequest

pytestmark = pytest.mark.django_db
A = "/api/v1/admin/courier"


# ------------------------------------------------------------------ settings (CRR-01, CRR-06)


def test_courier_keys_shown_not_audited_and_saving_lifts_the_stop(as_user, owner, steadfast):
    c = as_user(owner)
    SiteSettings.objects.update(steadfast_keys_rejected=True)
    r = c.patch("/api/v1/admin/settings/", {"steadfast_api_key": "new-key"}, format="json")
    assert r.status_code == 200
    for k in ("steadfast_api_key", "steadfast_secret_key", "steadfast_webhook_token"):
        assert r.data[k] and r.data[f"{k}_set"] is True  # CRR-01 (v1.8): shown to settings.manage
    assert r.data["steadfast_api_key"] == "new-key"
    assert r.data["steadfast_keys_rejected"] is False
    assert "new-key" not in str(AuditLog.objects.values_list("before", "after"))
    r = c.patch("/api/v1/admin/settings/", {"steadfast_webhook_token": "bad token!"},
                format="json")
    assert r.status_code == 400


def test_ping_reports_reachability_and_keys(as_user, owner, steadfast):  # CRR-02
    r = as_user(owner).post(f"{A}/ping/")
    assert r.data["ok"] is True and r.data["reachable"]["ok"] and r.data["keys"]["ok"]
    ping_headers = steadfast.calls[0][3]
    assert "Api-Key" not in ping_headers  # /ping needs no keys
    steadfast.routes[("GET", r"^/ping$")] = lambda b: (_ for _ in ()).throw(
        sf.SteadfastError("Steadfast could not be reached."))
    r = as_user(owner).post(f"{A}/ping/")
    assert r.data["ok"] is False and r.data["keys"]["code"] == "skipped"


def test_scenario_34_401_stops_every_call_until_settings_saved(as_user, owner, steadfast):
    steadfast.routes[("GET", r"^/get_balance$")] = (401, {"message": "Unauthenticated."})
    r = as_user(owner).get(f"{A}/dashboard/")
    assert r.data["keys_rejected"] is True and r.data["balance"]["code"] == "keys_rejected"
    sent = len(steadfast.calls)
    r = as_user(owner).get(f"{A}/dashboard/?from=2026-10-01&to=2026-10-31")
    assert len(steadfast.calls) == sent  # CRR-06: no network at all
    assert r.data["states"]["message"] == sf.KEYS_REJECTED
    as_user(owner).patch("/api/v1/admin/settings/", {"steadfast_secret_key": "fixed"},
                         format="json")
    steadfast.routes[("GET", r"^/get_balance$")] = (200, {"current_balance": 5})
    r = as_user(owner).get(f"{A}/dashboard/?from=2026-09-01&to=2026-09-30")
    assert r.data["keys_rejected"] is False and r.data["balance"]["data"]["current_balance"] == 5


def test_client_error_shapes(steadfast):
    steadfast.routes[("GET", r"^/get_balance$")] = (500, None)
    with pytest.raises(sf.SteadfastError) as e:
        sf.balance()
    assert e.value.retryable and e.value.status == 500
    steadfast.routes[("GET", r"^/get_balance$")] = (429, {"message": "Too many"})
    with pytest.raises(sf.SteadfastError) as e:
        sf.balance()
    assert e.value.code == "throttled" and not e.value.retryable
    SiteSettings.objects.update(steadfast_api_key="")
    with pytest.raises(sf.SteadfastError) as e:
        sf.balance()
    assert e.value.code == "not_configured"


# ------------------------------------------------------------------ dashboard (CRR-12)


def test_dashboard_and_cache(as_user, owner, steadfast):
    c = as_user(owner)
    r = c.get(f"{A}/dashboard/")
    assert r.status_code == 200 and r.data["balance"]["data"]["current_balance"] == 12450
    assert r.data["states"]["data"]["states"]["delivery_processing"]["count"] == 2
    assert r.data["stats"]["ok"] and r.data["from"] < r.data["to"]
    sent = len(steadfast.calls)
    c.get(f"{A}/dashboard/")
    assert len(steadfast.calls) == sent  # cached for 60 seconds
    r = c.get(f"{A}/parcel-states/delivery_processing/?page=2")
    assert r.status_code == 200 and steadfast.calls[-1][1] == "/parcel_states/delivery_processing"
    assert c.get(f"{A}/parcel-states/nonsense/").status_code == 404


# ------------------------------------------------------------------ order actions


def test_refresh_and_return_request(confirmed_order, as_user, owner, steadfast):  # CRR-11/14
    order = confirmed_order()
    c = as_user(owner)
    r = c.post(f"{A}/orders/{order.id}/refresh/")
    assert r.status_code == 200 and r.data["shipment"]["courier_status"] == "pending"
    assert c.post(f"{A}/orders/{order.id}/return/", {"reason": "x"}, format="json") \
        .status_code == 400
    r = c.post(f"{A}/orders/{order.id}/return/", {"reason": "Customer changed mind"},
               format="json")
    assert r.status_code == 200 and r.data["return_request"]["status"] == "pending"
    body = steadfast.calls[-1][2]
    assert body == {"consignment_id": order.shipment.consignment_id,
                    "reason": "Customer changed mind"}
    steadfast.routes[("POST", r"^/create_return_request$")] = (
        422, {"message": "A return request is already open."})
    r = c.post(f"{A}/orders/{order.id}/return/", {"reason": "Again please"}, format="json")
    assert r.status_code == 422 and "already open" in r.data["message"]


def test_refresh_without_parcel_is_400(as_user, owner, steadfast):
    from apps.shop.checkout import place_order
    from apps.shop.tests.helpers import one_variant, payload

    order, _ = place_order(payload(one_variant()))
    assert as_user(owner).post(f"{A}/orders/{order.id}/refresh/").status_code == 400


def test_order_detail_has_courier_block(confirmed_order, as_user, owner):
    order = confirmed_order()
    r = as_user(owner).get(f"/api/v1/admin/orders/{order.id}/")
    assert r.data["shipment"]["tracking_code"] == order.tracking_ref
    assert r.data["courier_events"][0]["source"] == "booking"
    r = as_user(owner).get("/api/v1/admin/orders/?attention=any")
    assert r.data["results"] == []


# ------------------------------------------------------------------ pickups and returns


def test_pickups_and_police_stations(as_user, owner, steadfast):  # CRR-13
    c = as_user(owner)
    assert c.get(f"{A}/police-stations/").data == [
        {"id": 17, "name": "Dhanmondi", "district": "Dhaka"}]
    bad = c.post(f"{A}/pickups/", {"address_id": 42, "police_station_id": 17,
                                    "address": "House 17", "contact_number": "01212345678"},
                 format="json")
    assert bad.status_code == 400
    r = c.post(f"{A}/pickups/", {"address_id": 42, "police_station_id": 17,
                                  "address": "House 17", "contact_number": "01712345678",
                                  "estim_qty": 5}, format="json")
    assert r.status_code == 201 and r.data["steadfast_id"] == 9081 and r.data["status"] == "pending"
    assert c.get(f"{A}/pickups/").data[0]["address_id"] == 42
    steadfast.routes[("POST", r"^/create_pickup_request$")] = (409, {"message": "exists"})
    r = c.post(f"{A}/pickups/", {"address_id": 42, "police_station_id": 17,
                                  "address": "House 17", "contact_number": "01712345678"},
               format="json")
    assert r.status_code == 409 and PickupRequest.objects.count() == 1


def test_return_requests_list_and_detail(as_user, owner, steadfast):  # CRR-14
    c = as_user(owner)
    assert c.get(f"{A}/returns/?page=1").data["data"][0]["id"] == 1
    assert c.get(f"{A}/returns/1/").data["status"] == "pending"


def test_fraud_check(as_user, owner, steadfast):  # CRR-15
    c = as_user(owner)
    r = c.get(f"{A}/fraud-check/?phone=+8801712345678")
    assert r.data["delivery_ratio"] == 92 and steadfast.calls[-1][1].endswith("/01712345678")
    assert c.get(f"{A}/fraud-check/?phone=").status_code == 400
    assert c.get(f"{A}/fraud-check/?phone=123").status_code == 400


# ------------------------------------------------------------------ permissions (scenario 40)


def test_scenario_40_courier_permission_matrix(make_staff, as_user, steadfast, confirmed_order):
    order = confirmed_order()
    viewer = as_user(make_staff("courier.view"))
    assert viewer.get(f"{A}/dashboard/").status_code == 200
    assert viewer.get(f"{A}/returns/").status_code == 200
    for method, url in [("post", f"{A}/orders/{order.id}/book/"),
                        ("post", f"{A}/orders/{order.id}/refresh/"),
                        ("post", f"{A}/orders/{order.id}/return/"),
                        ("post", f"{A}/bulk-book/"), ("get", f"{A}/pickups/"),
                        ("get", f"{A}/police-stations/"), ("post", f"{A}/ping/"),
                        ("get", f"{A}/fraud-check/?phone=01712345678")]:
        assert getattr(viewer, method)(url).status_code == 403, url
    nobody = as_user(make_staff("orders.view"))
    assert nobody.get(f"{A}/dashboard/").status_code == 403
    assert nobody.get(f"{A}/fraud-check/?phone=01712345678").status_code == 200
    manager = as_user(make_staff("courier.manage"))  # PRM-05: implies courier.view
    assert manager.get(f"{A}/dashboard/").status_code == 200
    assert manager.get(f"{A}/pickups/").status_code == 200
