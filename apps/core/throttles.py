"""Scoped rate limits. SRS §9 (login, OTP, forgot password, coupon, contact, tracking,
cart-validate), CRT-08, scenario 20. Counters live in the shared database cache, so every
gunicorn worker sees the same count. The client is identified by IP (X-Forwarded-For from nginx)."""

from rest_framework.throttling import SimpleRateThrottle


class _Scoped(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


def scoped(name: str):
    return type(f"Throttle_{name}", (_Scoped,), {"scope": name})


CartValidateThrottle = scoped("cart_validate")
CouponValidateThrottle = scoped("coupon_validate")
CheckoutThrottle = scoped("checkout")
TrackThrottle = scoped("track")
ContactThrottle = scoped("contact")
LoginThrottle = scoped("login")
OtpIpThrottle = scoped("otp_ip")
ForgotThrottle = scoped("forgot")
