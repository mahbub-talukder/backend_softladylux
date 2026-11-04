"""Customer accounts. SRS ACC-01..ACC-14, ARC-06.

Registration needs name, mobile, password (+ optional email) and a verified SMS OTP; login is
mobile + password; forgot password uses an SMS OTP and signs out every other session; earlier guest
orders on the verified number are linked to the new account (ACC-13)."""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core import audit
from apps.core.phone import normalize_phone
from apps.core.throttles import ForgotThrottle, LoginThrottle, OtpIpThrottle

from . import otp as otp_service
from .models import Address, Otp, OtpPurpose, Role, User
from .tokens import clear_auth_cookies, set_auth_cookies

Invalid = serializers.ValidationError
LOCK_AFTER, LOCK_MINUTES = 5, 15
MAX_ADDRESSES = 5


class IsCustomer(BasePermission):
    """Signed-in active customer (staff use the admin API; ACC-10)."""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_active and u.role == Role.CUSTOMER)


def clean_phone(value, field="phone") -> str:
    try:
        phone = normalize_phone(value)
    except ValueError as e:
        raise Invalid({field: str(e)}) from None
    if not phone:
        raise Invalid({field: "Enter your mobile number."})
    return phone


def check_password(password: str, phone: str | None, user=None):
    """ACC-12: at least 8 characters, must not equal the mobile number."""
    if phone and password == phone:
        raise Invalid({"password": "Your password cannot be your mobile number."})
    try:
        validate_password(password, user)
    except DjangoValidationError as e:
        raise Invalid({"password": list(e.messages)}) from None


def me_payload(u: User) -> dict:
    return {"id": u.id, "full_name": u.full_name, "phone": u.phone, "email": u.email,
            "birthday": u.birthday, "phone_verified": u.phone_verified}


def link_guest_orders(user: User) -> int:
    """ACC-13: earlier guest orders on the verified number join the account's history."""
    from apps.shop.models import Order

    return Order.objects.filter(user__isnull=True, phone=user.phone).update(user=user)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class RegisterView(APIView):
    """ACC-01: validate, create an inactive account and text a verification code. Registering
    again with the same unverified number just refreshes the details and sends a new code."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [OtpIpThrottle]

    def post(self, request):
        d = request.data
        name = str(d.get("full_name", "")).strip()
        if not 2 <= len(name) <= 150:
            raise Invalid({"full_name": "Enter your full name."})
        phone = clean_phone(d.get("phone"))
        password = str(d.get("password", ""))
        email = str(d.get("email") or "").strip().lower() or None
        check_password(password, phone)
        if email:
            try:
                serializers.EmailField().run_validation(email)
            except serializers.ValidationError:
                raise Invalid({"email": "Enter a valid email address."}) from None
        existing = User.objects.filter(phone=phone).first()
        if existing and (existing.phone_verified or existing.role != Role.CUSTOMER):
            # ACC-14: customers and staff share one number space.
            raise Invalid({"phone": (
                "This number is already registered. Log in or reset your password.")})
        taken = User.objects.filter(email=email).exclude(pk=getattr(existing, "pk", None))
        if email and taken.exists():
            raise Invalid({"email": "This email is already used."})
        otp_service.check_limits(phone, OtpPurpose.REGISTER)  # nothing is saved if we can't text
        with transaction.atomic():
            if existing:
                existing.full_name, existing.email = name, email
                existing.set_password(password)
                existing.save()
            else:
                User.objects.create_user(phone=phone, email=email, password=password,
                                         full_name=name, role=Role.CUSTOMER, is_active=False)
            otp_service.issue(phone, OtpPurpose.REGISTER)
        return Response({"phone": phone, "resend_in": otp_service.RESEND_AFTER},
                        status=status.HTTP_202_ACCEPTED)


class OtpStatusView(APIView):
    """ACC-02: lets the page tell the user if the SMS could not be sent, so they can retry or
    contact support. Returns only a coarse status for the newest code."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [OtpIpThrottle]

    def get(self, request):
        phone = clean_phone(request.query_params.get("phone"))
        purpose = request.query_params.get("purpose", "register")
        otp = Otp.objects.filter(phone=phone, purpose=purpose).first()
        return Response({"status": otp.sms_status if otp else "none"})


