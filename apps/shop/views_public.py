"""Customer-facing order endpoints. SRS §7: cart/validate, coupons/validate, checkout, orders,
orders/{number}, orders/{number}/cancel, track. Pricing is never trusted from the client."""

from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.core.exceptions import Conflict
from apps.core.models import SiteSettings
from apps.core.phone import normalize_phone
from apps.core.throttles import (
    CartValidateThrottle,
    CheckoutThrottle,
    CouponValidateThrottle,
    TrackThrottle,
)

from . import cart, coupons
from .checkout import coupon_lines, delivery_fee, method_enabled, place_order, price_lines
from .models import Area, Order, OrderStatus, PayMethod
from .orders import transition
from .serializers import CustomerOrderSerializer


def customer_or_none(request):
    u = request.user
    return u if u and u.is_authenticated and u.role == Role.CUSTOMER else None


def _lines(request):
    lines = request.data.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValidationError({"lines": "Send at least one line."})
    if len(lines) > cart.MAX_LINES:
        raise ValidationError({"lines": f"At most {cart.MAX_LINES} lines."})
    try:
        return [{"variant": int(x["variant"]), "quantity": int(x["quantity"])} for x in lines]
    except (KeyError, TypeError, ValueError):
        raise ValidationError({"lines": "Each line needs a variant and a quantity."}) from None


def _coupon_result(code, lines, request, phone=None):
    """CRT-05: a clear message for invalid, expired, minimum not met, or already used."""
    code = (code or "").strip()
    if not code:
        return None
    try:
        phone = normalize_phone(phone) if phone else None
    except ValueError:
        phone = None
    try:
        rows, _ = price_lines(lines)
    except Conflict:  # unavailable lines are reported by the cart lines themselves
        return {"code": code.upper(), "valid": False,
                "error": {"code": "lines", "message": "Fix the items in your bag first."}}
    try:
        coupon, discount = coupons.evaluate(
            code, coupon_lines(rows), user=customer_or_none(request), phone=phone)
    except coupons.CouponError as e:
        return {"code": code.upper(), "valid": False,
                "error": {"code": e.code, "message": e.message}}
    return {"code": coupon.code, "valid": True, "discount": str(discount),
            "description": coupon.description}


class CartValidateView(APIView):
    """CRT-08: public, rate-limited, never trusted for pricing (checkout recomputes)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [CartValidateThrottle]

    def post(self, request):
        lines = _lines(request)
        out, subtotal = cart.validate_lines(lines)
        site = SiteSettings.load()
        coupon = _coupon_result(request.data.get("coupon_code"), lines, request,
                                request.data.get("phone"))
        area = request.data.get("area")
        fee = delivery_fee(site, area) if area in Area.values else None
        discount = Decimal(coupon["discount"]) if coupon and coupon.get("valid") else Decimal(0)
        return Response({
            "lines": out, "subtotal": str(subtotal), "coupon": coupon,
            "delivery": {"inside_dhaka": str(site.delivery_fee_inside_dhaka),
                         "outside_dhaka": str(site.delivery_fee_outside_dhaka),
                         "selected": str(fee) if fee is not None else None},
            "total": str(subtotal - discount + (fee or 0)) if fee is not None else None,
        })


class CouponValidateView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [CouponValidateThrottle]

    def post(self, request):
        lines = _lines(request)
        res = _coupon_result(request.data.get("code"), lines, request, request.data.get("phone"))
        if res is None:
            raise ValidationError({"code": "Enter a coupon code."})
        return Response(res)


class CheckoutView(APIView):
    """CHK-06/CHK-09: idempotent. 201 for a new order, 200 when the key was already used."""

    permission_classes = [AllowAny]
    throttle_classes = [CheckoutThrottle]

    def post(self, request):
        key = request.headers.get("Idempotency-Key") or request.data.get("idempotency_key")
        order, created = place_order(dict(request.data), user=customer_or_none(request),
                                     idempotency_key=(str(key)[:64] if key else None))
        data = CustomerOrderSerializer(order).data
        return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PaymentMethodsView(APIView):
    """Which payment methods the checkout may show (§11: hidden when numbers are empty)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        site = SiteSettings.load()
        return Response({m: method_enabled(site, m) for m in PayMethod.values})


class MyOrdersView(ListAPIView):
    """ACC-06: order history for the signed-in customer."""

    permission_classes = [IsAuthenticated]
    serializer_class = CustomerOrderSerializer

    def get_queryset(self):
        u = customer_or_none(self.request)
        if u is None:
            return Order.objects.none()
        return Order.objects.filter(user=u).select_related("payment").prefetch_related(
            "items", "logs")


class MyOrderDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CustomerOrderSerializer
    lookup_field = "number"

    def get_queryset(self):
        return MyOrdersView.get_queryset(self)


class OrderCancelView(APIView):
    """ORD-04: Draft or Confirmed only. A signed-in customer cancels their own order; a guest
    proves ownership with the phone number (decision: guests may cancel via order number + phone).
    A reason is required (§10)."""

    permission_classes = [AllowAny]
    throttle_classes = [TrackThrottle]

    def post(self, request, number):
        order = get_object_or_404(Order, number=number)
        user = customer_or_none(request)
        if user is not None and order.user_id == user.id:
            pass
        else:
            try:
                phone = normalize_phone(request.data.get("phone"))
            except ValueError:
                phone = None
            if not phone or phone != order.phone:
                raise NotFound()  # never reveal whether the order exists
        if order.status not in (OrderStatus.DRAFT, OrderStatus.CONFIRMED):
            raise Conflict("This order has shipped and can no longer be cancelled. "
                           "Please contact us.", code="cannot_cancel")
        order = transition(order.id, OrderStatus.CANCELLED, user=user,
                           reason=request.data.get("reason", ""))
        return Response(CustomerOrderSerializer(order).data)


class TrackView(APIView):
    """ACC-09: a guest tracks an order with order number + phone. Wrong pairs look like 404."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [TrackThrottle]

    def post(self, request):
        number = str(request.data.get("number", "")).strip().upper()
        try:
            phone = normalize_phone(request.data.get("phone"))
        except ValueError:
            phone = None
        order = Order.objects.filter(number=number, phone=phone).select_related("payment")\
            .prefetch_related("items", "logs").first() if phone else None
        if order is None:
            raise NotFound("We couldn't find an order with that number and phone.")
        return Response(CustomerOrderSerializer(order).data)
