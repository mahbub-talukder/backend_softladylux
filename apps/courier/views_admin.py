"""Courier admin API. SRS v1.7 CRR-02, CRR-07, CRR-12..CRR-15; PRM-03 permission on every view.

`courier.view` reads Steadfast data, `courier.manage` changes anything at Steadfast, the connection
test needs `settings.manage`, and the customer delivery record needs `orders.view`.
"""

from django.core.cache import cache
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import require
from apps.core import audit
from apps.core.models import SiteSettings
from apps.core.phone import normalize_phone
from apps.core.throttles import CourierPingThrottle
from apps.shop.models import Order
from apps.shop.serializers import AdminOrderSerializer

from . import services, steadfast
from .models import PickupRequest
from .serializers import PickupSerializer
from .steadfast import SteadfastError

DASHBOARD_TTL = 60  # CRR-12: Steadfast figures are cached for 60 seconds
STATIONS_TTL = 24 * 3600  # police stations change rarely (Steadfast's own advice)


class CourierUnavailable(APIException):
    """A Steadfast failure shown to staff as {code, message, fields}."""

    def __init__(self, err: SteadfastError):
        super().__init__(str(err), err.code)
        self.code, self.fields = err.code, err.errors
        if err.code in ("keys_rejected", "not_configured"):
            self.status_code = 409
        elif err.status in (400, 404, 409, 422, 429):
            self.status_code = err.status
        else:
            self.status_code = 503


def _call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except SteadfastError as e:
        raise CourierUnavailable(e) from None


def _part(fn, *args):
    """Dashboard pieces fail independently, so one slow endpoint does not blank the page."""
    try:
        return {"ok": True, "data": fn(*args)}
    except SteadfastError as e:
        return {"ok": False, "code": e.code, "message": str(e)}


def _order_payload(order_id, request):
    order = Order.objects.select_related("payment", "user").prefetch_related(
        "items", "logs__user").get(pk=order_id)
    return AdminOrderSerializer(order, context={"request": request,
                                                "site": SiteSettings.load()}).data


class PingView(APIView):
    """CRR-02: Test connection. /ping proves Steadfast is reachable, /get_balance the keys."""

    permission_classes = [require("settings.manage")]
    throttle_classes = [CourierPingThrottle]

    def post(self, request):
        reach = _part(steadfast.ping)
        keys = _part(steadfast.balance) if reach["ok"] else {
            "ok": False, "code": "skipped", "message": "Skipped: Steadfast is not reachable."}
        audit.log(request, "courier_test", "SiteSettings", 1, None,
                  {"reachable": reach["ok"], "keys_ok": keys["ok"]})
        return Response({"reachable": reach, "keys": keys, "ok": reach["ok"] and keys["ok"]})


class DashboardView(APIView):
    """CRR-12: balance, the four dashboard states and stats for a date range."""

    permission_classes = [require("courier.view")]

    def get(self, request):
        d_from = services.parse_date(request.query_params.get("from"))
        d_to = services.parse_date(request.query_params.get("to"))
        if not (d_from and d_to):
            d_from, d_to = services.default_stats_range()
        key = f"courier:dashboard:{d_from}:{d_to}"
        data = cache.get(key)
        if data is None:
            data = {"balance": _part(steadfast.balance), "states": _part(steadfast.parcel_states),
                    "stats": _part(steadfast.stats, d_from, d_to), "from": d_from, "to": d_to}
            if all(data[k]["ok"] for k in ("balance", "states", "stats")):
                cache.set(key, data, DASHBOARD_TTL)
        site = SiteSettings.load()
        return Response({**data, "configured": steadfast.is_configured(),
                         "keys_rejected": site.steadfast_keys_rejected})


class ParcelStateView(APIView):
    """CRR-12 drill-down: the parcels in one dashboard state, a page at a time."""

    permission_classes = [require("courier.view")]

    def get(self, request, state):
        page = max(int(request.query_params.get("page", 1) or 1), 1)
        key = f"courier:state:{state}:{page}"
        data = cache.get(key)
        if data is None:
            data = _call(steadfast.parcels_in_state, state, page)
            cache.set(key, data, DASHBOARD_TTL)
        return Response(data)


