from django.contrib import admin

from workspace.models import Workspace, WorkspaceInvitation, WorkspaceUser


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "industry", "is_active", "created_at")
    list_filter = ("industry", "is_active", "created_at")
    search_fields = ("name", "slug", "owner__email", "owner__name")
    autocomplete_fields = ("owner",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(WorkspaceUser)
class WorkspaceUserAdmin(admin.ModelAdmin):
    list_display = ("workspace", "user", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "created_at")
    search_fields = ("workspace__name", "user__email", "user__name")
    autocomplete_fields = ("workspace", "user")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(WorkspaceInvitation)
class WorkspaceInvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "workspace", "expires_at", "accepted_at", "created_at")
    list_filter = ("accepted_at", "expires_at", "created_at")
    search_fields = ("email", "workspace__name", "workspace__slug")
    autocomplete_fields = ("workspace", "created_by")
    readonly_fields = (
        "id",
        "token_hash",
        "accepted_at",
        "created_at",
        "updated_at",
    )
