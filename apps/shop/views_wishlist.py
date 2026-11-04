"""Wishlist. SRS ACC-07, §7 `GET/POST/DELETE me/wishlist/`: stored in the database for signed-in
customers; the browser's guest wishlist is merged by the frontend after login."""

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.views_customer import IsCustomer
from apps.catalog import public
from apps.catalog.models import Product

from .models import WishlistItem


def _cards(user):
    items = list(WishlistItem.objects.filter(user=user).values_list("product_id", flat=True))
    by_id = {p.id: p for p in public.products_qs().filter(pk__in=items)}
    return [public.card(by_id[i]) for i in items if i in by_id]  # newest first, active only


class WishlistView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request):
        return Response(_cards(request.user))

    def post(self, request):
        """Add one product, or merge a list (`products`: ids) from the guest wishlist."""
        ids = request.data.get("products")
        if ids is None:
            ids = [request.data.get("product")]
        if not isinstance(ids, list) or len(ids) > 200:
            raise serializers.ValidationError({"product": "Send a product id or a list of ids."})
        valid = public.products_qs().filter(pk__in=[i for i in ids if isinstance(i, int)])
        with transaction.atomic():
            for p in valid:
                WishlistItem.objects.get_or_create(user=request.user, product=p)
        if not valid:
            raise serializers.ValidationError({"product": "Product not found."})
        return Response(_cards(request.user), status=status.HTTP_201_CREATED)

    def delete(self, request):
        product = get_object_or_404(Product, pk=request.data.get("product"))
        WishlistItem.objects.filter(user=request.user, product=product).delete()
        return Response(_cards(request.user))
