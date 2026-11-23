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


HOME_SECTIONS = (
    ("carousel", "Hero carousel"), ("trust", "Trust strip"), ("categories", "Category tiles"),
    ("banners", "Promo banners"), ("new_arrivals", "New arrivals"),
    ("signature", "Signature collection"), ("offers", "Offers and favourites"),
    ("videos", "Video gallery"))


class SiteSettings(SingletonModel):
    """SRS §6 core_sitesettings; SET-01..05, SET-09. Every company-specific value on the
    storefront comes from here (SET-08). Placeholders are shown until the owner fills them."""

    # WEB-06: which home sections show. Keys missing = on. Read once with the cached home payload,
    # and a section that is off is not queried at all, so a lean home page loads faster.
    home_sections = models.JSONField(default=dict, blank=True)

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
    # CHK-04 / SET (v1.11): static Bank payment method; hidden while the account number is empty.
    bank_name = models.CharField(max_length=80, blank=True)
    bank_account_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=40, blank=True)
    bank_branch = models.CharField(max_length=80, blank=True)
    bank_routing_number = models.CharField(max_length=20, blank=True)
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

    # ACC-02 / NTF-02 (amendment v1.6): MiMSMS JSON API, configured only here (no env, no fake
    # sender). The API key is shown only to settings.manage (v1.8) and never logged or audited.
    mimsms_api_key = models.CharField(max_length=200, blank=True)
    mimsms_username = models.CharField(max_length=120, blank=True)
    mimsms_sender_name = models.CharField(max_length=40, blank=True)  # numeric sender, e.g. 8809…
    mimsms_url = models.URLField(blank=True, default="https://api.mimsms.com/api/V2/SMS")
    sms_order_enabled = models.BooleanField(default=True)  # NTF-02: order-received SMS on/off
    # CHK-11 (v1.11): guests verify their mobile by SMS OTP before ordering. Owner can switch it
    # off (e.g. while SMS is not set up), otherwise guests could not order at all.
    checkout_otp_enabled = models.BooleanField(default=True)

    # CRR-01 (v1.7): Steadfast courier, configured only here (no env). Keys and the webhook token
    # are shown only to settings.manage (v1.8) and never logged or audited.
    steadfast_api_key = models.CharField(max_length=200, blank=True)
    steadfast_secret_key = models.CharField(max_length=200, blank=True)
    steadfast_webhook_token = models.CharField(max_length=200, blank=True)
    steadfast_base_url = models.URLField(blank=True, default="https://portal.packzy.com/api/v1")
    steadfast_auto_book = models.BooleanField(default=True)  # CRR-03
    # CRR-06: set by a 401/403 from Steadfast; every courier call stops until the courier settings
    # are saved again (Steadfast locks a client out for 60 minutes after 10 auth failures).
    steadfast_keys_rejected = models.BooleanField(default=False)

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
    """SRS WEB-02 (v1.6): four fixed placements, edit only. `signature` is the big Signature
    collection panel; `popup` is the Promotional Ads Panel (WEB-11)."""

    PLACEMENTS = [("home_promo", "Home promo"), ("home_offer", "Home offer"),
                  ("signature", "Signature collection"), ("popup", "Promotional popup")]
    HOME_ROW = ("home_promo", "home_offer")  # the two side-by-side home banners
    placement = models.CharField(max_length=20, choices=PLACEMENTS, unique=True)
    media = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    heading = models.CharField(max_length=120, blank=True)
    text = models.CharField(max_length=240, blank=True)
    button_label = models.CharField(max_length=40, blank=True)
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
    # Storefront page Background and Text (owner-approved amendment); defaults = Signature neutrals.
    background_hex = models.CharField(max_length=7, default="#F8F4EC")
    text_hex = models.CharField(max_length=7, default="#231E1C")
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
    background_hex = models.CharField(max_length=7, default="#F8F4EC")
    text_hex = models.CharField(max_length=7, default="#231E1C")
    tokens = models.JSONField(default=dict)
    # THM-21 / THM-26 (v1.9): font pairing keys from theme.FONT_PAIRINGS, one per area.
    storefront_font = models.CharField(max_length=20, default="signature")
    admin_font = models.CharField(max_length=20, default="signature")
    # THM-28 (v1.10): text size per area (compact / standard / large).
    storefront_text_size = models.CharField(max_length=10, default="standard")
    admin_text_size = models.CharField(max_length=10, default="standard")
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


class SmsLog(TimeStampedModel):
    """SRS v1.11 NTF-07 / §6 core_smslog: one row per SMS sent through the provider. The message is
    stored as sent except OTP codes, which are masked. Written by apps.core.sms.send() only."""

    class Source(models.TextChoices):
        OTP_REGISTER = "otp_register", "Registration OTP"
        OTP_RESET = "otp_reset", "Password reset OTP"
        OTP_CHECKOUT = "otp_checkout", "Checkout OTP"
        ORDER_RECEIVED = "order_received", "Order received"
        ADMIN_TEST = "admin_test", "Admin test"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"  # v1.11 owner decision D5

    receiver = models.CharField(max_length=11, db_index=True)
    message = models.TextField()
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.OTHER)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING,
                              db_index=True)
    provider_ref = models.CharField(max_length=80, blank=True)
    error = models.CharField(max_length=300, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["-created_at"])]


class ContactMessage(TimeStampedModel):
    """SRS §6 core_contactmessage, NTF-01: stored, emailed to the support email, marked handled."""

    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField()
    subject = models.CharField(max_length=150, blank=True)
    message = models.TextField(max_length=3000)
    is_handled = models.BooleanField(default=False)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]


class Subscriber(TimeStampedModel):
    """SRS §6 core_subscriber, NTF-04: newsletter sign-up (footer); export to CSV."""

    email = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-subscribed_at", "-id"]
