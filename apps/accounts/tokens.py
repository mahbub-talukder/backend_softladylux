from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken


def set_auth_cookies(response, user, remember: bool = True):
    """SRS ARC-06 / ACC-03.

    Issue access + refresh JWTs as HTTP-only cookies. Without `remember`, cookies are
    session cookies (cleared when the browser closes), per ACC-03."""
    refresh = RefreshToken.for_user(user)
    refresh["tv"] = user.token_version  # ACC-11: lets a password reset end every older session
    common = {
        "httponly": True,
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
        "path": "/",
    }
    access_age = int(refresh.access_token.lifetime.total_seconds()) if remember else None
    refresh_age = int(refresh.lifetime.total_seconds()) if remember else None
    response.set_cookie(settings.AUTH_COOKIE_ACCESS, str(refresh.access_token),
                        max_age=access_age, **common)
    response.set_cookie(settings.AUTH_COOKIE_REFRESH, str(refresh),
                        max_age=refresh_age, **common)
    return response


def clear_auth_cookies(response):
    for name in (settings.AUTH_COOKIE_ACCESS, settings.AUTH_COOKIE_REFRESH):
        response.delete_cookie(name, path="/", samesite=settings.AUTH_COOKIE_SAMESITE)
    return response
