"""Serializers for settings, theme, media and website content.

SRS SET-06 (public fields only), WEB-01..08, WEB-09 (upload validation), THM-14.
"""

from rest_framework import serializers

from . import theme
from .models import (
    Announcement,
    Banner,
    CarouselSlide,
    MediaAsset,
    MediaKind,
    Page,
    SiteSettings,
    ThemePalette,
    TrustItem,
    Video,
)

MAX_UPLOAD = 5 * 1024 * 1024  # WEB-09
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class MediaSerializer(serializers.ModelSerializer):
    """Read shape used wherever a media asset is embedded."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = ["id", "url", "alt_text", "width", "height", "kind"]

    def get_url(self, obj):
        return obj.file.url


class MediaUploadSerializer(serializers.ModelSerializer):
    """WEB-09: JPG/PNG/WebP, max 5 MB, alt text required; original stored as uploaded."""

    url = serializers.SerializerMethodField()
    alt_text = serializers.CharField(max_length=200)
    kind = serializers.ChoiceField(choices=MediaKind.choices)

    class Meta:
        model = MediaAsset
        fields = ["id", "file", "kind", "alt_text", "width", "height", "url"]
        read_only_fields = ["width", "height"]

    def get_url(self, obj):
        return obj.file.url

    def validate_file(self, f):
        from PIL import Image

        if f.size > MAX_UPLOAD:
            raise serializers.ValidationError("Image must be 5 MB or smaller.")
        try:
            img = Image.open(f)
            fmt, size = img.format, img.size
            img.verify()
        except Exception:
            raise serializers.ValidationError("Upload a valid JPG, PNG or WebP image.") from None
        if fmt not in ALLOWED_FORMATS:
            raise serializers.ValidationError("Only JPG, PNG or WebP images are allowed.")
        f.seek(0)
        self._size = size
        return f

    def validate(self, data):
        # §10: product photos need at least 600 px on the long side.
        if data.get("kind") == MediaKind.PRODUCT and max(getattr(self, "_size", (0, 0))) < 600:
            raise serializers.ValidationError(
                {"file": "Product photos must be at least 600 px on the long side."})
        return data

    def create(self, validated):
        validated["width"], validated["height"] = getattr(self, "_size", (0, 0))
        validated["uploaded_by"] = self.context["request"].user
        return super().create(validated)


class MediaRefField(serializers.PrimaryKeyRelatedField):
    """Write by id, read as the embedded media object."""

    def __init__(self, **kw):
        if not kw.get("read_only"):
            kw.setdefault("queryset", MediaAsset.objects.all())
        super().__init__(**kw)

    def use_pk_only_optimization(self):
        return False  # we render the full media object

    def to_representation(self, value):
        return MediaSerializer(value).data


PUBLIC_SETTINGS_FIELDS = [
    "company_name", "tagline", "logo", "favicon", "address_line1", "address_line2", "city",
    "country", "support_phones", "whatsapp_number", "support_email", "working_hours",
    "facebook_url", "instagram_url", "youtube_url", "tiktok_url", "messenger_link",
    "footer_about", "copyright_text", "bkash_number", "bkash_account_type", "nagad_number",
    "nagad_account_type", "payment_instructions", "cod_enabled", "delivery_fee_inside_dhaka",
    "delivery_fee_outside_dhaka", "delivery_time_inside_dhaka", "delivery_time_outside_dhaka",
    "seo_title", "seo_description", "seo_share_image", "gtm_id",
]  # SET-06: never the internal fields (order rules, SMS credentials)


class PublicSettingsSerializer(serializers.ModelSerializer):
    logo = MediaRefField(read_only=True)
    favicon = MediaRefField(read_only=True)
    seo_share_image = MediaRefField(read_only=True)

    class Meta:
        model = SiteSettings
        fields = PUBLIC_SETTINGS_FIELDS


class AdminSettingsSerializer(serializers.ModelSerializer):
    """SET-01..05, SET-09. SMS credentials are write-only (ACC-02): only a `set` flag is shown."""

    logo = MediaRefField(allow_null=True, required=False)
    favicon = MediaRefField(allow_null=True, required=False)
    seo_share_image = MediaRefField(allow_null=True, required=False)
    mimsms_api_key = serializers.CharField(write_only=True, allow_blank=True, required=False)
    mimsms_api_key_set = serializers.SerializerMethodField()

    class Meta:
        model = SiteSettings
        exclude = ["created_at", "updated_at"]

    def get_mimsms_api_key_set(self, obj):
        return bool(obj.mimsms_api_key)

    def validate_gtm_id(self, v):
        v = v.strip()
        if v and not v.startswith("GTM-"):
            raise serializers.ValidationError("GTM container IDs look like GTM-XXXXXXX.")
        return v

    def validate_support_phones(self, v):
        if not isinstance(v, list) or not all(isinstance(p, str) for p in v):
            raise serializers.ValidationError("Provide a list of phone numbers.")
        return v

    def validate(self, data):
        for f in ("delivery_fee_inside_dhaka", "delivery_fee_outside_dhaka"):
            if f in data and data[f] < 0:
                raise serializers.ValidationError({f: "Fee cannot be negative."})
        return data


class PaletteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ThemePalette
        fields = ["id", "name", "primary_hex", "accent_hex", "is_builtin"]
        read_only_fields = ["is_builtin"]

    def validate(self, data):
        try:
            data["primary_hex"] = theme.normalize_hex(data["primary_hex"])
            data["accent_hex"] = theme.normalize_hex(data["accent_hex"])
        except ValueError as e:
            raise serializers.ValidationError(str(e)) from None
        return data


class SlideSerializer(serializers.ModelSerializer):
    media = MediaRefField()
    mobile_media = MediaRefField(allow_null=True, required=False)

    class Meta:
        model = CarouselSlide
        exclude = ["created_at", "updated_at"]


class BannerSerializer(serializers.ModelSerializer):
    media = MediaRefField(allow_null=True, required=False)

    class Meta:
        model = Banner
        exclude = ["created_at", "updated_at"]
        read_only_fields = ["placement"]  # WEB-02: only the two fixed placements


class VideoSerializer(serializers.ModelSerializer):
    thumbnail = MediaRefField(allow_null=True, required=False)

    class Meta:
        model = Video
        exclude = ["created_at", "updated_at"]


class AnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Announcement
        exclude = ["created_at", "updated_at"]


class TrustItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrustItem
        exclude = ["created_at", "updated_at"]


class PageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Page
        exclude = ["created_at", "updated_at"]
