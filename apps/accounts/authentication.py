from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import CSRFCheck
from rest_framework_simplejwt.authentication import JWTAuthentication


class CookieJWTAuthentication(JWTAuthentication):
    """SRS ARC-06 (JWT in HTTP-only cookies, CSRF enabled), PRM-07 (deactivation blocks at once).

    JWT from the Authorization header, or from the HTTP-only access cookie.

    Cookie-authenticated requests get the same CSRF check as session auth (ARC-06).
    The user is loaded and `is_active` checked on every request, so deactivating a user
    blocks them immediately (PRM-07).
    """

    def authenticate(self, request):
        header = self.get_header(request)
        raw = self.get_raw_token(header) if header else None
        from_cookie = raw is None
        if from_cookie:
            raw = request.COOKIES.get(settings.AUTH_COOKIE_ACCESS)
        if raw is None:
            return None
        validated = self.get_validated_token(raw)
        if from_cookie:
            self._enforce_csrf(request)
        user = self.get_user(validated)
        if validated.get("tv", 0) != user.token_version:  # ACC-11 / PRM-07: session was ended
            raise exceptions.AuthenticationFailed("Please sign in again.")
        return user, validated

    @staticmethod
    def _enforce_csrf(request):
        check = CSRFCheck(lambda r: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise exceptions.PermissionDenied(f"CSRF Failed: {reason}")
