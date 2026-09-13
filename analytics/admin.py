from django.contrib import admin
from analytics.models import AIUsage

@admin.register(AIUsage)
class AIUsageAdmin(admin.ModelAdmin):
    list_display = (
        "chatbot__chatbot_name",
        "cost",
        "tokens",    
        "created_at",
    )
    
    # 1. Prevent adding new records
    def has_add_permission(self, request):
        return False

    # 2. Prevent modifying existing records
    def has_change_permission(self, request, obj=None):
        return False

    # 3. Prevent deleting records
    def has_delete_permission(self, request, obj=None):
        return False