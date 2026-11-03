from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base for all tables.

    SRS ARC-11: every table has created_at / updated_at. Soft delete
    (is_active / deleted_at) is added per model where SRS §5 rule 11 requires it.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SingletonModel(TimeStampedModel):
    """One-row tables (SiteSettings, ThemeSettings): always pk=1."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SiteSettings(SingletonModel):
    """SRS §6 core_sitesettings; SET-01..05, SET-09. Every company-specific value on the
    storefront comes from here (SET-08). Placeholders are shown until the owner fills them."""

    # SET-01 company
    company_name = models.CharField(max_length=120, default="SoftLadyLux")
    tagline = models.CharField(max_length=200, blank=True)
    logo = models.ForeignKey("MediaAsset", null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+")
    favicon = models.ForeignKey("MediaAsset", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=80, blank=True)
    country = models.CharField(max_length=80, default="Bangladesh")
    support_phones = models.JSONField(default=list, blank=True)
    whatsapp_number = models.CharField(max_length=20, blank=True)
    support_email = models.EmailField(blank=True)
    working_hours = models.CharField(max_length=200, blank=True)
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)
    tiktok_url = models.URLField(blank=True)
    messenger_link = models.URLField(blank=True)
    footer_about = models.TextField(blank=True)
    copyright_text = models.CharField(max_length=200, blank=True)

    # SET-02 payment configuration
    bkash_number = models.CharField(max_length=20, blank=True)
    bkash_account_type = models.CharField(max_length=20, blank=True)
    nagad_number = models.CharField(max_length=20, blank=True)
    nagad_account_type = models.CharField(max_length=20, blank=True)
    payment_instructions = models.TextField(blank=True)
    cod_enabled = models.BooleanField(default=True)

    # SET-03 delivery fees (BDT) and time text
    delivery_fee_inside_dhaka = models.DecimalField(max_digits=8, decimal_places=2, default=60)
    delivery_fee_outside_dhaka = models.DecimalField(max_digits=8, decimal_places=2, default=120)
    delivery_time_inside_dhaka = models.CharField(max_length=80, blank=True)
    delivery_time_outside_dhaka = models.CharField(max_length=80, blank=True)

    # SET-04 SEO defaults
    seo_title = models.CharField(max_length=120, blank=True)
    seo_description = models.CharField(max_length=300, blank=True)
    seo_share_image = models.ForeignKey("MediaAsset", null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="+")

    # SET-05 order rules
    stale_draft_hours = models.PositiveSmallIntegerField(default=24)
    low_stock_threshold = models.PositiveSmallIntegerField(default=5)
    order_number_prefix = models.CharField(max_length=10, default="SLL")

    # SET-09 analytics (empty = disabled)
    gtm_id = models.CharField(max_length=20, blank=True)

    # ACC-02 SMS provider credentials. Env wins if set; never returned by any API (write-only).
    mimsms_api_key = models.CharField(max_length=200, blank=True)
    mimsms_sender_id = models.CharField(max_length=40, blank=True)

    class Meta:
        verbose_name_plural = "site settings"


class MediaKind(models.TextChoices):
    PRODUCT = "product"
    CAROUSEL = "carousel"
    BANNER = "banner"
    CATEGORY = "category"
    LOGO = "logo"
    VIDEO_THUMB = "video_thumb"
    PAGE = "page"


class MediaAsset(TimeStampedModel):
    """SRS §6 core_mediaasset; WEB-09 / SD-02: the original is stored, display sizes are
    generated at request time by Nuxt Image. Alt text is required."""

    file = models.ImageField(upload_to="uploads/%Y/%m/")
    kind = models.CharField(max_length=12, choices=MediaKind.choices)
    alt_text = models.CharField(max_length=200)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")


class LinkType(models.TextChoices):
    CATEGORY = "category"
    PRODUCT = "product"
    URL = "url"


class CarouselSlide(TimeStampedModel):
    """SRS WEB-01 / §6 core_carouselslide: 1–8 active slides, no scheduling (SD-17)."""

    media = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name="+")
    mobile_media = models.ForeignKey(MediaAsset, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    eyebrow = models.CharField(max_length=80, blank=True)
    heading = models.CharField(max_length=120, blank=True)
    subtext = models.CharField(max_length=240, blank=True)
    button_label = models.CharField(max_length=40, blank=True)
    link_type = models.CharField(max_length=10, choices=LinkType.choices, default=LinkType.URL)
    link_value = models.CharField(max_length=300, blank=True)
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort", "id"]


class Banner(TimeStampedModel):
    """SRS WEB-02: exactly two fixed placements; other placements are not editable in v1."""

    PLACEMENTS = [("home_promo", "Home promo"), ("home_offer", "Home offer")]
    placement = models.CharField(max_length=12, choices=PLACEMENTS, unique=True)
    media = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    heading = models.CharField(max_length=120, blank=True)
    text = models.CharField(max_length=240, blank=True)
    link = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=False)


class Video(TimeStampedModel):
    """SRS WEB-04: video gallery item (YouTube/Facebook/direct URL)."""

    title = models.CharField(max_length=120)
    url = models.URLField()
    thumbnail = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort", "id"]


class Announcement(TimeStampedModel):
    """SRS WEB-05: announcement-bar messages."""

    text = models.CharField(max_length=200)
    link = models.CharField(max_length=300, blank=True)
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort", "id"]


class TrustItem(TimeStampedModel):
    """SRS WEB-08: trust strip item."""

    icon = models.CharField(max_length=40, blank=True)
    title = models.CharField(max_length=80)
    text = models.CharField(max_length=200, blank=True)
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort", "id"]


class Page(TimeStampedModel):
    """SRS WEB-07: static rich-text page (About, Delivery & Returns, Privacy, Terms)."""

    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=120)
    body = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)


class ThemePalette(TimeStampedModel):
    """SRS THM-18/THM-19 core_themepalette: 12 built-in rows (seeded) + up to 5 custom."""

    name = models.CharField(max_length=40)
    primary_hex = models.CharField(max_length=7)
    accent_hex = models.CharField(max_length=7)
    is_builtin = models.BooleanField(default=False)
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["sort", "id"]


class ThemeSettings(SingletonModel):
    """SRS THM-01 core_themesettings: the single source of truth for brand colours.
    `palette` null means custom colours. `tokens` is derived on the server (THM-14)."""

    palette = models.ForeignKey(ThemePalette, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    primary_hex = models.CharField(max_length=7, default="#231E1C")
    accent_hex = models.CharField(max_length=7, blank=True, default="#8C5550")  # "" = none
    tokens = models.JSONField(default=dict)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")


class AuditLog(TimeStampedModel):
    """SRS ADM-10 / §6 core_auditlog: who changed what and when. Append-only."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                             related_name="+")
    action = models.CharField(max_length=40)
    model = models.CharField(max_length=60)
    object_id = models.CharField(max_length=40, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip_masked = models.CharField(max_length=45, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
