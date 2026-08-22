from chatbot.utils.choices import ChatbotPermissionTypes, ChatbotRoleTypes


# A null feature flag means that the permission is always available. Adding a
# future permission requires one choice plus one entry here; membership rows do
# not need a schema migration because their grants are stored as permission codes.
# Lead and appointment permissions remain unavailable until their dedicated
# modules expose these feature flags on the chatbot.
CHATBOT_PERMISSION_FEATURE_FLAGS = {
    ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT: None,
    ChatbotPermissionTypes.LEAD_MANAGEMENT: "lead_capture_enabled",
    ChatbotPermissionTypes.APPOINTMENT_MANAGEMENT: (
        "appointment_booking_enabled"
    ),
    ChatbotPermissionTypes.SETUP_CONFIGURATION: None,
}


def default_chatbot_user_permissions():
    return [ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT.value]


def normalize_chatbot_permission_codes(chatbot, permissions):
    if permissions is None:
        permissions = default_chatbot_user_permissions()
    if not isinstance(permissions, list):
        raise ValueError("Permissions must be a list of permission codes.")
    if not all(isinstance(permission, str) for permission in permissions):
        raise ValueError("Every permission code must be a string.")
    if len(permissions) != len(set(permissions)):
        raise ValueError("Each permission code can only be supplied once.")

    unknown_codes = set(permissions) - set(ChatbotPermissionTypes.values)
    if unknown_codes:
        raise ValueError(
            f"Unknown permission codes: {', '.join(sorted(unknown_codes))}."
        )

    available_codes = set(available_chatbot_permission_codes(chatbot))
    unavailable_codes = set(permissions) - available_codes
    if unavailable_codes:
        raise ValueError(
            "These permissions are not available for this chatbot: "
            f"{', '.join(sorted(unavailable_codes))}."
        )

    requested_codes = set(permissions)
    return [
        code
        for code in ChatbotPermissionTypes.values
        if code in requested_codes
    ]


def available_chatbot_permission_codes(chatbot):
    available = []
    for code, _label in ChatbotPermissionTypes.choices:
        feature_flag = CHATBOT_PERMISSION_FEATURE_FLAGS[code]
        if feature_flag is None or getattr(chatbot, feature_flag, False):
            available.append(code)
    return available


def available_chatbot_permissions(chatbot):
    available_codes = set(available_chatbot_permission_codes(chatbot))
    return [
        {"code": code, "label": str(label)}
        for code, label in ChatbotPermissionTypes.choices
        if code in available_codes
    ]


def effective_chatbot_permission_codes(membership):
    available_codes = available_chatbot_permission_codes(membership.chatbot)
    if membership.role == ChatbotRoleTypes.ADMIN:
        return available_codes

    granted_codes = set(membership.permissions or [])
    return [code for code in available_codes if code in granted_codes]
