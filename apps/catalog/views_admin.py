"""Admin catalogue APIs. SRS ADM-03, CAT-14, CAT-15, CAT-18, CAT-21, PRM-03/05 (products.view
reads, products.manage writes), audit on every write (ADM-10)."""

import csv

from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.accounts.permissions import require_by_method
from apps.core import audit, cache
from apps.core.models import MediaKind

from . import services
from .models import Category, Product, ProductImage, SizeChart, SubCategory, Variant
from .serializers import (
    CategorySerializer,
    ImageSerializer,
    ProductRowSerializer,
    ProductSerializer,
    SizeChartSerializer,
    VariantSerializer,
)

CatalogPermission = require_by_method("products.view", "products.manage")


class AuditedMixin:
    """Audit + public-cache invalidation for every write (SET-07 pattern, WEB-10)."""

    audit_name = ""

    def _snap(self, obj):
        return self.get_serializer(obj).data

    def perform_create(self, ser):
        obj = ser.save()
        audit.log(self.request, "create", self.audit_name, obj.pk, None, _slim(self._snap(obj)))
        cache.invalidate()

    def perform_update(self, ser):
        before = _slim(self._snap(ser.instance))
        obj = ser.save()
        audit.log(self.request, "update", self.audit_name, obj.pk, before, _slim(self._snap(obj)))
        cache.invalidate()


def _slim(d):
    """Keep audit rows small: drop bulky nested lists."""
    return {k: v for k, v in dict(d).items() if k not in ("images", "subcategories", "variants")}


class CategoryViewSet(AuditedMixin, viewsets.ModelViewSet):
    permission_classes = [CatalogPermission]
    serializer_class = CategorySerializer
    queryset = Category.objects.prefetch_related("subcategories").select_related("tile_media")
    pagination_class = None
    audit_name = "Category"

    def perform_destroy(self, cat):
        if cat.products.exists():  # PROTECT would 500; give the user a reason (CAT-21 spirit)
            raise serializers.ValidationError("This category has products. Deactivate it instead.")
        audit.log(self.request, "delete", "Category", cat.pk, {"name": cat.name}, None)
        cat.delete()
        cache.invalidate()


class ProductViewSet(AuditedMixin, viewsets.ModelViewSet):
    permission_classes = [CatalogPermission]
    audit_name = "Product"

    def get_queryset(self):
        qs = (Product.objects.select_related("category", "subcategory")
              .prefetch_related("images__media", "variants")
              .annotate(stock=Sum("variants__on_hand", filter=Q(variants__is_active=True))))
        q = self.request.query_params
        if t := q.get("q", "").strip():
            qs = qs.filter(Q(name__icontains=t) | Q(variants__sku__icontains=t)
                           | Q(sku_prefix__icontains=t)).distinct()
        if c := q.get("category"):
            qs = qs.filter(category_id=c)
        if s := q.get("subcategory"):
            qs = qs.filter(subcategory_id=s)
        if st := q.get("status"):
            qs = qs.filter(is_active=(st == "active"))
        return qs

    def get_serializer_class(self):
        return ProductRowSerializer if self.action == "list" else ProductSerializer

    def retrieve(self, request, *args, **kwargs):
        return Response(ProductSerializer(self.get_object(), context={"request": request}).data)

    def perform_destroy(self, p):
        """CAT-14: a product that has been ordered (or had stock) is only deactivated."""
        busy = p.has_orders() or Variant.objects.filter(product=p, movements__isnull=False).exists()
        audit.log(self.request, "deactivate" if busy else "delete", "Product", p.pk,
                  {"name": p.name}, None)
        if busy:
            p.is_active = False
            p.save(update_fields=["is_active", "updated_at"])
        else:
            p.delete()
        cache.invalidate()

    @action(detail=False, methods=["post"])
    def bulk(self, request):
        """CAT-15 / CAT-21: activate, deactivate, change category (new sub-category required when
        the category has any), set a section flag."""
        ids = request.data.get("ids") or []
        op = request.data.get("action")
        qs = Product.objects.filter(pk__in=ids)
        if not ids or not qs.exists():
            raise serializers.ValidationError({"ids": "Select at least one product."})
        if op in ("activate", "deactivate"):
            if op == "activate":
                for p in qs:
                    services.check_publishable(p)
            qs.update(is_active=(op == "activate"))
        elif op == "set_category":
            cat = get_object_or_404(Category, pk=request.data.get("category"))
            sub = None
            if request.data.get("subcategory"):
                sub = get_object_or_404(SubCategory, pk=request.data["subcategory"])
            services.check_taxonomy(cat, sub)
            qs.update(category=cat, subcategory=sub)
        elif op == "set_flag":
            flag = request.data.get("flag")
            if flag not in ("is_new", "is_signature", "is_offer"):
                raise serializers.ValidationError({"flag": "Unknown flag."})
            qs.update(**{flag: bool(request.data.get("value", True))})
        else:
            raise serializers.ValidationError({"action": "Unknown bulk action."})
        audit.log(request, f"bulk_{op}", "Product", ",".join(map(str, ids)), None,
                  {k: v for k, v in request.data.items() if k != "ids"})
        cache.invalidate()
        return Response({"updated": qs.count()})

    @action(detail=False, methods=["get"])
    def export(self, request):
        """CAT-15 / CAT-22: CSV of the current filter, including sub-category."""
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="products.csv"'
        w = csv.writer(resp)
        w.writerow(["name", "slug", "category", "sub_category", "uom", "price", "compare_at_price",
                    "stock", "status"])
        for p in self.get_queryset():
            w.writerow([p.name, p.slug, p.category.name,
                        p.subcategory.name if p.subcategory else "", p.uom, p.price,
                        p.compare_at_price or "", p.stock or 0,
                        "active" if p.is_active else "draft"])
        return resp


