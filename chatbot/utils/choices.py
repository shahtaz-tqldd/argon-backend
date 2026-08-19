from django.db import models

class ChatbotRoleTypes(models.TextChoices):
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"

class ChatbotStatusTypes(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"
    DISABLED_BY_ADMIN = "disabled_by_admin", "Disabled by Admin"
