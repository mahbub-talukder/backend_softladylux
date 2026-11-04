"""Inventory admin API. SRS INV-07/08/10/11/12, PRM-03/05: `inventory.view` reads,
`inventory.adjust` changes stock. Every change goes through record_movement (logged, locked)."""

import csv

from django.db.models import Q
from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import require
from apps.catalog.models import Variant
from apps.core import audit
from apps.core.models import SiteSettings

from .models import Reason, StockMovement
from .services import record_movement

Invalid = serializers.ValidationError
KINDS = {  # INV-07: stock in, correction, damage, loss
    "stock_in": (1, True), "correction": (0, False), "damage": (-1, True), "loss": (-1, True)}


def level(v, threshold):
    return "out" if v.on_hand == 0 else ("low" if v.on_hand <= threshold else "in_stock")


class VariantRowSerializer(serializers.ModelSerializer):
    product = serializers.CharField(source="product.name")
    category = serializers.CharField(source="product.category.name")
    label = serializers.SerializerMethodField()
    level = serializers.SerializerMethodField()

    class Meta:
        model = Variant
        fields = ["id", "sku", "product", "category", "label", "on_hand", "level", "is_active"]

    def get_label(self, v):
        return " / ".join(x for x in (v.color_name, v.size_label) if x)

    def get_level(self, v):
        return level(v, self.context["threshold"])


def stock_queryset(request):
    qs = Variant.objects.select_related("product__category").order_by("product__name", "id")
    q = request.query_params
    th = SiteSettings.load().low_stock_threshold
    if t := q.get("q", "").strip():
        qs = qs.filter(Q(sku__icontains=t) | Q(product__name__icontains=t))
    if c := q.get("category"):
        qs = qs.filter(product__category_id=c)
    if s := q.get("status"):  # chips: In stock / Low / Out (INV-10)
        qs = {"out": qs.filter(on_hand=0), "low": qs.filter(on_hand__gt=0, on_hand__lte=th),
              "in_stock": qs.filter(on_hand__gt=th)}.get(s, qs)
    return qs, th


class StockListView(ListAPIView):
    permission_classes = [require("inventory.view")]
    serializer_class = VariantRowSerializer

    def get_queryset(self):
        return stock_queryset(self.request)[0]

    def get_serializer_context(self):
        th = SiteSettings.load().low_stock_threshold
        return {**super().get_serializer_context(), "threshold": th}

    def list(self, request, *args, **kwargs):
        resp = super().list(request, *args, **kwargs)
        th = SiteSettings.load().low_stock_threshold
        base = Variant.objects.filter(is_active=True)  # INV-11 counts for the dashboard/KPIs
        resp.data["summary"] = {
            "in_stock": base.filter(on_hand__gt=th).count(),
            "low": base.filter(on_hand__gt=0, on_hand__lte=th).count(),
            "out": base.filter(on_hand=0).count(), "threshold": th}
        return resp


class MovementSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="variant.sku")
    user = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = ["id", "created_at", "sku", "delta_on_hand", "on_hand_after", "reason",
                  "order_ref", "lot_reference", "remarks", "user"]

    def get_user(self, m):
        return str(m.user) if m.user else None


class MovementListView(ListAPIView):
    """INV-10 movement history; `?variant=<id>` for one variant, `?reason=` to filter."""

    permission_classes = [require("inventory.view")]
    serializer_class = MovementSerializer

    def get_queryset(self):
        qs = StockMovement.objects.select_related("variant", "user")
        if v := self.request.query_params.get("variant"):
            qs = qs.filter(variant_id=v)
        if r := self.request.query_params.get("reason"):
            qs = qs.filter(reason=r)
        return qs


class AdjustView(APIView):
    """INV-07: signed quantity, kind, lot/shipment reference and remarks. Lot is required except
    for corrections; remarks are always required (3–300 characters, §10). Below zero is rejected."""

    permission_classes = [require("inventory.adjust")]

    def post(self, request):
        d = request.data
        kind = d.get("kind")
        if kind not in KINDS:
            raise Invalid({"kind": "Choose stock in, correction, damage or loss."})
        try:
            qty = int(d.get("quantity"))
        except (TypeError, ValueError):
            raise Invalid({"quantity": "Enter a whole number."}) from None
        sign, lot_required = KINDS[kind]
        if kind == "correction":
            if qty == 0:
                raise Invalid({"quantity": "Enter a non-zero quantity."})
            delta = qty  # signed
        else:
            if qty <= 0:
                raise Invalid({"quantity": "Enter a quantity above zero."})
            delta = sign * qty
        lot, remarks = (d.get("lot_reference") or "").strip(), (d.get("remarks") or "").strip()
        if lot_required and not lot:
            raise Invalid({"lot_reference": "Lot / shipment reference is required."})
        if not 3 <= len(remarks) <= 300:
            raise Invalid({"remarks": "Remarks are required (3–300 characters)."})
        variant = Variant.objects.filter(pk=d.get("variant")).first()
        if variant is None:
            raise Invalid({"variant": "Choose a SKU."})
        reason = Reason.ADJUSTMENT_IN if delta > 0 else Reason.ADJUSTMENT_OUT
        m = record_movement(variant, delta, reason, user=request.user, lot_reference=lot[:80],
                            remarks=f"[{kind}] {remarks}"[:300])
        audit.log(request, "stock_adjust", "Variant", variant.pk,
                  {"on_hand": m.on_hand_after - delta}, {"on_hand": m.on_hand_after, "kind": kind})
        return Response(MovementSerializer(m).data, status=201)


class StockExportView(APIView):
    """INV-10: CSV of the current filter."""

    permission_classes = [require("inventory.view")]

    def get(self, request):
        qs, th = stock_queryset(request)
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="stock.csv"'
        w = csv.writer(resp)
        w.writerow(["sku", "product", "category", "color", "size", "on_hand", "level", "active"])
        for v in qs:
            w.writerow([v.sku, v.product.name, v.product.category.name, v.color_name, v.size_label,
                        v.on_hand, level(v, th), "yes" if v.is_active else "no"])
        return resp
