from django.core.exceptions import ValidationError

from appointment_booking.utils.choices import (
    AppointmentFieldMode,
    AppointmentFieldType,
)


COLLECTABLE_FIELD_LABEL_MAX_LENGTH = 100
COLLECTABLE_FIELD_TYPES = {
    "name": AppointmentFieldType.TEXT.value,
    "email": AppointmentFieldType.EMAIL.value,
    "phone": AppointmentFieldType.TEXT.value,
    
}


def default_collectable_fields():
    return [
        {
            "label": "Name",
            "value": "name",
            "mode": AppointmentFieldMode.REQUIRED.value,
            "type": AppointmentFieldType.TEXT.value,
        },
        {
            "label": "Email",
            "value": "email",
            "mode": AppointmentFieldMode.REQUIRED.value,
            "type": AppointmentFieldType.EMAIL.value,
        },
        {
            "label": "Phone",
            "value": "phone",
            "mode": AppointmentFieldMode.OPTIONAL.value,
            "type": AppointmentFieldType.TEXT.value,
        },
    ]


def validate_collectable_fields(value):
    if not isinstance(value, list):
        raise ValidationError("Collectable fields must be a list.")
    if len(value) != len(COLLECTABLE_FIELD_TYPES):
        raise ValidationError(
            "Collectable fields must contain name, email, phone, date, and slot."
        )

    seen_labels = set()
    seen_values = set()
    expected_keys = {"label", "value", "mode", "type"}

    for index, field in enumerate(value, start=1):
        if not isinstance(field, dict):
            raise ValidationError(f"Collectable field {index} must be an object.")
        if set(field) != expected_keys:
            raise ValidationError(
                f"Collectable field {index} must contain exactly label, value, "
                "mode, and type."
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
                f"Collectable field {index} has an invalid label."
            )
        normalized_label = label.casefold()
        if normalized_label in seen_labels:
            raise ValidationError("Collectable field labels must be unique.")
        seen_labels.add(normalized_label)

        if field_value not in COLLECTABLE_FIELD_TYPES:
            raise ValidationError(
                "Collectable field values must be name, email, phone, date, "
                "or slot."
            )
        if field_value in seen_values:
            raise ValidationError("Collectable field values must be unique.")
        seen_values.add(field_value)

        if mode not in AppointmentFieldMode.values:
            raise ValidationError(
                f"Collectable field {index} has an invalid mode."
            )
        expected_type = COLLECTABLE_FIELD_TYPES[field_value]
        if field_type != expected_type:
            raise ValidationError(
                f"Collectable field '{field_value}' must use type "
                f"'{expected_type}'."
            )

    if seen_values != set(COLLECTABLE_FIELD_TYPES):
        raise ValidationError(
            "Collectable fields must contain name, email, phone, date, and slot."
        )


def validate_collected_fields(value):
    if not isinstance(value, dict):
        raise ValidationError("Collected fields must be an object.")
    if not all(isinstance(key, str) for key in value):
        raise ValidationError("Collected field keys must be strings.")


def validate_appointment_metadata(value):
    if not isinstance(value, dict):
        raise ValidationError("Appointment metadata must be an object.")
