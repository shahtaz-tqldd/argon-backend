from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from analytics.choices import AIUsageType
from analytics.models import AIUsage
from analytics.services.ai_usage import record_ai_usage


class AIUsageValidationTests(TestCase):
    def test_accepts_supported_metadata_fields(self):
        usage = AIUsage(
            usage_type=AIUsageType.CHAT,
            metadata={
                "model": "gemini-2.5-flash",
                "input_tokens": 100,
                "output_tokens": 20,
                "cached_tokens": 10,
                "input_cost": "0.001",
                "output_cost": "0.002",
                "cached_cost": "0.0001",
            },
        )

        usage.full_clean()

    def test_rejects_unsupported_metadata_fields(self):
        usage = AIUsage(
            usage_type=AIUsageType.CHAT,
            metadata={"model": "gemini-2.5-flash", "provider": "google"},
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Unsupported AI usage metadata field(s): provider.",
        ):
            usage.full_clean()

    def test_rejects_non_object_metadata(self):
        usage = AIUsage(
            usage_type=AIUsageType.CHAT,
            metadata=["gemini-2.5-flash"],
        )

        with self.assertRaisesMessage(
            ValidationError,
            "AI usage metadata must be a JSON object.",
        ):
            usage.full_clean()

    def test_rejects_non_string_metadata_keys_cleanly(self):
        usage = AIUsage(
            usage_type=AIUsageType.CHAT,
            metadata={1: "unexpected"},
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Unsupported AI usage metadata field(s): 1.",
        ):
            usage.full_clean()

    def test_record_ai_usage_validates_metadata_before_saving(self):
        with self.assertRaises(ValidationError):
            record_ai_usage(
                user=None,
                usage_type=AIUsageType.CHAT,
                cost=Decimal("0.001"),
                tokens=120,
                metadata={"provider": "google"},
            )

        self.assertFalse(AIUsage.objects.exists())

    def test_record_ai_usage_normalizes_float_cost_to_field_precision(self):
        usage = record_ai_usage(
            user=None,
            usage_type=AIUsageType.CHAT,
            cost=0.00013919999999999998,
            tokens=464,
        )

        self.assertEqual(usage.cost, Decimal("0.00013920"))
