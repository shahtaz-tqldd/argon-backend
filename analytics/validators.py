from django.core.exceptions import ValidationError


AI_USAGE_METADATA_FIELDS = frozenset(
    {
        "model",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "input_cost",
        "output_cost",
        "cached_cost",
    }
)


def validate_ai_usage_metadata(value):
    """Ensure AI usage metadata is an object containing only supported fields."""
    if not isinstance(value, dict):
        raise ValidationError("AI usage metadata must be a JSON object.")

    unsupported_fields = sorted(
        (field for field in value if field not in AI_USAGE_METADATA_FIELDS),
        key=str,
    )
    if unsupported_fields:
        fields = ", ".join(map(str, unsupported_fields))
        raise ValidationError(f"Unsupported AI usage metadata field(s): {fields}.")
