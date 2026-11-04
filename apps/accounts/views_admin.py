"""Admin auth, current-user permissions and the Owner-only Users screen API.

SRS ACC-10 (staff email + password, separate from the customer form), ACC-03 (5 failures lock
15 min), ADM-06, PRM-01..PRM-07, §7 admin list (`auth/login`, `me/`, `permissions/`, `users/`).
"""

from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core import audit

from .models import Role, User
from .permissions import (
    DEFAULT_CODES,
    IsOwner,
    IsStaffUser,
    catalogue_payload,
    close_dependencies,
    set_permissions,
    user_permissions,
)
from .tokens import clear_auth_cookies, set_auth_cookies

LOCK_AFTER = 5
LOCK_MINUTES = 15


def me_payload(user):
    """PRM-04: what the UI needs to show only permitted areas."""
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "permissions": sorted(user_permissions(user)),
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")  # ARC-06: SPA reads csrftoken
class AdminLoginView(APIView):
    """ACC-10/ACC-03: email + password for Owner/Staff only. Customers get the same generic
    error. Sets HTTP-only JWT cookies (ARC-06)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        password = str(request.data.get("password", ""))
        fail = Response({"code": "invalid_credentials",
                         "message": "Wrong email or password.", "fields": {}},
                        status=status.HTTP_401_UNAUTHORIZED)
        user = User.objects.filter(email=email, role__in=[Role.OWNER, Role.STAFF]).first()
        if user is None:
            return fail
        if user.locked_until and user.locked_until > timezone.now():
            return Response({"code": "locked", "message":
                             "Too many attempts. Try again in a few minutes.", "fields": {}},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        if not user.is_active or not user.check_password(password):
            if user.is_active:
                user.failed_login_count += 1
                if user.failed_login_count >= LOCK_AFTER:
                    user.locked_until = timezone.now() + timedelta(minutes=LOCK_MINUTES)
                    user.failed_login_count = 0
                user.save(update_fields=["failed_login_count", "locked_until", "updated_at"])
            return fail
        user.failed_login_count, user.locked_until = 0, None
        user.save(update_fields=["failed_login_count", "locked_until", "updated_at"])
        response = Response(me_payload(user))
        return set_auth_cookies(response, user, remember=bool(request.data.get("remember", True)))


@method_decorator(ensure_csrf_cookie, name="dispatch")  # ARC-06: SPA reads csrftoken
class AdminRefreshView(APIView):
    """ARC-06: rotate the refresh cookie (access lives 15 min). Deactivated users are refused
    (PRM-07)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        from django.conf import settings

        raw = request.COOKIES.get(settings.AUTH_COOKIE_REFRESH)
        try:
            token = RefreshToken(raw)
            user = User.objects.get(pk=token["user_id"], is_active=True,
                                    role__in=[Role.OWNER, Role.STAFF])
            if token.get("tv", 0) != user.token_version:
                raise TokenError("session ended")
        except (TokenError, User.DoesNotExist, TypeError, KeyError):
            return clear_auth_cookies(Response(
                {"code": "not_authenticated", "message": "Please sign in again.", "fields": {}},
                status=status.HTTP_401_UNAUTHORIZED))
        return set_auth_cookies(Response(me_payload(user)), user)


class AdminLogoutView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        return clear_auth_cookies(Response(status=status.HTTP_204_NO_CONTENT))


@method_decorator(ensure_csrf_cookie, name="dispatch")  # ARC-06: SPA reads csrftoken
class AdminMeView(APIView):
    """PRM-04: own role and permissions, read from the DB on every request (PRM-03)."""

    permission_classes = [IsStaffUser]

    def get(self, request):
        return Response(me_payload(request.user))


class PermissionCatalogueView(APIView):
    """PRM-02: catalogue with groups, descriptions, defaults and sensitive flags (Owner)."""

    permission_classes = [IsOwner]

    def get(self, request):
        return Response(catalogue_payload())


