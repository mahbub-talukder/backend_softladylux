"""Reports, dashboard and insights APIs. SRS RPT-01..07, ADM-02, INS-01/06, PRM-03.
Reports need `reports.view`; the dashboard needs `dashboard.view` and returns only the widgets for
areas the user can open; insights need `insights.view`."""

import csv

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import require, user_permissions
from apps.catalog.models import Product

from . import services

CSV_SECTIONS = {
    "sales": {
        "summary": lambda d: [{"metric": k, "value": v} for k, v in d["summary"].items()],
        "by_day": lambda d: d["by_day"], "by_category": lambda d: d["by_category"],
        "top_products": lambda d: d["top_products"],
        "by_payment_method": lambda d: d["by_payment_method"], "by_area": lambda d: d["by_area"]},
    "funnel": {"funnel": lambda d: d},
    "inventory": {"stock": lambda d: d["stock"], "low": lambda d: d["low"],
                  "out": lambda d: d["out"], "movements": lambda d: d["movements"]},
    "coupons": {"coupons": lambda d: d},
}


def csv_response(name, rows):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    w = csv.writer(resp)
    if rows:
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow(r.values())
    return resp


class ReportView(APIView):
    """One endpoint per report; `?export=csv&section=…` downloads a table as CSV (RPT-07)."""

    permission_classes = [require("reports.view")]
    kind = ""
    builders = {"sales": services.sales, "funnel": services.funnel,
                "inventory": services.inventory, "coupons": services.coupons}

    def get(self, request):
        start, end, label = services.parse_range(request.query_params)
        data = self.builders[self.kind](start, end)
        if request.query_params.get("export") == "csv":
            sections = CSV_SECTIONS[self.kind]
            section = request.query_params.get("section") or next(iter(sections))
            if section not in sections:
                raise ValidationError({"section": "Unknown section."})
            return csv_response(f"{self.kind}-{section}-{label['from']}-{label['to']}",
                                sections[section](data))
        payload = {"period": label}
        if isinstance(data, list):
            payload["rows"] = data
        else:
            payload.update(data)
        return Response(payload)


def report(kind):
    return type(f"{kind.title()}ReportView", (ReportView,), {"kind": kind})


class DashboardView(APIView):
    permission_classes = [require("dashboard.view")]

    def get(self, request):
        return Response(services.dashboard(user_permissions(request.user)))


class WishlistInsightView(APIView):
    permission_classes = [require("insights.view")]

    def get(self, request):
        return Response(services.wishlist_overview())


class WishlistSaversView(APIView):
    permission_classes = [require("insights.view")]

    def get(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id)
        return Response({"product": product.name,
                         "customers": services.wishlist_savers(product.pk)})
