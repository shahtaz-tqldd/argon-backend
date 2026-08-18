from django.test import SimpleTestCase

from app.api.v1.serializers import TourtoiseConfigUpdateSerializer


class TourtoiseConfigUpdateSerializerTests(SimpleTestCase):
    def test_accepts_sectioned_feature_payload_with_false_value(self):
        serializer = TourtoiseConfigUpdateSerializer(
            data={"feature": {"is_vectorize_enabled": False}},
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data,
            {"is_vectorize_enabled": False},
        )

    def test_still_accepts_flat_payload(self):
        serializer = TourtoiseConfigUpdateSerializer(
            data={"is_vectorize_enabled": False},
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data,
            {"is_vectorize_enabled": False},
        )

    def test_rejects_non_object_section_payload(self):
        serializer = TourtoiseConfigUpdateSerializer(
            data={"feature": False},
            partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("feature", serializer.errors)
