"""Admin APIs for settings, theme, media, website content and the audit log.

SRS ADM-04/05/10/11, SET-07, THM-02/07/08/10/14/19, WEB-01..10, PRM-03 (each view declares
the permission it needs), §2.2 (audit log is Owner-only).
"""

from rest_framework import generics, mixins, serializers, status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwner, require

from . import audit, cache, theme
from .models import (
    Announcement,
    AuditLog,
    Banner,
    CarouselSlide,
    Page,
    SiteSettings,
    ThemePalette,
    ThemeSettings,
    TrustItem,
    Video,
)
from .serializers import (
    AdminSettingsSerializer,
    AnnouncementSerializer,
    BannerSerializer,
    MediaUploadSerializer,
    PageSerializer,
    PaletteSerializer,
    SlideSerializer,
    TrustItemSerializer,
    VideoSerializer,
)


class AdminSettingsView(APIView):
    """SET-07: only `settings.manage` (Owner by default) edits; every save is audited
    with old and new values. The SMS API key is never logged or returned."""

    permission_classes = [require("settings.manage")]

    def get(self, request):
        return Response(AdminSettingsSerializer(SiteSettings.load()).data)

    def patch(self, request):
        obj = SiteSettings.load()
        before = AdminSettingsSerializer(obj).data
        ser = AdminSettingsSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        after = AdminSettingsSerializer(obj).data
        keep = {k: (before.get(k), after.get(k)) for k in after if before.get(k) != after.get(k)}
        audit.log(request, "update", "SiteSettings", obj.pk,
                  {k: v[0] for k, v in keep.items()}, {k: v[1] for k, v in keep.items()})
        cache.invalidate()
        return Response(after)


class MediaUploadView(APIView):
    """WEB-09: original upload and validation. Needs whichever content permission applies."""

    permission_classes = [require("website.manage", "settings.manage", "products.manage",
                                  any_of=True)]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        ser = MediaUploadSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        asset = ser.save()
        return Response(MediaUploadSerializer(asset, context={"request": request}).data,
                        status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- theme


def _theme_payload(t: ThemeSettings):
    return {
        "palette": t.palette_id,
        "primary_hex": t.primary_hex,
        "accent_hex": t.accent_hex or None,
        "tokens": t.tokens,
        "updated_by": str(t.updated_by) if t.updated_by else None,
        "updated_at": t.updated_at,
        "low_contrast_warning": theme.readability_warning(t.primary_hex),
    }


class ThemeInputSerializer(serializers.Serializer):
    palette = serializers.PrimaryKeyRelatedField(
        queryset=ThemePalette.objects.filter(is_active=True), required=False, allow_null=True)
    primary_hex = serializers.CharField(required=False)
    accent_hex = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    confirm_low_contrast = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        p = data.get("palette")
        if p:  # THM-19: choosing a palette sets both colours
            data["primary_hex"], data["accent_hex"] = p.primary_hex, p.accent_hex
        if not data.get("primary_hex"):
            raise serializers.ValidationError({"primary_hex": "Primary colour is required."})
        try:
            data["primary_hex"] = theme.normalize_hex(data["primary_hex"])
            acc = data.get("accent_hex")
            data["accent_hex"] = theme.normalize_hex(acc) if acc else None
        except ValueError as e:
            raise serializers.ValidationError({"primary_hex": str(e)}) from None
        return data


def _apply_theme(request, primary, accent, palette, action):
    t = ThemeSettings.load()
    before = {"palette": t.palette_id, "primary_hex": t.primary_hex, "accent_hex": t.accent_hex}
    t.palette, t.primary_hex, t.accent_hex = palette, primary, accent or ""
    t.tokens = theme.derive_tokens(primary, accent)  # THM-14: always recomputed server-side
    t.updated_by = request.user
    t.save()
    audit.log(request, action, "ThemeSettings", t.pk, before,
              {"palette": t.palette_id, "primary_hex": primary, "accent_hex": accent})
    cache.invalidate()
    return t


class AdminThemeView(APIView):
    """THM-08/09: `theme.manage`. PUT = Save and publish. THM-07: a primary below 3:1 on the
    page background needs `confirm_low_contrast` (otherwise 400 with a warning)."""

    permission_classes = [require("theme.manage")]

    def get(self, request):
        return Response(_theme_payload(ThemeSettings.load()))

    def put(self, request):
        ser = ThemeInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if theme.readability_warning(d["primary_hex"]) and not d["confirm_low_contrast"]:
            return Response(
                {"code": "low_contrast", "message":
                 "This primary colour has low contrast on the page background. Confirm to publish.",
                 "fields": {}}, status=status.HTTP_400_BAD_REQUEST)
        t = _apply_theme(request, d["primary_hex"], d["accent_hex"], d.get("palette"), "update")
        return Response(_theme_payload(t))


class ThemeResetView(APIView):
    """THM-13: reset to Signature."""

    permission_classes = [require("theme.manage")]

    def post(self, request):
        sig = ThemePalette.objects.get(name="Signature", is_builtin=True)
        t = _apply_theme(request, sig.primary_hex, sig.accent_hex, sig, "reset")
        return Response(_theme_payload(t))


class PaletteViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin,
                     viewsets.GenericViewSet):
    """THM-18/19: list all; create (max 5 custom) and delete custom only."""

    permission_classes = [require("theme.manage")]
    serializer_class = PaletteSerializer
    queryset = ThemePalette.objects.filter(is_active=True)
    pagination_class = None

    def perform_create(self, serializer):
        custom = ThemePalette.objects.filter(is_builtin=False, is_active=True).count()
        if custom >= theme.MAX_CUSTOM_PALETTES:
            raise serializers.ValidationError(
                f"You can save up to {theme.MAX_CUSTOM_PALETTES} custom palettes.")
        p = serializer.save(created_by=self.request.user, is_builtin=False,
                            sort=100 + ThemePalette.objects.count())
        audit.log(self.request, "create", "ThemePalette", p.pk, None, serializer.data)

    def destroy(self, request, *args, **kwargs):
        p = self.get_object()
        if p.is_builtin:
            return Response({"code": "builtin_palette", "message":
                             "Built-in palettes cannot be deleted.", "fields": {}},
                            status=status.HTTP_400_BAD_REQUEST)
        audit.log(request, "delete", "ThemePalette", p.pk, PaletteSerializer(p).data, None)
        ThemeSettings.objects.filter(palette=p).update(palette=None)  # keep live colours
        p.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- website


