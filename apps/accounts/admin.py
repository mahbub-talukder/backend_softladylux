from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "phone", "email", "role", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("full_name", "phone", "email")
    exclude = ("password",)
