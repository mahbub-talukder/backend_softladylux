"""Catalogue models. SRS §6 catalog_*; CAT-01..CAT-22."""

from decimal import Decimal

from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import MediaAsset, TimeStampedModel

SKU_VALIDATOR = RegexValidator(r"^[A-Z0-9-]+$", "SKU: capital letters, digits and dashes only.")


class Category(TimeStampedModel):
    """CAT-01: seeded with the eight categories; Offers is a virtual view, not a row."""

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    tile_media = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    seo_title = models.CharField(max_length=120, blank=True)
    seo_description = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["sort", "id"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class SubCategory(TimeStampedModel):
    """CAT-17: belongs to exactly one category; name unique per category (case-insensitive)."""

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="subcategories")
    name = models.CharField(max_length=40)
    slug = models.SlugField()
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort", "id"]
        verbose_name_plural = "sub-categories"
        constraints = [
            models.UniqueConstraint(fields=["category", "slug"], name="uniq_sub_slug_in_cat"),
            models.UniqueConstraint("category", Lower("name"), name="uniq_sub_name_in_cat"),
        ]

    def __str__(self):
        return f"{self.category.name} / {self.name}"


class Uom(models.TextChoices):
    PIECE = "pc", "Piece"
    SET = "set", "Set"
    PAIR = "pair", "Pair"
    PACK = "pack", "Pack"


class Product(TimeStampedModel):
    """CAT-02. `is_active` False = draft. Sub-category rules (CAT-19) live in services."""

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    subcategory = models.ForeignKey(SubCategory, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="products")
    short_desc = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    care_notes = models.TextField(blank=True)
    collection = models.CharField(max_length=80, blank=True)
    uom = models.CharField(max_length=4, choices=Uom.choices, default=Uom.PIECE)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    compare_at_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sku_prefix = models.CharField(max_length=20, blank=True, validators=[SKU_VALIDATOR])
    is_active = models.BooleanField(default=False)
    is_new = models.BooleanField(default=False)
    is_signature = models.BooleanField(default=False)
    is_offer = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)
    seo_title = models.CharField(max_length=120, blank=True)
    seo_description = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.name

    @property
    def discount_pct(self) -> int:
        """CAT-05: round((compare − price) / compare × 100) when compare-at > price, else 0."""
        c = self.compare_at_price
        if c and c > self.price:
            return int(((c - self.price) / c * Decimal(100)).quantize(Decimal(1), "ROUND_HALF_UP"))
        return 0

    @property
    def on_offer(self) -> bool:
        """CAT-06."""
        return self.discount_pct > 0 or self.is_offer

    def has_orders(self) -> bool:
        """CAT-14 hook: orders arrive in M3; until then nothing has been ordered."""
        return False


class ProductImage(TimeStampedModel):
    """CAT-04: many per product, one primary, ordered, optionally tied to a colour."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    media = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name="+")
    color = models.CharField(max_length=40, blank=True)
    is_primary = models.BooleanField(default=False)
    sort = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-is_primary", "sort", "id"]


class Variant(TimeStampedModel):
    """CAT-03: every product has at least one variant (default variant when no options)."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    size_label = models.CharField(max_length=20, blank=True)
    color_name = models.CharField(max_length=40, blank=True)
    color_hex = models.CharField(max_length=7, blank=True)
    sku = models.CharField(max_length=40, unique=True, validators=[SKU_VALIDATOR])
    price_override = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    on_hand = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]

    @property
    def price(self):
        return self.price_override if self.price_override is not None else self.product.price

    def __str__(self):
        return self.sku


class SizeChart(TimeStampedModel):
    """CAT-08: admin-managed tables; the calculator reads its thresholds from `rows`."""

    TYPES = [("bra", "Bra"), ("apparel", "Apparel"), ("panty", "Panty")]
    name = models.CharField(max_length=60)
    type = models.CharField(max_length=10, choices=TYPES, unique=True)
    rows = models.JSONField(default=dict)
