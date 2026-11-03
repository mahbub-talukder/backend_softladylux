from django.urls import include, path
from rest_framework.routers import SimpleRouter

from . import views_admin as v

router = SimpleRouter()
router.register("categories", v.CategoryViewSet, basename="admin-category")
router.register("products", v.ProductViewSet, basename="admin-product")
router.register("size-charts", v.SizeChartViewSet, basename="admin-size-chart")

variants = v.VariantViewSet.as_view({"get": "list", "post": "create"})
variant = v.VariantViewSet.as_view({"patch": "partial_update"})
images = v.ImageViewSet.as_view({"get": "list", "post": "create"})
image = v.ImageViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
reorder = v.ImageViewSet.as_view({"post": "reorder"})

# SRS §7 admin list: products/{id}/variants/, products/{id}/images/.
urlpatterns = [
    path("products/<int:product_pk>/variants/", variants, name="admin-variants"),
    path("products/<int:product_pk>/variants/<int:pk>/", variant, name="admin-variant"),
    path("products/<int:product_pk>/images/", images, name="admin-images"),
    path("products/<int:product_pk>/images/reorder/", reorder, name="admin-images-reorder"),
    path("products/<int:product_pk>/images/<int:pk>/", image, name="admin-image"),
    path("", include(router.urls)),
]