class WebsiteViewSet(viewsets.ModelViewSet):
    """WEB-01..08, WEB-10: `website.manage`; saves are audited and clear the public cache."""

    permission_classes = [require("website.manage")]
    pagination_class = None
    audit_name = ""

    def _snapshot(self, obj):
        return self.get_serializer(obj).data

    def perform_create(self, ser):
        obj = ser.save()
        audit.log(self.request, "create", self.audit_name, obj.pk, None, self._snapshot(obj))
        cache.invalidate()

    def perform_update(self, ser):
        before = self._snapshot(ser.instance)
        obj = ser.save()
        audit.log(self.request, "update", self.audit_name, obj.pk, before, self._snapshot(obj))
        cache.invalidate()

    def perform_destroy(self, obj):
        audit.log(self.request, "delete", self.audit_name, obj.pk, self._snapshot(obj), None)
        obj.delete()
        cache.invalidate()


class SlideViewSet(WebsiteViewSet):
    serializer_class = SlideSerializer
    queryset = CarouselSlide.objects.all()
    audit_name = "CarouselSlide"

    def _check_cap(self, ser):
        # WEB-01: at most 8 active slides.
        active = CarouselSlide.objects.filter(is_active=True)
        if ser.instance:
            active = active.exclude(pk=ser.instance.pk)
        if ser.validated_data.get("is_active", True) and active.count() >= 8:
            raise serializers.ValidationError("At most 8 slides can be active at once.")

    def perform_create(self, ser):
        self._check_cap(ser)
        super().perform_create(ser)

    def perform_update(self, ser):
        self._check_cap(ser)
        super().perform_update(ser)


class BannerViewSet(WebsiteViewSet):
    """WEB-02: two fixed placements, edit only (no create/delete)."""

    serializer_class = BannerSerializer
    queryset = Banner.objects.all()
    audit_name = "Banner"
    http_method_names = ["get", "put", "patch", "head", "options"]


class VideoViewSet(WebsiteViewSet):
    serializer_class = VideoSerializer
    queryset = Video.objects.all()
    audit_name = "Video"


class AnnouncementViewSet(WebsiteViewSet):
    serializer_class = AnnouncementSerializer
    queryset = Announcement.objects.all()
    audit_name = "Announcement"


class TrustItemViewSet(WebsiteViewSet):
    serializer_class = TrustItemSerializer
    queryset = TrustItem.objects.all()
    audit_name = "TrustItem"


class PageViewSet(WebsiteViewSet):
    """WEB-07: the four static pages are edited, not created or deleted."""

    serializer_class = PageSerializer
    queryset = Page.objects.all()
    audit_name = "Page"
    http_method_names = ["get", "put", "patch", "head", "options"]


# ---------------------------------------------------------------- audit log


class AuditLogSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ["id", "created_at", "user", "action", "model", "object_id", "before", "after"]

    def get_user(self, a):
        return str(a.user) if a.user else None


class AuditLogView(generics.ListAPIView):
    """ADM-10: Owner only, paginated (ADM-07), newest first; optional ?model= filter."""

    permission_classes = [IsOwner]
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        qs = AuditLog.objects.select_related("user")
        m = self.request.query_params.get("model")
        return qs.filter(model=m) if m else qs
