from django.test import SimpleTestCase
from rest_framework import serializers

from accounts.api.v1.admin.serializers import AccountListFilterSerializer
from accounts.api.v1.client.serializers import UserSerializer, UserUpdateSerializer


class AccountSerializerSchemaTests(SimpleTestCase):
    def test_user_serializer_matches_current_account_models(self):
        self.assertEqual(
            tuple(UserSerializer().fields),
            (
                "id",
                "email",
                "name",
                "provider",
                "status",
                "is_email_verified",
                "is_active",
                "phone",
                "avatar_url",
                "city",
                "country",
                "timezone",
                "created_at",
                "updated_at",
                "last_login",
            ),
        )

    def test_profile_picture_cannot_be_cleared_and_replaced_together(self):
        serializer = UserUpdateSerializer()

        with self.assertRaisesMessage(
            serializers.ValidationError,
            "Cannot clear and replace the profile picture together.",
        ):
            serializer.validate(
                {
                    "profile_picture": object(),
                    "clear_profile_picture": True,
                }
            )


class AccountListFilterSerializerTests(SimpleTestCase):
    def test_accepts_current_profile_statuses(self):
        serializer = AccountListFilterSerializer(
            data={
                "status": ["ACTIVE", "SUSPENDED"],
                "is_email_verified": "true",
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["status"],
            ["ACTIVE", "SUSPENDED"],
        )
        self.assertIs(serializer.validated_data["is_email_verified"], True)

    def test_rejects_unknown_status(self):
        serializer = AccountListFilterSerializer(data={"status": ["UNKNOWN"]})

        self.assertFalse(serializer.is_valid())
        self.assertIn("status", serializer.errors)
