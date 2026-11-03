"""Admin serializers. SRS ADM-03, CAT-02..04, CAT-17..19, CAT-21, §10."""

from django.db import transaction
from django.db.models import Max, Sum
from rest_framework import serializers

from apps.core.serializers import MediaRefField

from . import services
from .models import Category, Product, ProductImage, SizeChart, SubCategory, Variant


class SubCategorySerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)
    slug = serializers.SlugField(required=False, allow_blank=True)
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = SubCategory
        fields = ["id", "name", "slug", "sort", "is_active", "product_count"]
        # The model-level unique-together checks are replaced by the category-level validation
        # below, which also knows about rows being renamed in the same save.
        validators: list = []

    def get_product_count(self, obj):
        return obj.products.count()

    def validate_name(self, v):
        v = v.strip()
        if not 2 <= len(v) <= 40:
            raise serializers.ValidationError("Name must be 2–40 characters.")  # CAT-17
        return v


class CategorySerializer(serializers.ModelSerializer):
    """CAT-18: one save creates, renames, reorders or deactivates the sub-categories."""

    tile_media = MediaRefField(allow_null=True, required=False)
    subcategories = SubCategorySerializer(many=True, required=False)
    slug = serializers.SlugField(required=False, allow_blank=True)
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        exclude = ["created_at", "updated_at"]

    def get_product_count(self, obj):
        return obj.products.count()

    def validate_subcategories(self, rows):
        seen = set()
        for r in rows:
            key = r["name"].lower()
            if key in seen:
                raise serializers.ValidationError(f"“{r['name']}” is listed twice.")  # CAT-17
            seen.add(key)
        return rows

    @transaction.atomic
    def _save_subs(self, category, rows):
        kept = []
        existing = {s.id: s for s in category.subcategories.all()}
        for r in rows:
            sub = existing.get(r.get("id"))
            slug = r.get("slug") or (sub.slug if sub else "")  # CAT-21: rename keeps the slug
            if not slug:
                slug = services.unique_slug(SubCategory, r["name"], category=category)
            elif SubCategory.objects.filter(category=category, slug=slug).exclude(
                    pk=sub.pk if sub else None).exists():
                raise serializers.ValidationError({"subcategories": f"Slug “{slug}” is taken."})
            fields = {"name": r["name"], "slug": slug, "sort": r.get("sort", 0),
                      "is_active": r.get("is_active", True)}
            if sub:
                for k, v in fields.items():
                    setattr(sub, k, v)
                sub.save()
            else:
                sub = SubCategory.objects.create(category=category, **fields)
            kept.append(sub.id)
        for sub in existing.values():  # rows removed from the form
            if sub.id not in kept:
                services.delete_subcategory_allowed(sub)  # CAT-21 (400 if it has products)
                sub.delete()
        # drop the stale prefetched list (it still holds the deleted rows)
        getattr(category, "_prefetched_objects_cache", {}).pop("subcategories", None)

    def create(self, validated):
        rows = validated.pop("subcategories", [])
        if "sort" not in validated:  # new categories go last
            validated["sort"] = (Category.objects.aggregate(m=Max("sort"))["m"] or 0) + 1
        validated["slug"] = validated.get("slug") or services.unique_slug(
            Category, validated["name"])
        with transaction.atomic():
            cat = super().create(validated)
            self._save_subs(cat, rows)
        return cat

    def update(self, instance, validated):
        rows = validated.pop("subcategories", None)
        with transaction.atomic():
            cat = super().update(instance, validated)
            if rows is not None:
                self._save_subs(cat, rows)
        return cat


class VariantSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)
    sku = serializers.CharField(required=False, allow_blank=True, max_length=40)

    class Meta:
        model = Variant
        fields = ["id", "size_label", "color_name", "color_hex", "sku", "price_override",
                  "cost_price", "on_hand", "is_active"]
        extra_kwargs = {"on_hand": {"required": False}}

    def validate_sku(self, v):
        v = v.strip().upper()
        if v and not Variant.sku.field.validators[0].regex.match(v):
            raise serializers.ValidationError("SKU: capital letters, digits and dashes only.")
        return v

    def validate_color_hex(self, v):
        from apps.core.theme import HEX_RE

        if v and not HEX_RE.match(v):
            raise serializers.ValidationError("Colour must be a hex value like #RRGGBB.")
        return v.upper()


class ImageSerializer(serializers.ModelSerializer):
    media = MediaRefField(read_only=True)

    class Meta:
        model = ProductImage
        fields = ["id", "media", "color", "is_primary", "sort"]


