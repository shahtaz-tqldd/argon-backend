import re

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Case, F, IntegerField, Value, When
from django.db.models.lookups import Exact

from lead_capture.utils.choices import LeadCaptureFieldMode


MAX_CAPTURE_FIELDS = 10
CUSTOM_FIELD_LABEL_MAX_LENGTH = 100
CUSTOM_FIELD_VALUE_MAX_LENGTH = 64
RESERVED_LEAD_FIELD_VALUES = {"name", "email", "phone", "address"}
CUSTOM_FIELD_VALUE_PATTERN = re.compile(
    r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
)


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

    for index, field in enumerate(value):
        field_number = index + 1
        if not isinstance(field, dict):
            raise ValidationError(
                f"Custom field {field_number} must be an object."
            )
        if set(field) != expected_keys:
            raise ValidationError(
                f"Custom field {field_number} must contain exactly label, "
                "value, and mode."
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
            raise ValidationError(
                f"Custom field {field_number} has an invalid label."
            )
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
                f"Custom field {field_number} value must be a snake_case key."
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
                f"Custom field {field_number} mode must be optional or required."
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
