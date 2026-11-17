"""Admin APIs for settings, theme, media, website content and the audit log.

SRS ADM-04/05/10/11, SET-07, THM-02/07/08/10/14/19, WEB-01..10, PRM-03 (each view declares
the permission it needs), §2.2 (audit log is Owner-only).
"""

from rest_framework import generics, mixins, serializers, status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwner, require

from . import audit, cache, sms, theme
from .models import (
    HOME_SECTIONS,
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
from .phone import normalize_phone
from .serializers import (
    SECRET_FIELDS,
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
from .throttles import SmsTestThrottle


class AdminSettingsView(APIView):
    """SET-07: only `settings.manage` (Owner by default) edits; every save is audited
    with old and new values. Credentials (SECRET_FIELDS) are shown to this permission but the audit
    only records that they changed, never their values (ACC-02, CRR-01)."""

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
        keep.update({k: ("(hidden)", "(changed)") for k in SECRET_FIELDS if k in keep})
        audit.log(request, "update", "SiteSettings", obj.pk,
                  {k: v[0] for k, v in keep.items()}, {k: v[1] for k, v in keep.items()})
        cache.invalidate()
        return Response(after)


class SmsTestView(APIView):
    """ACC-02 (amendment v1.6): Admin → Settings → SMS "Send test SMS". Sends synchronously with the
    saved settings and returns the gateway's answer, so the owner can check the setup. Audited
    (number only, never the key) and rate-limited, because every SMS costs credit."""

    permission_classes = [require("settings.manage")]
    throttle_classes = [SmsTestThrottle]

    def post(self, request):
        try:
            phone = normalize_phone(request.data.get("phone"))
        except ValueError as e:
            raise serializers.ValidationError({"phone": str(e)}) from None
        if not phone:
            raise serializers.ValidationError({"phone": "Enter a mobile number."})
        name = SiteSettings.load().company_name or "SoftLadyLux"
        try:
            trxn = sms.send_sms(phone, f"{name}: test SMS from Admin settings. SMS is working.")
        except sms.SmsError as e:
            audit.log(request, "sms_test", "SiteSettings", 1, None, {"phone": phone, "ok": False})
            return Response({"ok": False, "detail": str(e)})  # shown inline, not as an error
        audit.log(request, "sms_test", "SiteSettings", 1, None, {"phone": phone, "ok": True})
        return Response({"ok": True, "detail": f"Sent (transaction {trxn or 'n/a'})."})


class HomeSectionsView(APIView):
    """WEB-06: switch home sections on or off (`website.manage`). Saved on the settings row,
    audited, and the public cache is cleared so the change shows within the minute."""

    permission_classes = [require("website.manage")]

    @staticmethod
    def _state(obj):
        return [{"key": k, "label": label, "on": bool(obj.home_sections.get(k, True))}
                for k, label in HOME_SECTIONS]

    def get(self, request):
        return Response(self._state(SiteSettings.load()))

    def put(self, request):
        obj = SiteSettings.load()
        before = self._state(obj)
        sent = request.data if isinstance(request.data, dict) else {}
        keys = {k for k, _ in HOME_SECTIONS}
        if not set(sent) <= keys or not all(isinstance(v, bool) for v in sent.values()):
            raise serializers.ValidationError(
                {"detail": "Send {section: true|false} for known sections."})
        obj.home_sections = {**obj.home_sections, **sent}
        obj.save(update_fields=["home_sections"])
        after = self._state(obj)
        audit.log(request, "update", "HomeSections", obj.pk, before, after)
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
    issues = theme.readability_issues(t.primary_hex, t.background_hex, t.text_hex)
    return {
        "palette": t.palette_id,
        "primary_hex": t.primary_hex,
        "accent_hex": t.accent_hex or None,
        "background_hex": t.background_hex,
        "text_hex": t.text_hex,
        "tokens": t.tokens,
        "updated_by": str(t.updated_by) if t.updated_by else None,
        "updated_at": t.updated_at,
        "low_contrast_warning": bool(issues),
        "contrast_issues": issues,
        # THM-20/21 (v1.9): current selections and the catalogue for the Fonts section
        "storefront_font": t.storefront_font,
        "admin_font": t.admin_font,
        "font_pairings": list(theme.FONT_PAIRINGS.values()),
    }


COLOUR_FIELDS = ("palette", "primary_hex", "accent_hex", "background_hex", "text_hex")
FONT_FIELDS = ("storefront_font", "admin_font")


class FontInputSerializer(serializers.Serializer):
    """THM-25: only catalogue keys are accepted."""

    storefront_font = serializers.ChoiceField(choices=theme.FONT_KEYS, required=False,
                                              error_messages={"invalid_choice": "Choose a font "
                                                              "pairing from the list."})
    admin_font = serializers.ChoiceField(choices=theme.FONT_KEYS, required=False,
                                         error_messages={"invalid_choice": "Choose a font "
                                                         "pairing from the list."})


def _apply_fonts(request, fonts: dict, action="update_fonts"):
    """THM-21/25: save one or both font selections and audit old → new keys."""
    t = ThemeSettings.load()
    before = {k: getattr(t, k) for k in FONT_FIELDS}
    for k, v in fonts.items():
        setattr(t, k, v)
    after = {k: getattr(t, k) for k in FONT_FIELDS}
    if after != before:
        t.updated_by = request.user
        t.save()
        audit.log(request, action, "ThemeSettings", t.pk, before, after)
        cache.invalidate()
    return t


class ThemeInputSerializer(serializers.Serializer):
    palette = serializers.PrimaryKeyRelatedField(
        queryset=ThemePalette.objects.filter(is_active=True), required=False, allow_null=True)
    primary_hex = serializers.CharField(required=False)
    accent_hex = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    background_hex = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    text_hex = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    confirm_low_contrast = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        p = data.get("palette")
        if p:  # THM-19: choosing a palette sets all its colours
            data["primary_hex"], data["accent_hex"] = p.primary_hex, p.accent_hex
            data["background_hex"], data["text_hex"] = p.background_hex, p.text_hex
        if not data.get("primary_hex"):
            raise serializers.ValidationError({"primary_hex": "Primary colour is required."})
        errors = {}
        for field, default in (("primary_hex", None), ("accent_hex", None),
                               ("background_hex", theme.DEFAULT_BG),
                               ("text_hex", theme.DEFAULT_TEXT)):
            raw = data.get(field) or default
            try:
                data[field] = theme.normalize_hex(raw) if raw else None
            except ValueError as e:
                errors[field] = str(e)
        if errors:
            raise serializers.ValidationError(errors)
        return data


def _apply_theme(request, primary, accent, palette, action, background=None, text=None):
    t = ThemeSettings.load()
    before = {"palette": t.palette_id, "primary_hex": t.primary_hex, "accent_hex": t.accent_hex,
              "background_hex": t.background_hex, "text_hex": t.text_hex}
    background, text = background or theme.DEFAULT_BG, text or theme.DEFAULT_TEXT
    t.palette, t.primary_hex, t.accent_hex = palette, primary, accent or ""
    t.background_hex, t.text_hex = background, text
    # THM-14: always recomputed server-side
    t.tokens = theme.derive_tokens(primary, accent, background, text)
    t.updated_by = request.user
    t.save()
    audit.log(request, action, "ThemeSettings", t.pk, before,
              {"palette": t.palette_id, "primary_hex": primary, "accent_hex": accent,
               "background_hex": background, "text_hex": text})
    cache.invalidate()
    return t


class AdminThemeView(APIView):
    """THM-08/09: `theme.manage`. PUT = Save and publish. THM-07: a primary below 3:1 on the
    page background needs `confirm_low_contrast` (otherwise 400 with a warning)."""

    permission_classes = [require("theme.manage")]

    def get(self, request):
        return Response(_theme_payload(ThemeSettings.load()))

    def put(self, request):
        fonts = FontInputSerializer(data=request.data)
        fonts.is_valid(raise_exception=True)
        if not any(k in request.data for k in COLOUR_FIELDS):
            # THM-24 (v1.9): fonts can be saved on their own, without resending the colours.
            if not fonts.validated_data:
                raise serializers.ValidationError({"storefront_font": "Choose a font pairing."})
            return Response(_theme_payload(_apply_fonts(request, fonts.validated_data)))
        ser = ThemeInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        issues = theme.readability_issues(d["primary_hex"], d["background_hex"], d["text_hex"])
        if issues and not d["confirm_low_contrast"]:
            msgs = {"primary_on_background": "The primary colour has low contrast on the page "
                    "background.", "text_on_background": "The text colour is hard to read on the "
                    "page background."}
            return Response(
                {"code": "low_contrast", "message": " ".join(msgs[i] for i in issues)
                 + " Confirm to publish.", "fields": {}, "issues": issues},
                status=status.HTTP_400_BAD_REQUEST)
        t = _apply_theme(request, d["primary_hex"], d["accent_hex"], d.get("palette"), "update",
                         d["background_hex"], d["text_hex"])
        if fonts.validated_data:
            t = _apply_fonts(request, fonts.validated_data)
        return Response(_theme_payload(t))


class ThemeResetView(APIView):
    """THM-13: reset to Signature. THM-26 (v1.9): the default fonts are restored too."""

    permission_classes = [require("theme.manage")]

    def post(self, request):
        sig = ThemePalette.objects.get(name="Signature", is_builtin=True)
        _apply_theme(request, sig.primary_hex, sig.accent_hex, sig, "reset",
                     sig.background_hex, sig.text_hex)
        t = _apply_fonts(request, {k: theme.DEFAULT_FONT for k in FONT_FIELDS}, "reset_fonts")
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
    """WEB-02 / WEB-11: fixed placements, edit only (no create/delete).
    `?placement=a,b` limits the list, so each admin tab shows only its own banners."""

    serializer_class = BannerSerializer
    audit_name = "Banner"
    http_method_names = ["get", "put", "patch", "head", "options"]

    def get_queryset(self):
        qs = Banner.objects.order_by("id")
        if self.action == "list" and (p := self.request.query_params.get("placement")):
            qs = qs.filter(placement__in=[x.strip() for x in p.split(",") if x.strip()])
        return qs


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
