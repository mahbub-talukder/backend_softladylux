from django.urls import path

from . import views_public as v

urlpatterns = [
    path("categories/", v.CategoryListView.as_view(), name="public-categories"),
    path("products/", v.ProductListView.as_view(), name="public-products"),
    path("products/<slug:slug>/", v.ProductDetailView.as_view(), name="public-product"),
    path("search/suggest/", v.SuggestView.as_view(), name="public-suggest"),
    path("size-charts/", v.SizeChartListView.as_view(), name="public-size-charts"),
]
