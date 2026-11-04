from django.urls import include, path
from rest_framework.routers import SimpleRouter

from apps.shop.views_wishlist import WishlistView

from . import views_customer as v

router = SimpleRouter()
router.register("me/addresses", v.AddressViewSet, basename="my-address")

# SRS §7 public list: auth/register, register/verify, login, logout, password/forgot,
# password/reset, me/, me/addresses/, me/wishlist/ (+ auth/refresh and auth/otp-status).
urlpatterns = [
    path("auth/register/", v.RegisterView.as_view(), name="register"),
    path("auth/register/verify/", v.RegisterVerifyView.as_view(), name="register-verify"),
    path("auth/otp-status/", v.OtpStatusView.as_view(), name="otp-status"),
    path("auth/login/", v.LoginView.as_view(), name="login"),
    path("auth/refresh/", v.RefreshView.as_view(), name="refresh"),
    path("auth/logout/", v.LogoutView.as_view(), name="logout"),
    path("auth/password/forgot/", v.ForgotPasswordView.as_view(), name="password-forgot"),
    path("auth/password/reset/", v.ResetPasswordView.as_view(), name="password-reset"),
    path("me/", v.MeView.as_view(), name="me"),
    path("me/wishlist/", WishlistView.as_view(), name="my-wishlist"),
    path("", include(router.urls)),
]