class VariantViewSet(AuditedMixin, mixins.ListModelMixin, mixins.CreateModelMixin,
                     mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """§7 products/{id}/variants/. Stock (`on_hand`) is read-only after creation: it changes
    only through logged movements (INV-08). Deactivate via PATCH is_active=false."""

    permission_classes = [CatalogPermission]
    serializer_class = VariantSerializer
    pagination_class = None
    audit_name = "Variant"
    http_method_names = ["get", "post", "patch", "head", "options"]

    def _product(self):
        return get_object_or_404(Product, pk=self.kwargs["product_pk"])

    def get_queryset(self):
        return Variant.objects.filter(product_id=self.kwargs["product_pk"])

    def perform_create(self, ser):
        v = services.create_variant(self._product(), dict(ser.validated_data), self.request.user)
        ser.instance = v
        audit.log(self.request, "create", "Variant", v.pk, None, {"sku": v.sku})
        cache.invalidate()

    def perform_update(self, ser):
        ser.validated_data.pop("on_hand", None)
        super().perform_update(ser)


class ImageViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin,
                   mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """§7 products/{id}/images/: multi-upload, primary, colour link, delete, reorder (CAT-04)."""

    permission_classes = [CatalogPermission]
    serializer_class = ImageSerializer
    pagination_class = None
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def _product(self):
        return get_object_or_404(Product, pk=self.kwargs["product_pk"])

    def get_queryset(self):
        return ProductImage.objects.filter(product_id=self.kwargs["product_pk"]).select_related(
            "media")

    def create(self, request, *args, **kwargs):
        from apps.core.serializers import MediaUploadSerializer

        product = self._product()
        files = request.FILES.getlist("files") or request.FILES.getlist("file")
        if not files:
            raise serializers.ValidationError({"files": "Choose at least one image."})
        alt = request.data.get("alt_text") or product.name
        made = []
        for f in files:
            ser = MediaUploadSerializer(data={"file": f, "kind": MediaKind.PRODUCT,
                                              "alt_text": alt}, context={"request": request})
            ser.is_valid(raise_exception=True)  # WEB-09 + 600 px minimum (§10)
            asset = ser.save()
            first = not product.images.exists()
            made.append(ProductImage.objects.create(
                product=product, media=asset, is_primary=first,
                sort=product.images.count()))
        audit.log(request, "images_added", "Product", product.pk, None, {"count": len(made)})
        cache.invalidate()
        return Response(ImageSerializer(made, many=True).data, status=status.HTTP_201_CREATED)

    def perform_update(self, ser):
        img = ser.save()
        if ser.validated_data.get("is_primary"):  # exactly one primary
            ProductImage.objects.filter(product=img.product).exclude(pk=img.pk).update(
                is_primary=False)
        cache.invalidate()

    def perform_destroy(self, img):
        product, was_primary = img.product, img.is_primary
        img.delete()
        if was_primary:  # promote the next image
            nxt = product.images.order_by("sort", "id").first()
            if nxt:
                nxt.is_primary = True
                nxt.save(update_fields=["is_primary", "updated_at"])
        if product.is_active and not product.images.exists():
            product.is_active = False  # CAT-04: can't stay published without an image
            product.save(update_fields=["is_active", "updated_at"])
        cache.invalidate()

    @action(detail=False, methods=["post"])
    def reorder(self, request, product_pk=None):
        ids = request.data.get("ids") or []
        own = {i.id: i for i in self.get_queryset()}
        if set(ids) != set(own):
            raise serializers.ValidationError({"ids": "Send every image id of this product."})
        for pos, iid in enumerate(ids):
            ProductImage.objects.filter(pk=iid).update(sort=pos)
        cache.invalidate()
        return Response(ImageSerializer(self.get_queryset(), many=True).data)


class SizeChartViewSet(AuditedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                       mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """CAT-08: admin-managed size charts (edit only; one per type)."""

    permission_classes = [CatalogPermission]
    serializer_class = SizeChartSerializer
    queryset = SizeChart.objects.all()
    pagination_class = None
    audit_name = "SizeChart"
    http_method_names = ["get", "patch", "head", "options"]