# ---------------------------------------------------------------- users (Owner only)


class UserSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "full_name", "email", "phone", "role", "is_active", "last_login",
                  "permissions"]
        read_only_fields = ["role", "last_login"]

    def get_permissions(self, u):
        return sorted(user_permissions(u))


class StaffCreateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    permissions = serializers.ListField(child=serializers.CharField(), required=False)

    def validate_email(self, v):
        if User.objects.filter(email__iexact=v).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return v.lower()

    def validate_password(self, v):
        try:
            validate_password(v)
        except DjangoValidationError as e:
            raise serializers.ValidationError(list(e.messages)) from None
        return v


def _active_owner_count():
    return User.objects.filter(role=Role.OWNER, is_active=True).count()


class UserViewSet(viewsets.ModelViewSet):
    """ADM-06: Owner creates/deactivates Staff, resets passwords, edits permissions.
    PRM-06: the Owner row can't be edited or deactivated here; the last active Owner is
    always protected."""

    permission_classes = [IsOwner]
    serializer_class = UserSerializer
    queryset = User.objects.filter(role__in=[Role.OWNER, Role.STAFF]).order_by("id")
    http_method_names = ["get", "post", "patch", "put", "head", "options"]  # put: permissions only

    def create(self, request, *args, **kwargs):
        ser = StaffCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        user = User.objects.create_user(email=d["email"], password=d["password"],
                                        full_name=d["full_name"], role=Role.STAFF)
        set_permissions(user, d.get("permissions", DEFAULT_CODES))  # PRM-01 defaults
        audit.log(request, "create", "User", user.pk, None,
                  {"email": user.email, "permissions": sorted(user_permissions(user))})
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        # PUT is only used by users/{id}/permissions/; the user row itself is edited by PATCH.
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def partial_update(self, request, *args, **kwargs):
        user = self.get_object()
        if user.role == Role.OWNER:  # PRM-06
            return Response({"code": "owner_protected", "message":
                             "The Owner account can't be edited here.", "fields": {}},
                            status=status.HTTP_403_FORBIDDEN)
        before = {"full_name": user.full_name, "is_active": user.is_active}
        ser = UserSerializer(user, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()  # PRM-07: is_active=False blocks the next request at once
        audit.log(request, "update", "User", user.pk, before,
                  {"full_name": user.full_name, "is_active": user.is_active})
        return Response(ser.data)

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        user = self.get_object()
        if user.role == Role.OWNER:
            return Response({"code": "owner_protected", "message":
                             "The Owner password can't be reset here.", "fields": {}},
                            status=status.HTTP_403_FORBIDDEN)
        new = str(request.data.get("password", ""))
        try:
            validate_password(new, user)  # ACC-12
        except DjangoValidationError as e:
            raise serializers.ValidationError({"password": list(e.messages)}) from None
        user.set_password(new)
        user.save()
        audit.log(request, "reset_password", "User", user.pk)  # never log the password
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "put"], url_path="permissions")
    def permissions(self, request, pk=None):
        """PRM-01/PRM-05/PRM-07. Dependencies are added automatically."""
        user = self.get_object()
        if user.role == Role.OWNER:  # PRM-06: the Owner always holds everything
            if request.method == "PUT":
                return Response({"code": "owner_protected", "message":
                                 "The Owner always has every permission.", "fields": {}},
                                status=status.HTTP_403_FORBIDDEN)
            return Response({"permissions": sorted(user_permissions(user))})
        if request.method == "PUT":
            codes = request.data.get("permissions")
            if not isinstance(codes, list):
                raise serializers.ValidationError({"permissions": "Provide a list of codes."})
            added, removed = set_permissions(user, close_dependencies(codes))
            audit.log(request, "permissions", "User", user.pk,
                      {"removed": sorted(removed)}, {"added": sorted(added)})
        return Response({"permissions": sorted(user_permissions(user))})
