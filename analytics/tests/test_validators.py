from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from analytics.validators import validate_ai_usage_metadata


class AIUsageMetadataValidatorTests(SimpleTestCase):
    def test_accepts_appointment_confirmation_context(self):
        validate_ai_usage_metadata({
            "event": "appointment_confirmation",
            "appointment_id": "6c3667b3-9fee-45a9-8976-60b8a9b20032",
        })

    def test_still_rejects_unknown_context(self):
        with self.assertRaisesRegex(
            ValidationError,
            "Unsupported AI usage metadata field.*unknown",
        ):
            validate_ai_usage_metadata({"unknown": "value"})
