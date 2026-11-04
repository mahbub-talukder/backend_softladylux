from django.urls import path

from . import views as v

# SRS §7 admin list: dashboard/, reports/*, insights/*.
urlpatterns = [
    path("dashboard/", v.DashboardView.as_view(), name="admin-dashboard"),
    path("reports/sales/", v.report("sales").as_view(), name="report-sales"),
    path("reports/funnel/", v.report("funnel").as_view(), name="report-funnel"),
    path("reports/inventory/", v.report("inventory").as_view(), name="report-inventory"),
    path("reports/coupons/", v.report("coupons").as_view(), name="report-coupons"),
    path("insights/wishlist/", v.WishlistInsightView.as_view(), name="insights-wishlist"),
    path("insights/wishlist/<int:product_id>/", v.WishlistSaversView.as_view(),
         name="insights-wishlist-savers"),
]
