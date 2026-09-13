CONFIG_SECTIONS = {
    "branding": ("title", "logo", "favicon", "support_email"),
    "legal_document": (
        "privacy_policy",
        "terms_of_service",
        "data_deletion_policy",
        "cookie_policy",
    ),
    "feature": ("is_vectorize_enabled", "maintenance_mode"),
    "announcement": (
        "notify_banner_enabled",
        "notify_banner_text",
        "notify_banner_url",
    ),
    "platform_default": ("default_free_credits", "monthly_free_credits"),
    "seo": ("meta_title", "meta_description"),
    "audit": ("created_at", "updated_at", "updated_by"),
}

LEGAL_DOCUMENT_TYPES = CONFIG_SECTIONS["legal_document"]


def serialize_config_sections(instance, sections, document_type=None):
    data = {}
    for section in sections:
        fields = CONFIG_SECTIONS[section]
        if section == "legal_document" and document_type:
            fields = (document_type,)

        values = {}
        for field in fields:
            value = getattr(instance, field)
            if field == "updated_by":
                value = str(value.pk) if value else None
            elif field in ("created_at", "updated_at"):
                value = value.isoformat() if value else None
            values[field] = value
        data[section] = values
    return data
