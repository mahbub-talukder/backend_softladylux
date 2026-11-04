from django.urls import include, path
from rest_framework.routers import SimpleRouter

from apps.inventory import views_admin as inv

from . import views_admin as v

router = SimpleRouter()
router.register("orders", v.OrderViewSet, basename="admin-order")
router.register("coupons", v.CouponViewSet, basename="admin-coupon")

# SRS §7 admin list: orders/ (+ transition, payment verify|reject, bulk-transition), inventory/
# (list, adjust/, movements/), coupons/.
urlpatterns = [
    path("inventory/", inv.StockListView.as_view(), name="admin-inventory"),
    path("inventory/adjust/", inv.AdjustView.as_view(), name="admin-inventory-adjust"),
    path("inventory/movements/", inv.MovementListView.as_view(), name="admin-inventory-movements"),
    path("inventory/export/", inv.StockExportView.as_view(), name="admin-inventory-export"),
    path("", include(router.urls)),
]
