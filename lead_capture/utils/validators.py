import re

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Case, F, IntegerField, Value, When
from django.db.models.lookups import Exact

from lead_capture.utils.choices import (
    LeadCaptureFieldMode,
    LeadCaptureFieldType,
)


MAX_CAPTURE_FIELDS = 10
COLLECTABLE_FIELD_LABEL_MAX_LENGTH = 100
COLLECTABLE_FIELD_VALUE_MAX_LENGTH = 64
PREDEFINED_LEAD_FIELD_TYPES = {
    "name": LeadCaptureFieldType.TEXT.value,
    "email": LeadCaptureFieldType.EMAIL.value,
    "phone": LeadCaptureFieldType.TEXT.value,
    "address": LeadCaptureFieldType.TEXT.value,
}
COLLECTABLE_FIELD_VALUE_PATTERN = re.compile(
    r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
)
# Legacy names remain because migration 0001 serializes these validators.
CUSTOM_FIELD_LABEL_MAX_LENGTH = COLLECTABLE_FIELD_LABEL_MAX_LENGTH
CUSTOM_FIELD_VALUE_MAX_LENGTH = COLLECTABLE_FIELD_VALUE_MAX_LENGTH
CUSTOM_FIELD_VALUE_PATTERN = COLLECTABLE_FIELD_VALUE_PATTERN
RESERVED_LEAD_FIELD_VALUES = set(PREDEFINED_LEAD_FIELD_TYPES)


def default_collectable_fields():
    return [
        {
            "label": "Name",
            "value": "name",
            "mode": LeadCaptureFieldMode.REQUIRED.value,
            "type": LeadCaptureFieldType.TEXT.value,
        },
        {
            "label": "Email",
            "value": "email",
            "mode": LeadCaptureFieldMode.REQUIRED.value,
            "type": LeadCaptureFieldType.EMAIL.value,
        },
        {
            "label": "Phone",
            "value": "phone",
            "mode": LeadCaptureFieldMode.OPTIONAL.value,
            "type": LeadCaptureFieldType.TEXT.value,
        },
        {
            "label": "Address",
            "value": "address",
            "mode": LeadCaptureFieldMode.HIDDEN.value,
            "type": LeadCaptureFieldType.TEXT.value,
        },
    ]


def validate_collectable_fields(value):
    if not isinstance(value, list):
        raise ValidationError("Collectable fields must be a list.")
    visible_field_count = sum(
        isinstance(field, dict)
        and field.get("mode") != LeadCaptureFieldMode.HIDDEN
        for field in value
    )
    if visible_field_count > MAX_CAPTURE_FIELDS:
        raise ValidationError(
            f"No more than {MAX_CAPTURE_FIELDS} optional and required fields "
            "are allowed."
        )

    seen_labels = set()
    seen_values = set()
    expected_keys = {"label", "value", "mode", "type"}

    for index, field in enumerate(value):
        field_number = index + 1
        if not isinstance(field, dict):
            raise ValidationError(
                f"Collectable field {field_number} must be an object."
            )
        if set(field) != expected_keys:
            raise ValidationError(
                f"Collectable field {field_number} must contain exactly label, "
                "value, mode, and type."
            )

        label = field["label"]
        field_value = field["value"]
        mode = field["mode"]
        field_type = field["type"]

        if (
            not isinstance(label, str)
            or not label.strip()
            or label != label.strip()
            or len(label) > COLLECTABLE_FIELD_LABEL_MAX_LENGTH
        ):
            raise ValidationError(
                f"Collectable field {field_number} has an invalid label."
            )
        normalized_label = label.casefold()
        if normalized_label in seen_labels:
            raise ValidationError("Collectable field labels must be unique.")
        seen_labels.add(normalized_label)

        if (
            not isinstance(field_value, str)
            or len(field_value) > COLLECTABLE_FIELD_VALUE_MAX_LENGTH
            or not COLLECTABLE_FIELD_VALUE_PATTERN.fullmatch(field_value)
        ):
            raise ValidationError(
                f"Collectable field {field_number} value must be a snake_case key."
            )
        if field_value in seen_values:
            raise ValidationError("Collectable field values must be unique.")
        seen_values.add(field_value)

        if mode not in LeadCaptureFieldMode.values:
            raise ValidationError(
                f"Collectable field {field_number} has an invalid mode."
            )
        if field_type not in LeadCaptureFieldType.values:
            raise ValidationError(
                f"Collectable field {field_number} has an invalid type."
            )
        expected_type = PREDEFINED_LEAD_FIELD_TYPES.get(field_value)
        if expected_type is not None and field_type != expected_type:
            raise ValidationError(
                f"Collectable field '{field_value}' must use type "
                f"'{expected_type}'."
            )


