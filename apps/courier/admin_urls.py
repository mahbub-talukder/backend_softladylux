from django.urls import path

from . import views_admin as v

# SRS v1.7 §7, mounted at /api/v1/admin/courier/. Every view declares its permission (PRM-03).
urlpatterns = [
    path("ping/", v.PingView.as_view(), name="courier-ping"),
    path("dashboard/", v.DashboardView.as_view(), name="courier-dashboard"),
    path("parcel-states/<str:state>/", v.ParcelStateView.as_view(), name="courier-parcel-state"),
    path("orders/<int:pk>/book/", v.BookOrderView.as_view(), name="courier-book"),
    path("orders/<int:pk>/refresh/", v.RefreshOrderView.as_view(), name="courier-refresh"),
    path("orders/<int:pk>/return/", v.ReturnOrderView.as_view(), name="courier-return"),
    path("bulk-book/", v.BulkBookView.as_view(), name="courier-bulk-book"),
    path("pickups/", v.PickupView.as_view(), name="courier-pickups"),
    path("police-stations/", v.PoliceStationsView.as_view(), name="courier-police-stations"),
    path("returns/", v.ReturnListView.as_view(), name="courier-returns"),
    path("returns/<int:rid>/", v.ReturnDetailView.as_view(), name="courier-return-detail"),
    path("fraud-check/", v.FraudCheckView.as_view(), name="courier-fraud-check"),
]