class RegisterVerifyView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [OtpIpThrottle]

    def post(self, request):
        phone = clean_phone(request.data.get("phone"))
        user = User.objects.filter(phone=phone, role=Role.CUSTOMER, phone_verified=False).first()
        if user is None:
            raise Invalid({"code": "Start registration first."})
        otp_service.verify(phone, OtpPurpose.REGISTER, request.data.get("code"))
        with transaction.atomic():
            user.is_active, user.phone_verified = True, True
            user.save(update_fields=["is_active", "phone_verified", "updated_at"])
            linked = link_guest_orders(user)  # ACC-13
        audit.log(request, "register", "User", user.pk, None, {"linked_guest_orders": linked},
                  user=user)
        return set_auth_cookies(Response(me_payload(user), status=status.HTTP_201_CREATED), user)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class LoginView(APIView):
    """ACC-03: mobile + password, 5 failures lock for 15 minutes, "remember me" sets the cookie
    lifetime. Staff accounts cannot sign in here (ACC-10)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        fail = Response({"code": "invalid_credentials",
                         "message": "Wrong mobile number or password.", "fields": {}},
                        status=status.HTTP_401_UNAUTHORIZED)
        try:
            phone = normalize_phone(request.data.get("phone"))
        except ValueError:
            return fail
        user = User.objects.filter(phone=phone, role=Role.CUSTOMER).first() if phone else None
        if user is None:
            return fail
        if user.locked_until and user.locked_until > timezone.now():
            return Response({"code": "locked", "message":
                             "Too many attempts. Try again in a few minutes.", "fields": {}},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        password = str(request.data.get("password", ""))
        if not (user.is_active and user.phone_verified) or not user.check_password(password):
            if user.is_active:
                user.failed_login_count += 1
                if user.failed_login_count >= LOCK_AFTER:
                    user.locked_until = timezone.now() + timedelta(minutes=LOCK_MINUTES)
                    user.failed_login_count = 0
                user.save(update_fields=["failed_login_count", "locked_until", "updated_at"])
            return fail
        user.failed_login_count, user.locked_until = 0, None
        user.save(update_fields=["failed_login_count", "locked_until", "updated_at"])
        return set_auth_cookies(Response(me_payload(user)), user,
                                remember=bool(request.data.get("remember", True)))


class RefreshView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        raw = request.COOKIES.get(settings.AUTH_COOKIE_REFRESH)
        try:
            token = RefreshToken(raw)
            user = User.objects.get(pk=token["user_id"], is_active=True, role=Role.CUSTOMER)
            if token.get("tv", 0) != user.token_version:
                raise TokenError("session ended")
        except (TokenError, User.DoesNotExist, TypeError, KeyError):
            return clear_auth_cookies(Response(
                {"code": "not_authenticated", "message": "Please sign in again.", "fields": {}},
                status=status.HTTP_401_UNAUTHORIZED))
        return set_auth_cookies(Response(me_payload(user)), user)


class LogoutView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        return clear_auth_cookies(Response(status=status.HTTP_204_NO_CONTENT))


class ForgotPasswordView(APIView):
    """ACC-11: always answers the same way so numbers can't be probed; only a registered, verified
    customer actually gets an SMS (each SMS costs money)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ForgotThrottle, OtpIpThrottle]

    def post(self, request):
        phone = clean_phone(request.data.get("phone"))
        user = User.objects.filter(phone=phone, role=Role.CUSTOMER, is_active=True,
                                   phone_verified=True).first()
        if user:
            otp_service.issue(phone, OtpPurpose.RESET)
        return Response({"phone": phone, "resend_in": otp_service.RESEND_AFTER},
                        status=status.HTTP_202_ACCEPTED)


