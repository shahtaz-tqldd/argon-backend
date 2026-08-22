from django.db import models


class ChatbotRoleTypes(models.TextChoices):
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"


class ChatbotPermissionTypes(models.TextChoices):
    CHAT_SESSION_MANAGEMENT = (
        "chat_session_management",
        "Chat session/Inbox Management",
    )
    LEAD_MANAGEMENT = "lead_management", "Lead Management"
    APPOINTMENT_MANAGEMENT = "appointment_management", "Appointment Management"
    SETUP_CONFIGURATION = "setup_configuration", "Setup and Configuration"


class ChatbotStatusTypes(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"
    DISABLED_BY_ADMIN = "disabled_by_admin", "Disabled by Admin"


class ChatbotWidgetLauncherPositionTypes(models.TextChoices):
    BOTTOM_LEFT = "bottom_left", "Bottom left"
    BOTTOM_RIGHT = "bottom_right", "Bottom right"


class ChatbotWidgetThemeTypes(models.TextChoices):
    LIGHT = "light", "Light"
    DARK = "dark", "Dark"
    SYSTEM = "system", "System"