class BookOrderView(APIView):
    """CRR-05: Book with Steadfast (first time, or retry after a failed booking)."""

    permission_classes = [require("courier.manage")]

    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk)
        _ship, err = services.book(order.id, request.user)
        audit.log(request, "courier_book", "Order", order.pk, None, {"ok": err is None})
        if err is not None:
            raise CourierUnavailable(err)
        return Response(_order_payload(order.id, request))


class RefreshOrderView(APIView):
    """CRR-11 on demand: re-read the parcel's status and steps now."""

    permission_classes = [require("courier.manage")]

    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk)
        ship = getattr(order, "shipment", None)
        if ship is None or not ship.is_booked:
            raise serializers.ValidationError({"order": "This order has no Steadfast parcel."})
        _call(services.refresh, ship)
        return Response(_order_payload(order.id, request))


class ReturnOrderView(APIView):
    """CRR-14: Ask Steadfast to return a Shipping parcel."""

    permission_classes = [require("courier.manage")]

    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk)
        data = _call(services.request_return, order.id, request.data.get("reason"), request.user)
        audit.log(request, "courier_return", "Order", order.pk, None,
                  {"reason": request.data.get("reason")})
        return Response({"return_request": data, "order": _order_payload(order.id, request)})


class BulkBookView(APIView):
    """CRR-07: bulk Book with Steadfast; every failure is reported per order."""

    permission_classes = [require("courier.manage")]

    def post(self, request):
        ids = request.data.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise serializers.ValidationError({"ids": "Select Confirmed orders to book."})
        result = services.bulk_book([int(i) for i in ids], request.user)
        audit.log(request, "courier_bulk_book", "Order", ",".join(map(str, ids)), None,
                  {"done": result["done"], "failed": [f["number"] for f in result["failed"]]})
        return Response(result)


class PickupView(APIView):
    """CRR-13: list pickups sent from here; send a new one."""

    permission_classes = [require("courier.manage")]

    def get(self, request):
        rows = PickupRequest.objects.select_related("requested_by")[:100]
        return Response(PickupSerializer(rows, many=True).data)

    def post(self, request):
        ser = PickupSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        pickup = _call(services.create_pickup, ser.validated_data, request.user)
        audit.log(request, "courier_pickup", "PickupRequest", pickup.pk, None,
                  {"address_id": pickup.address_id})
        return Response(PickupSerializer(pickup).data, status=201)


def _station_rows(data):
    """The /police_stations shape is not documented: accept a list at the top level or under a
    common key, and keep id, name and district."""
    rows = data
    if isinstance(data, dict):
        rows = next((data[k] for k in ("police_stations", "data", "stations")
                     if isinstance(data.get(k), list)), [])
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or r.get("id") is None:
            continue
        district = r.get("district")
        if isinstance(district, dict):
            district = district.get("name")
        out.append({"id": r["id"], "name": r.get("name") or r.get("title") or str(r["id"]),
                    "district": district or r.get("district_name") or ""})
    return out


class PoliceStationsView(APIView):
    """CRR-13: thana list for the pickup form, cached for a day."""

    permission_classes = [require("courier.manage")]

    def get(self, request):
        rows = cache.get("courier:police_stations")
        if rows is None:
            rows = _station_rows(_call(steadfast.police_stations))
            if rows:
                cache.set("courier:police_stations", rows, STATIONS_TTL)
        return Response(rows)


class ReturnListView(APIView):
    """CRR-14: Steadfast return requests, newest first, ten to a page."""

    permission_classes = [require("courier.view")]

    def get(self, request):
        page = max(int(request.query_params.get("page", 1) or 1), 1)
        return Response(_call(steadfast.return_requests, page))


class ReturnDetailView(APIView):
    permission_classes = [require("courier.view")]

    def get(self, request, rid):
        return Response(_call(steadfast.return_request, rid))


class FraudCheckView(APIView):
    """CRR-15: the customer's cross-merchant delivery record, fetched only on request."""

    permission_classes = [require("orders.view")]

    def get(self, request):
        try:
            phone = normalize_phone(request.query_params.get("phone"))
        except ValueError as e:
            raise serializers.ValidationError({"phone": str(e)}) from None
        if not phone:
            raise serializers.ValidationError({"phone": "Enter a mobile number."})
        return Response(_call(steadfast.fraud_score, phone))
