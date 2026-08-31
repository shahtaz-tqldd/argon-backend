from django.core.exceptions import ValidationError


def validate_json_object(value):
    if not isinstance(value, dict):
        raise ValidationError("This value must be a JSON object.")