class ProductSerializer(serializers.ModelSerializer):
    """ADM-03 product form. `variants` is a full sync: ids update, new rows are created (opening
    stock logged), rows left out are deactivated, never deleted (orders may reference them)."""

    slug = serializers.SlugField(required=False, allow_blank=True, max_length=180)
    variants = VariantSerializer(many=True, required=False)
    images = ImageSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    subcategory_name = serializers.CharField(source="subcategory.name", read_only=True,
                                             default=None)
    discount_pct = serializers.IntegerField(read_only=True)
    stock = serializers.SerializerMethodField()
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        exclude = ["created_at", "updated_at"]

    def get_stock(self, obj):
        return obj.variants.filter(is_active=True).aggregate(n=Sum("on_hand"))["n"] or 0

    def get_primary_image(self, obj):
        img = next(iter(obj.images.all()), None)
        return img.media.file.url if img else None

    def validate(self, data):
        inst = self.instance
        price = data.get("price", inst.price if inst else None)
        compare = data["compare_at_price"] if "compare_at_price" in data else (
            inst.compare_at_price if inst else None)
        if price is not None and price < 0:
            raise serializers.ValidationError({"price": "Price cannot be negative."})
        if compare is not None and price is not None and compare <= price:
            raise serializers.ValidationError(
                {"compare_at_price": "Compare-at price must be higher than the price."})
        category = data.get("category", inst.category if inst else None)
        if "subcategory" in data or "category" in data:
            sub = data.get("subcategory") if "subcategory" in data else (
                inst.subcategory if inst and "category" not in data else None)  # CAT-19: clears
            services.check_taxonomy(category, sub)
            data["subcategory"] = sub
        if data.get("is_active") and not inst:
            raise serializers.ValidationError(
                {"is_active": "Save as a draft first, add images, then publish."})
        return data

    def _sync_variants(self, product, rows):
        user = self.context["request"].user
        existing = {v.id: v for v in product.variants.all()}
        kept = set()
        for r in rows:
            r = dict(r)
            vid = r.pop("id", None)
            if vid and vid in existing:
                v = existing[vid]
                r.pop("on_hand", None)  # stock changes only through movements
                if r.get("sku"):
                    if Variant.objects.filter(sku=r["sku"]).exclude(pk=v.pk).exists():
                        raise serializers.ValidationError({"variants": f"SKU {r['sku']} is taken."})
                else:
                    r.pop("sku", None)
                for k, val in r.items():
                    setattr(v, k, val)
                v.save()
                kept.add(v.id)
            else:
                if r.get("sku") and Variant.objects.filter(sku=r["sku"].upper()).exists():
                    raise serializers.ValidationError({"variants": f"SKU {r['sku']} is taken."})
                kept.add(services.create_variant(product, r, user).id)
        for v in existing.values():
            if v.id not in kept and v.is_active:
                v.is_active = False
                v.save(update_fields=["is_active", "updated_at"])

    @transaction.atomic
    def create(self, validated):
        rows = validated.pop("variants", None)
        validated["slug"] = validated.get("slug") or services.unique_slug(
            Product, validated["name"])
        product = super().create(validated)
        self._sync_variants(product, rows or [{}])  # CAT-03: default variant when none given
        return product

    @transaction.atomic
    def update(self, instance, validated):
        rows = validated.pop("variants", None)
        if not validated.get("slug"):
            validated.pop("slug", None)
        product = super().update(instance, validated)
        if rows is not None:
            self._sync_variants(product, rows)
        if product.is_active:
            services.check_publishable(product)  # CAT-04
        return product

    def validate_slug(self, v):
        qs = Product.objects.filter(slug=v)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if v and qs.exists():
            raise serializers.ValidationError("This slug is already used.")
        return v


class ProductRowSerializer(serializers.ModelSerializer):
    """ADM-03 table row: image, name, category, price, stock, status."""

    category_name = serializers.CharField(source="category.name", read_only=True)
    subcategory_name = serializers.CharField(source="subcategory.name", read_only=True,
                                             default=None)
    stock = serializers.IntegerField(read_only=True)
    image = serializers.SerializerMethodField()
    discount_pct = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = ["id", "name", "slug", "sku_prefix", "category", "category_name", "subcategory",
                  "subcategory_name", "uom", "price", "compare_at_price", "discount_pct", "stock",
                  "is_active", "is_new", "is_signature", "is_offer", "image"]

    def get_image(self, obj):
        img = next(iter(obj.images.all()), None)
        return img.media.file.url if img else None


class SizeChartSerializer(serializers.ModelSerializer):
    class Meta:
        model = SizeChart
        fields = ["id", "name", "type", "rows"]

    def validate_rows(self, v):
        if not isinstance(v, dict):
            raise serializers.ValidationError("rows must be an object.")
        return v