def validate_collected_fields(value):
    if not isinstance(value, dict):
        raise ValidationError("Collected fields must be an object.")
    for field_value in value:
        if (
            not isinstance(field_value, str)
            or len(field_value) > COLLECTABLE_FIELD_VALUE_MAX_LENGTH
            or not COLLECTABLE_FIELD_VALUE_PATTERN.fullmatch(field_value)
        ):
            raise ValidationError(
                "Collected field keys must be valid snake_case values."
            )


# Kept for historical migrations. New code uses the consolidated validators.
def validate_custom_field_config(value):
    if not isinstance(value, list):
        raise ValidationError("Custom fields must be a list.")
    if len(value) > MAX_CAPTURE_FIELDS:
        raise ValidationError(
            f"No more than {MAX_CAPTURE_FIELDS} custom fields are allowed."
        )

    seen_labels = set()
    seen_values = set()
    expected_keys = {"label", "value", "mode"}
    for index, field in enumerate(value, start=1):
        if not isinstance(field, dict):
            raise ValidationError(f"Custom field {index} must be an object.")
        if set(field) != expected_keys:
            raise ValidationError(
                f"Custom field {index} must contain exactly label, value, "
                "and mode."
            )
        label = field["label"]
        field_value = field["value"]
        mode = field["mode"]
        if (
            not isinstance(label, str)
            or not label.strip()
            or label != label.strip()
            or len(label) > CUSTOM_FIELD_LABEL_MAX_LENGTH
        ):
            raise ValidationError(f"Custom field {index} has an invalid label.")
        normalized_label = label.casefold()
        if normalized_label in seen_labels:
            raise ValidationError("Custom field labels must be unique.")
        seen_labels.add(normalized_label)
        if (
            not isinstance(field_value, str)
            or len(field_value) > CUSTOM_FIELD_VALUE_MAX_LENGTH
            or not CUSTOM_FIELD_VALUE_PATTERN.fullmatch(field_value)
        ):
            raise ValidationError(
                f"Custom field {index} value must be a snake_case key."
            )
        if field_value in RESERVED_LEAD_FIELD_VALUES:
            raise ValidationError(
                f"'{field_value}' is reserved for a built-in lead field."
            )
        if field_value in seen_values:
            raise ValidationError("Custom field values must be unique.")
        seen_values.add(field_value)
        if mode not in {
            LeadCaptureFieldMode.OPTIONAL,
            LeadCaptureFieldMode.REQUIRED,
        }:
            raise ValidationError(
                f"Custom field {index} mode must be optional or required."
            )


def validate_lead_custom_fields(value):
    if not isinstance(value, dict):
        raise ValidationError("Captured custom fields must be an object.")
    for field_value in value:
        if (
            not isinstance(field_value, str)
            or len(field_value) > CUSTOM_FIELD_VALUE_MAX_LENGTH
            or not CUSTOM_FIELD_VALUE_PATTERN.fullmatch(field_value)
        ):
            raise ValidationError(
                "Captured custom field keys must be valid snake_case values."
            )
        if field_value in RESERVED_LEAD_FIELD_VALUES:
            raise ValidationError(
                f"'{field_value}' is reserved for a built-in lead field."
            )


# model validation for field limitation
# Kept for historical migrations.
def custom_field_count_expression():
    return models.Func(
        F("custom_fields"),
        function="jsonb_array_length",
        output_field=IntegerField(),
    )


def _selected_mode_count_expression(field_name):
    return Case(
        When(**{field_name: LeadCaptureFieldMode.HIDDEN}, then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )


def total_capture_field_count_expression():
    return (
        custom_field_count_expression()
        + _selected_mode_count_expression("name_mode")
        + _selected_mode_count_expression("email_mode")
        + _selected_mode_count_expression("phone_mode")
        + _selected_mode_count_expression("address_mode")
    )


def json_type_expression(field_name, expected_type):
    return Exact(
        models.Func(
            F(field_name),
            function="jsonb_typeof",
            output_field=models.CharField(),
        ),
        Value(expected_type),
    )
