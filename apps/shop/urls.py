from django.urls import path

from . import views_public as v

urlpatterns = [
    path("cart/validate/", v.CartValidateView.as_view(), name="cart-validate"),
    path("coupons/validate/", v.CouponValidateView.as_view(), name="coupon-validate"),
    path("checkout/", v.CheckoutView.as_view(), name="checkout"),
    path("payment-methods/", v.PaymentMethodsView.as_view(), name="payment-methods"),
    path("orders/", v.MyOrdersView.as_view(), name="my-orders"),
    path("orders/<str:number>/", v.MyOrderDetailView.as_view(), name="my-order"),
    path("orders/<str:number>/cancel/", v.OrderCancelView.as_view(), name="order-cancel"),
    path("track/", v.TrackView.as_view(), name="track"),
]
