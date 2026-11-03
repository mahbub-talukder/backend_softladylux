from rest_framework.pagination import PageNumberPagination


# SRS §7 Conventions: pagination via ?page=&page_size=; ADM-07 server-side pagination.
class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
