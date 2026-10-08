from django.contrib import admin

from workspace.models import Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "industry", "is_active", "created_at")
    list_filter = ("industry", "is_active", "created_at")
    search_fields = ("name", "slug", "owner__email", "owner__name")
    autocomplete_fields = ("owner",)
    readonly_fields = ("id", "created_at", "updated_at")
