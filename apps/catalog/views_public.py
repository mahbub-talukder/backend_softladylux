"""Public catalogue endpoints. SRS §7: categories/, products/, products/{slug}/, search/suggest/,
size-charts/."""

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import public
from .models import Category, SizeChart


class PublicMixin:
    authentication_classes: list = []
    permission_classes = [AllowAny]


class CategoryListView(PublicMixin, APIView):
    """CAT-01/CAT-17/CAT-20: active categories with nested active sub-categories and counts."""

    def get(self, request):
        live = Q(products__is_active=True)
        cats = (Category.objects.filter(is_active=True).select_related("tile_media")
                .prefetch_related("subcategories")
                .annotate(n=Count("products", filter=live, distinct=True)))
        out = []
        for c in cats:
            subs = []
            for s in c.subcategories.all():
                if s.is_active:
                    subs.append({"name": s.name, "slug": s.slug,
                                 "count": s.products.filter(is_active=True).count()})
            out.append({
                "name": c.name, "slug": c.slug, "description": c.description,
                "count": c.n, "subcategories": subs, "seo_title": c.seo_title,
                "seo_description": c.seo_description,
                "tile": ({"url": c.tile_media.file.url, "alt_text": c.tile_media.alt_text}
                         if c.tile_media else None),
            })
        return Response(out)


class ProductListView(PublicMixin, ListAPIView):
    """CAT-09: filters, sort, pagination, plus facets for the left filter."""

    def get_queryset(self):
        order = public.SORTS.get(self.request.query_params.get("sort", "featured"),
                                 public.SORTS["featured"])
        return public.filtered(self.request).order_by(*order)

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        resp = self.get_paginated_response([public.card(p) for p in page])
        resp.data["facets"] = public.facets(request)
        return resp


class ProductDetailView(PublicMixin, APIView):
    """CAT-11/CAT-22: detail with variants, images, breadcrumb and related products."""

    def get(self, request, slug):
        p = get_object_or_404(public.products_qs(), slug=slug)
        data = public.detail(p)
        related = (public.products_qs().filter(category=p.category).exclude(pk=p.pk)
                   .order_by("-is_signature", "-created_at")[:4])
        data["related"] = [public.card(r) for r in related]
        return Response(data)


class SuggestView(PublicMixin, APIView):
    """CAT-10: top 6 matches with image and price while typing (min 2 characters)."""

    def get(self, request):
        term = request.query_params.get("q", "").strip()
        if len(term) < 2:
            return Response([])
        qs = public.products_qs().filter(public.search_q(term)).distinct()[:6]
        return Response([{"name": c["name"], "slug": c["slug"], "price": c["price"],
                          "image": c["image"]} for c in map(public.card, qs)])


class SizeChartListView(PublicMixin, APIView):
    """CAT-08."""

    def get(self, request):
        return Response([{"name": c.name, "type": c.type, "rows": c.rows}
                         for c in SizeChart.objects.all()])

