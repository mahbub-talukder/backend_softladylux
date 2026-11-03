"""Public read-only endpoints. SRS §7 public list; SET-06, THM-06, WEB-06, WEB-07."""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog import services

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


def _cards(cond, n):
    """CAT-07/WEB-06: New arrivals, Signature and Offers fill automatically from product flags."""
    from apps.catalog import public

    return [public.card(p) for p in public.products_qs().filter(cond).distinct()[:n]]


def _categories():
    """WEB-03: category tiles for the home carousel (active categories, in order)."""
    from apps.catalog.models import Category

    return [{"name": c.name, "slug": c.slug,
             "tile": ({"url": c.tile_media.file.url, "alt_text": c.tile_media.alt_text}
                      if c.tile_media else None)}
            for c in Category.objects.filter(is_active=True).select_related("tile_media")]


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
                "categories": _categories(),
                "new_arrivals": _cards(services.new_arrivals_q(), 8),
                "signature": _cards(Q(is_signature=True), 6),
                "offers": _cards(services.offers_q(), 8),
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
