"""Admin order and coupon APIs. SRS ORD-07..ORD-12, PAY-03/06/08, CPN-05, PRM-03/05: reading needs
`orders.view`, changing needs `orders.manage`, payment actions need `payments.verify`."""

from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import require
from apps.core import audit
from apps.core.exceptions import Conflict
from apps.core.models import SiteSettings

from . import orders as svc
from .models import Coupon, Order, OrderStatus
from .serializers import (
    AdminOrderRowSerializer,
    AdminOrderSerializer,
    CouponSerializer,
    PaymentSerializer,
)

BULK_TARGETS = {"confirmed", "shipping", "cancelled"}  # ORD-07: Confirm / Ship / Cancel


class OrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    http_method_names = ["get", "post", "patch", "head", "options"]
    VIEW = ("list", "retrieve")
    PAYMENT = ("payment_verify", "payment_reject", "payment_edit")

    def get_permissions(self):
        if self.action in self.VIEW:
            code = "orders.view"
        elif self.action in self.PAYMENT:
            code = "payments.verify"
        else:
            code = "orders.manage"
        return [require(code)()]

    def get_queryset(self):
        qs = Order.objects.select_related("payment", "user").prefetch_related(
            "items", "logs__user")
        q = self.request.query_params
        if t := q.get("q", "").strip():  # ORD-07: order no., name, phone, TrxID
            qs = qs.filter(Q(number__icontains=t) | Q(name__icontains=t) | Q(phone__icontains=t)
                           | Q(payment__trx_id__icontains=t.upper()))
        if s := q.get("status"):
            qs = qs.filter(status=s)
        if m := q.get("payment_method"):
            qs = qs.filter(payment_method=m)
        if p := q.get("payment_status"):  # PAY-08 queue: payment_status=pending
            qs = qs.filter(payment__status=p)
        if a := q.get("area"):
            qs = qs.filter(area=a)
        if d := q.get("date_from"):
            qs = qs.filter(placed_at__gte=timezone.make_aware(datetime.combine(
                datetime.fromisoformat(d).date(), time.min)))
        if d := q.get("date_to"):
            qs = qs.filter(placed_at__lt=timezone.make_aware(datetime.combine(
                datetime.fromisoformat(d).date() + timedelta(days=1), time.min)))
        if q.get("stale") in ("1", "true"):  # ORD-12
            hours = SiteSettings.load().stale_draft_hours
            qs = qs.filter(status=OrderStatus.DRAFT,
                           placed_at__lt=timezone.now() - timedelta(hours=hours))
        order = q.get("ordering", "-placed_at")
        return qs.order_by(order if order.lstrip("-") in ("placed_at", "total", "number")
                           else "-placed_at")

    def get_serializer_class(self):
        return AdminOrderRowSerializer if self.action == "list" else AdminOrderSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "site": SiteSettings.load()}

    def list(self, request, *args, **kwargs):
        resp = super().list(request, *args, **kwargs)
        counts = dict(Order.objects.values_list("status").annotate(n=Count("id")))
        resp.data["counts"] = {s: counts.get(s, 0) for s in OrderStatus.values}  # tab badges
        return resp

    def _fresh(self, pk):
        return AdminOrderSerializer(self.get_queryset().get(pk=pk),
                                    context=self.get_serializer_context()).data

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        """ORD-01..05/10: move to the next stage. Body: to, reason, note, courier, tracking."""
        order = get_object_or_404(Order, pk=pk)
        before = order.status
        svc.transition(order.id, request.data.get("to"), request.user,
                       reason=request.data.get("reason", ""), note=request.data.get("note", ""),
                       courier=request.data.get("courier", ""),
                       tracking=request.data.get("tracking", ""))
        audit.log(request, "order_" + str(request.data.get("to")), "Order", order.pk,
                  {"status": before}, {"status": request.data.get("to")})
        return Response(self._fresh(order.pk))

    @action(detail=False, methods=["post"], url_path="bulk-transition")
    def bulk_transition(self, request):
        """ORD-07: bulk Confirm / Ship / Cancel. Orders that fail the rules are skipped and every
        failure is reported with its reason."""
        ids, to = request.data.get("ids") or [], request.data.get("to")
        if to not in BULK_TARGETS or not ids:
            raise serializers.ValidationError(
                {"to": "Choose Confirm, Ship or Cancel and select orders."})
        done, failed = [], []
        for order in Order.objects.filter(pk__in=ids).order_by("id"):
            try:
                svc.transition(order.id, to, request.user, reason=request.data.get("reason", ""),
                               courier=request.data.get("courier", ""),
                               tracking=request.data.get("tracking", ""))
                done.append(order.number)
            except Conflict as e:
                failed.append({"id": order.id, "number": order.number, "message": str(e.detail)})
            except serializers.ValidationError as e:
                failed.append({"id": order.id, "number": order.number,
                               "message": " ".join(str(x) for v in _flat(e.detail) for x in v)})
        audit.log(request, f"bulk_{to}", "Order", ",".join(map(str, ids)), None,
                  {"done": done, "failed": [f["number"] for f in failed]})
        return Response({"done": done, "failed": failed})

    def partial_update(self, request, pk=None):
        """ORD-09: address, phone and note on Draft/Confirmed orders (logged)."""
        order = get_object_or_404(Order, pk=pk)
        svc.edit_order(order.id, request.user, **{k: v for k, v in request.data.items()})
        return Response(self._fresh(order.pk))

    @action(detail=True, methods=["post"])
    def notes(self, request, pk=None):
        """ORD-08 internal notes (staff-only timeline entries)."""
        order = get_object_or_404(Order, pk=pk)
        svc.add_note(order, request.user, request.data.get("note"))
        return Response(self._fresh(order.pk), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="payment/verify")
    def payment_verify(self, request, pk=None):
        order = get_object_or_404(Order, pk=pk)
        pay = svc.verify_payment(order.id, request.user)
        audit.log(request, "payment_verified", "Payment", pay.pk, None, {"order": order.number})
        return Response(self._fresh(order.pk))

    @action(detail=True, methods=["post"], url_path="payment/reject")
    def payment_reject(self, request, pk=None):
        order = get_object_or_404(Order, pk=pk)
        pay = svc.reject_payment(order.id, request.user, request.data.get("reason"))
        audit.log(request, "payment_rejected", "Payment", pay.pk, None,
                  {"order": order.number, "reason": pay.reject_reason})
        return Response(self._fresh(order.pk))

    @action(detail=True, methods=["patch"], url_path="payment")
    def payment_edit(self, request, pk=None):
        """PAY-06: correct the TrxID / sender number (keeps per-method uniqueness)."""
        order = get_object_or_404(Order, pk=pk)
        pay = svc.edit_payment(order.id, request.user, trx_id=request.data.get("trx_id"),
                               sender_number=request.data.get("sender_number"),
                               note=request.data.get("note", ""))
        audit.log(request, "payment_edited", "Payment", pay.pk, None, {"order": order.number})
        return Response(PaymentSerializer(pay).data)