class ResetPasswordView(APIView):
    """ACC-11: verify the OTP, set the new password, sign out every other session (token_version),
    and sign this device in."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ForgotThrottle]

    def post(self, request):
        phone = clean_phone(request.data.get("phone"))
        password = str(request.data.get("new_password", ""))
        user = User.objects.filter(phone=phone, role=Role.CUSTOMER, is_active=True,
                                   phone_verified=True).first()
        if user is None:
            raise Invalid({"code": "This code is not valid. Request a new one."})
        check_password(password, phone, user)
        otp_service.verify(phone, OtpPurpose.RESET, request.data.get("code"))
        user.set_password(password)
        user.token_version += 1
        user.failed_login_count, user.locked_until = 0, None
        user.save()
        audit.log(request, "password_reset", "User", user.pk, None, None, user=user)
        return set_auth_cookies(Response(me_payload(user)), user)


class MeView(APIView):
    """ACC-04: name, mobile (read-only, verified), optional email, optional birthday. DELETE =
    account deletion request (ACC-08): personal data is anonymised, order records are kept."""

    permission_classes = [IsCustomer]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response(me_payload(request.user))

    def patch(self, request):
        u, d = request.user, request.data
        if "full_name" in d:
            name = str(d["full_name"]).strip()
            if not 2 <= len(name) <= 150:
                raise Invalid({"full_name": "Enter your full name."})
            u.full_name = name
        if "email" in d:
            email = str(d["email"] or "").strip().lower() or None
            if email:
                try:
                    serializers.EmailField().run_validation(email)
                except serializers.ValidationError:
                    raise Invalid({"email": "Enter a valid email address."}) from None
                if User.objects.filter(email=email).exclude(pk=u.pk).exists():
                    raise Invalid({"email": "This email is already used."})
            u.email = email
        if "birthday" in d:
            bd = d["birthday"] or None
            if bd:
                try:
                    bd = serializers.DateField().run_validation(bd)
                except serializers.ValidationError:
                    raise Invalid({"birthday": "Enter a valid date."}) from None
                if bd > timezone.localdate():
                    raise Invalid({"birthday": "Birthday cannot be in the future."})
            u.birthday = bd
        u.save()
        return Response(me_payload(u))

    def delete(self, request):
        u = request.user
        if not u.check_password(str(request.data.get("password", ""))):
            raise Invalid({"password": "Enter your password to confirm."})
        with transaction.atomic():
            u.addresses.all().delete()
            u.wishlist.all().delete()
            # Anonymise in place; phone is replaced by an unusable placeholder that frees the real
            # number (queryset update skips the phone normalisation on save).
            User.objects.filter(pk=u.pk).update(
                full_name="Deleted customer", email=None, phone=f"X{u.pk}", birthday=None,
                is_active=False, phone_verified=False, token_version=u.token_version + 1,
                password="!")
        audit.log(request, "account_deleted", "User", u.pk, None, None, user=u)
        return clear_auth_cookies(Response(status=status.HTTP_204_NO_CONTENT))


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = ["id", "label", "recipient_name", "phone", "area", "district", "thana",
                  "address_line", "is_default"]

    def validate_phone(self, v):
        return clean_phone(v)

    def validate_area(self, v):
        if v not in ("inside_dhaka", "outside_dhaka"):
            raise Invalid("Choose Inside Dhaka or Outside Dhaka.")
        return v


class AddressViewSet(viewsets.ModelViewSet):
    """ACC-05: up to 5 addresses, exactly one default (the first one becomes default)."""

    permission_classes = [IsCustomer]
    serializer_class = AddressSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "put", "delete", "head", "options"]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    @transaction.atomic
    def perform_create(self, ser):
        mine = Address.objects.select_for_update().filter(user=self.request.user)
        if mine.count() >= MAX_ADDRESSES:
            raise Invalid(f"You can save up to {MAX_ADDRESSES} addresses.")
        make_default = ser.validated_data.get("is_default") or not mine.exists()
        if make_default:
            mine.update(is_default=False)
        ser.save(user=self.request.user, is_default=make_default)

    @transaction.atomic
    def perform_update(self, ser):
        if ser.validated_data.get("is_default"):
            Address.objects.filter(user=self.request.user).exclude(pk=ser.instance.pk).update(
                is_default=False)
        ser.save()

    @transaction.atomic
    def perform_destroy(self, a):
        was_default = a.is_default
        a.delete()
        if was_default:  # keep exactly one default
            nxt = Address.objects.filter(user=self.request.user).first()
            if nxt:
                nxt.is_default = True
                nxt.save(update_fields=["is_default", "updated_at"])
