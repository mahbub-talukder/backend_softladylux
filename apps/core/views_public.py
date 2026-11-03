"""Public read-only endpoints. SRS §7 public list; SET-06, THM-06, WEB-06, WEB-07."""

from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .cache import cached
from .models import (
    Announcement,
    Banner,
    CarouselSlide,
    Page,
    SiteSettings,
    ThemeSettings,
    TrustItem,
    Video,
)
from .serializers import (
    AnnouncementSerializer,
    BannerSerializer,
    PageSerializer,
    PublicSettingsSerializer,
    SlideSerializer,
    TrustItemSerializer,
    VideoSerializer,
)


class PublicView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]


class PublicSettingsView(PublicView):
    """SET-06: public fields only, cached, cleared on save."""

    def get(self, request):
        return Response(cached("public:settings",
                               lambda: PublicSettingsSerializer(SiteSettings.load()).data))


class PublicThemeView(PublicView):
    """THM-06: tiny cached token payload for SSR injection."""

    def get(self, request):
        def build():
            t = ThemeSettings.load()
            return {"tokens": t.tokens, "primary": t.primary_hex, "accent": t.accent_hex}

        return Response(cached("public:theme", build))


class HomeView(PublicView):
    """WEB-01..08: fixed home layout content; each block is on/off (WEB-06).
    `categories` stays empty until the catalogue arrives in M2 (WEB-03)."""

    def get(self, request):
        def build():
            return {
                "announcements": AnnouncementSerializer(
                    Announcement.objects.filter(is_active=True), many=True).data,
                "carousel": SlideSerializer(
                    CarouselSlide.objects.filter(is_active=True)[:8],  # WEB-01: max 8
                    many=True).data,
                "banners": BannerSerializer(
                    Banner.objects.filter(is_active=True, media__isnull=False), many=True).data,
                "categories": [],
                "trust": TrustItemSerializer(
                    TrustItem.objects.filter(is_active=True), many=True).data,
                "videos": VideoSerializer(Video.objects.filter(is_active=True), many=True).data,
            }

        return Response(cached("public:home", build))


class PublicPageView(PublicView):
    """WEB-07: static page by slug."""

    def get(self, request, slug):
        page = get_object_or_404(Page, slug=slug, is_active=True)
        return Response(PageSerializer(page).data)
