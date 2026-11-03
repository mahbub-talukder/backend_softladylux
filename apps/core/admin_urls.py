from django.urls import include, path
from rest_framework.routers import SimpleRouter

from apps.accounts import views_admin as acc

from . import views_admin as v

router = SimpleRouter()
router.register("users", acc.UserViewSet, basename="admin-user")
router.register("theme/palettes", v.PaletteViewSet, basename="admin-palette")
router.register("website/carousel", v.SlideViewSet, basename="admin-slide")
router.register("website/banners", v.BannerViewSet, basename="admin-banner")
router.register("website/videos", v.VideoViewSet, basename="admin-video")
router.register("website/announcements", v.AnnouncementViewSet, basename="admin-announcement")
router.register("website/trust", v.TrustItemViewSet, basename="admin-trust")
router.register("website/pages", v.PageViewSet, basename="admin-page")

# SRS §7 admin list. Every view declares its permission (PRM-03).
urlpatterns = [
    path("auth/login/", acc.AdminLoginView.as_view(), name="admin-login"),
    path("auth/refresh/", acc.AdminRefreshView.as_view(), name="admin-refresh"),
    path("auth/logout/", acc.AdminLogoutView.as_view(), name="admin-logout"),
    path("me/", acc.AdminMeView.as_view(), name="admin-me"),
    path("permissions/", acc.PermissionCatalogueView.as_view(), name="admin-permissions"),
    path("settings/", v.AdminSettingsView.as_view(), name="admin-settings"),
    path("theme/", v.AdminThemeView.as_view(), name="admin-theme"),
    path("theme/reset/", v.ThemeResetView.as_view(), name="admin-theme-reset"),
    path("media/", v.MediaUploadView.as_view(), name="admin-media"),
    path("audit-log/", v.AuditLogView.as_view(), name="admin-audit-log"),
    path("", include(router.urls)),
]