def _flat(detail):
    if isinstance(detail, dict):
        for v in detail.values():
            yield from _flat(v)
    elif isinstance(detail, list):
        for v in detail:
            yield from _flat(v)
    else:
        yield [detail]


class CouponViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                    mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """CPN-01/CPN-05: create, edit, quick enable/disable (PATCH is_active). No delete: usage
    history must survive (rule 11 spirit); deactivate instead."""

    permission_classes = [require("coupons.manage")]
    serializer_class = CouponSerializer
    queryset = Coupon.objects.prefetch_related("categories", "products")
    pagination_class = None
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def perform_create(self, ser):
        c = ser.save()
        audit.log(self.request, "create", "Coupon", c.pk, None, {"code": c.code})

    def perform_update(self, ser):
        before = {"is_active": ser.instance.is_active, "value": str(ser.instance.value)}
        c = ser.save()
        audit.log(self.request, "update", "Coupon", c.pk, before,
                  {"is_active": c.is_active, "value": str(c.value)})

    @action(detail=True, methods=["get"])
    def orders(self, request, pk=None):
        """CPN-05 'used by': orders that used this code."""
        c = self.get_object()
        rows = Order.objects.filter(coupon_code=c.code).order_by("-placed_at")[:100]
        return Response([{"id": o.id, "number": o.number, "status": o.status, "name": o.name,
                          "phone": o.phone, "discount": str(o.coupon_discount),
                          "placed_at": o.placed_at} for o in rows])
