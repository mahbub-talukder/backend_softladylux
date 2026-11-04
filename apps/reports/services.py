"""Report queries. SRS RPT-01..RPT-06, ADM-02, INS-01.

Basis: orders are counted by the day they were placed (Asia/Dhaka). Revenue counts Confirmed and
later orders and excludes Cancelled and Returned (RPT-02). Money is BDT."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Variant
from apps.core.models import SiteSettings
from apps.inventory.models import StockMovement
from apps.shop.models import Order, OrderItem, OrderStatus, WishlistItem

S = OrderStatus
REVENUE_STATUSES = [S.CONFIRMED, S.SHIPPING, S.DELIVERED]  # RPT-02
ZERO = Value(Decimal(0), output_field=DecimalField())
PRESETS = {"today", "7d", "30d", "month", "custom"}


def dhaka_day_start(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


def parse_range(params):
    """RPT-01: today, 7 days, 30 days, this month, custom. Returns (start, end_exclusive, label)."""
    today = timezone.localdate()
    preset = params.get("range", "30d")
    if preset not in PRESETS:
        raise ValidationError({"range": "Unknown range."})
    if preset == "custom":
        try:
            a = date.fromisoformat(params.get("from", ""))
            b = date.fromisoformat(params.get("to", ""))
        except ValueError:
            raise ValidationError({"from": "Choose a start and end date."}) from None
        if b < a:
            raise ValidationError({"to": "The end date must not be before the start date."})
        if (b - a).days > 366:
            raise ValidationError({"to": "Choose a range of at most one year."})
    elif preset == "today":
        a = b = today
    elif preset == "7d":
        a, b = today - timedelta(days=6), today
    elif preset == "month":
        a, b = today.replace(day=1), today
    else:
        a, b = today - timedelta(days=29), today
    return dhaka_day_start(a), dhaka_day_start(b + timedelta(days=1)), {
        "range": preset, "from": a.isoformat(), "to": b.isoformat()}


def orders_in(start, end):
    return Order.objects.filter(placed_at__gte=start, placed_at__lt=end)


def money(v) -> str:
    return str(Decimal(v or 0).quantize(Decimal("0.01")))


def daily_revenue(start, end):
    """Revenue and order count per day, including days with no orders (for the chart)."""
    rows = (orders_in(start, end).filter(status__in=REVENUE_STATUSES)
            .annotate(day=TruncDate("placed_at")).values("day")
            .annotate(revenue=Sum("total"), orders=Count("id")))
    by_day = {r["day"]: r for r in rows}
    out, day = [], timezone.localtime(start).date()
    last = timezone.localtime(end - timedelta(seconds=1)).date()
    while day <= last:
        r = by_day.get(day)
        out.append({"day": day.isoformat(), "revenue": money(r["revenue"] if r else 0),
                    "orders": r["orders"] if r else 0})
        day += timedelta(days=1)
    return out


def sales(start, end):
    qs = orders_in(start, end)
    paid = qs.filter(status__in=REVENUE_STATUSES)
    agg = paid.aggregate(revenue=Coalesce(Sum("total"), ZERO), n=Count("id"),
                         discounts=Coalesce(Sum("coupon_discount"), ZERO))
    revenue, n = agg["revenue"], agg["n"]
    items = OrderItem.objects.filter(order__in=paid)
    cats = (items.values(name=F("variant__product__category__name"))
            .annotate(revenue=Sum("line_total"), units=Sum("quantity")).order_by("-revenue"))
    top = (items.values(product=F("variant__product_id"), name=F("product_name"),
                        category=F("variant__product__category__name"))
           .annotate(units=Sum("quantity"), revenue=Sum("line_total")).order_by("-revenue")[:20])
    return {
        "summary": {"orders": qs.count(), "revenue": money(revenue),
                    "average_order_value": money(revenue / n if n else 0),
                    "returns": qs.filter(status=S.RETURNED).count(),
                    "coupon_discounts": money(agg["discounts"])},
        "by_day": daily_revenue(start, end),
        "by_category": [{"name": r["name"], "revenue": money(r["revenue"]), "units": r["units"]}
                        for r in cats],
        "top_products": [{"name": r["name"], "category": r["category"], "units": r["units"],
                          "revenue": money(r["revenue"])} for r in top],
        "by_payment_method": _group(paid, "payment_method"),
        "by_area": _group(paid, "area"),
    }


def _group(qs, field):
    rows = qs.values(field).annotate(orders=Count("id"), revenue=Sum("total")).order_by("-revenue")
    return [{"key": r[field], "orders": r["orders"], "revenue": money(r["revenue"])} for r in rows]


def funnel(start, end):
    """RPT-04: how far orders placed in the period got, with rates against orders placed."""
    qs = orders_in(start, end)
    placed = qs.count()
    stages = [("placed", placed)] + [
        (name, qs.filter(**{f"{field}__isnull": False}).count()) for name, field in [
            ("confirmed", "confirmed_at"), ("shipping", "shipped_at"),
            ("delivered", "delivered_at"), ("cancelled", "cancelled_at"),
            ("returned", "returned_at")]]
    return [{"stage": s, "count": c, "rate": round(c / placed * 100, 1) if placed else 0}
            for s, c in stages]


def inventory(start, end):
    """RPT-05: stock by variant, low / out lists, movement summary by reason for the period."""
    th = SiteSettings.load().low_stock_threshold
    variants = (Variant.objects.filter(is_active=True).select_related("product__category")
                .order_by("product__name", "id"))
    rows = [{"sku": v.sku, "product": v.product.name, "category": v.product.category.name,
             "label": " / ".join(x for x in (v.color_name, v.size_label) if x),
             "on_hand": v.on_hand, "level": "out" if v.on_hand == 0 else (
                 "low" if v.on_hand <= th else "in_stock")} for v in variants]
    moves = (StockMovement.objects.filter(created_at__gte=start, created_at__lt=end)
             .values("reason").annotate(movements=Count("id"), units=Sum("delta_on_hand"))
             .order_by("reason"))
    return {"threshold": th, "stock": rows,
            "low": [r for r in rows if r["level"] == "low"],
            "out": [r for r in rows if r["level"] == "out"],
            "movements": [{"reason": m["reason"], "movements": m["movements"],
                           "units": m["units"]} for m in moves]}


def coupons(start, end):
    """RPT-06: usage and discount by code (non-cancelled orders that used a code)."""
    rows = (orders_in(start, end).exclude(coupon_code="").exclude(status=S.CANCELLED)
            .values("coupon_code").annotate(uses=Count("id"), discount=Sum("coupon_discount"),
                                            revenue=Sum("total")).order_by("-uses"))
    return [{"code": r["coupon_code"], "uses": r["uses"], "discount": money(r["discount"]),
             "revenue": money(r["revenue"])} for r in rows]


# ------------------------------------------------------------------ dashboard (ADM-02)


def dashboard(perms: set[str], limit=8):
    """Each widget is included only when the user holds the permission for that area."""
    site = SiteSettings.load()
    now = timezone.now()
    today = timezone.localdate()
    out = {}
    if "orders.view" in perms:
        todays = orders_in(dhaka_day_start(today), dhaka_day_start(today + timedelta(days=1)))
        paid = todays.filter(status__in=REVENUE_STATUSES).aggregate(r=Coalesce(Sum("total"), ZERO))
        stale_cut = now - timedelta(hours=site.stale_draft_hours)
        week_start = dhaka_day_start(today - timedelta(days=6))
        top = (OrderItem.objects.filter(order__placed_at__gte=week_start,
                                        order__status__in=REVENUE_STATUSES)
               .values(name=F("product_name")).annotate(units=Sum("quantity"),
                                                        revenue=Sum("line_total"))
               .order_by("-revenue")[:5])
        out.update({
            "today": {"orders": todays.count(), "revenue": money(paid["r"])},
            "awaiting_confirmation": Order.objects.filter(status=S.DRAFT).count(),
            "stale_drafts": Order.objects.filter(status=S.DRAFT, placed_at__lt=stale_cut).count(),
            "sales_7d": daily_revenue(week_start, dhaka_day_start(today + timedelta(days=1))),
            "top_products": [{"name": r["name"], "units": r["units"],
                              "revenue": money(r["revenue"])} for r in top],
            "orders_by_stage": {s: Order.objects.filter(status=s).count() for s in S.values},
            "recent_orders": [{"id": o.id, "number": o.number, "name": o.name,
                               "payment_method": o.payment_method, "total": str(o.total),
                               "status": o.status}
                              for o in Order.objects.order_by("-placed_at")[:limit]],
            # PAY-08: Pending bKash/Nagad payments, oldest first
            "payments_to_verify": [
                {"id": o.id, "number": o.number, "name": o.name, "method": o.payment_method,
                 "trx_id": o.payment.trx_id, "total": str(o.total), "placed_at": o.placed_at}
                for o in Order.objects.filter(status=S.DRAFT, payment__status="pending")
                .exclude(payment_method="cod").select_related("payment").order_by("placed_at")[:10]],
        })
    if "inventory.view" in perms:
        th = site.low_stock_threshold
        base = Variant.objects.filter(is_active=True)
        low = base.filter(on_hand__gt=0, on_hand__lte=th).select_related("product")
        out["stock"] = {
            "threshold": th, "low": low.count(), "out": base.filter(on_hand=0).count(),
            "low_items": [{"sku": v.sku, "name": v.product.name, "on_hand": v.on_hand}
                          for v in low.order_by("on_hand")[:5]]}
    return out


# ------------------------------------------------------------------ wishlist insight (INS-01)

BOUGHT = [S.CONFIRMED, S.SHIPPING, S.DELIVERED]


def wishlist_overview(limit=20):
    saved = list(WishlistItem.objects.values("product", "product__name",
                                             "product__category__name")
                 .annotate(saved=Count("user", distinct=True)).order_by("-saved")[:limit])
    rows = []
    for r in saved:
        savers = WishlistItem.objects.filter(product_id=r["product"]).values("user")
        buyers = (Order.objects.filter(status__in=BOUGHT, user__in=savers,
                                       items__variant__product_id=r["product"])
                  .values("user").distinct().count())
        units = (OrderItem.objects.filter(order__status__in=BOUGHT,
                                          variant__product_id=r["product"])
                 .aggregate(n=Coalesce(Sum("quantity"), 0))["n"])
        rows.append({"product": r["product"], "name": r["product__name"],
                     "category": r["product__category__name"], "saved_by": r["saved"],
                     "bought_by_savers": buyers, "units_sold": units,
                     "conversion": round(buyers / r["saved"] * 100, 1) if r["saved"] else 0})
    return {
        "kpis": {"saves": WishlistItem.objects.count(),
                 "customers": WishlistItem.objects.values("user").distinct().count(),
                 "products": WishlistItem.objects.values("product").distinct().count()},
        "products": rows}


def wishlist_savers(product_id):
    """INS-01: which customers wishlisted a product."""
    return [{"name": w.user.full_name, "phone": w.user.phone, "saved_at": w.created_at}
            for w in WishlistItem.objects.filter(product_id=product_id)
            .select_related("user").order_by("-created_at")]

