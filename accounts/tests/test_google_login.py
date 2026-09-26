from unittest.mock import patch

from django.test import TestCase

from accounts.api.v1.client.serializers import GoogleLoginSerializer
from accounts.choices import AccountProvider
from accounts.models import User


class GoogleLoginSerializerTests(TestCase):
    def _serializer(self, *, email, uid, name, photo_url):
        serializer = GoogleLoginSerializer(
            data={"firebase_id_token": "firebase-token"}
        )
        with patch(
            "accounts.api.v1.client.serializers.verify_firebase_id_token",
            return_value={
                "uid": uid,
                "email": email,
                "email_verified": True,
                "name": name,
                "picture": photo_url,
            },
        ):
            self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer

    @patch("accounts.api.v1.client.serializers.complete_email_verification")
    def test_first_google_login_sets_name_and_avatar(self, provision, complete):
        serializer = self._serializer(
            email="new-google-user@example.com",
            uid="new-google-uid",
            name="Google Name",
            photo_url="https://example.com/google-avatar.jpg",
        )

        serializer.save()

        user = User.objects.get(email="new-google-user@example.com")
        self.assertEqual(user.name, "Google Name")
        self.assertEqual(
            user.profile.avatar_url,
            "https://example.com/google-avatar.jpg",
        )
        provision.assert_called_once_with(user)
        complete.assert_called_once_with(user)

    @patch("accounts.api.v1.client.serializers.complete_email_verification")
    def test_later_google_login_preserves_name_and_avatar(self, complete):
        user = User.objects.create_user(
            email="existing-google-user@example.com",
            password=None,
            name="Custom Name",
            provider=AccountProvider.GOOGLE,
            firebase_uid="existing-google-uid",
            is_email_verified=True,
        )
        user.profile.avatar_url = "https://example.com/custom-avatar.jpg"
        user.profile.save(update_fields=["avatar_url"])
        serializer = self._serializer(
            email=user.email,
            uid=user.firebase_uid,
            name="Changed Google Name",
            photo_url="https://example.com/changed-google-avatar.jpg",
        )

        serializer.save()

        user.refresh_from_db()
        user.profile.refresh_from_db()
        self.assertEqual(user.name, "Custom Name")
        self.assertEqual(
            user.profile.avatar_url,
            "https://example.com/custom-avatar.jpg",
        )
        complete.assert_called_once_with(user)

    @patch("accounts.api.v1.client.serializers.complete_email_verification")
    def test_later_google_login_does_not_fill_an_empty_avatar(self, complete):
        user = User.objects.create_user(
            email="existing-user-without-avatar@example.com",
            password=None,
            name="Existing Name",
            provider=AccountProvider.GOOGLE,
            firebase_uid="existing-user-without-avatar-uid",
            is_email_verified=True,
        )
        serializer = self._serializer(
            email=user.email,
            uid=user.firebase_uid,
            name="Google Name",
            photo_url="https://example.com/google-avatar.jpg",
        )

        serializer.save()

        user.refresh_from_db()
        user.profile.refresh_from_db()
        self.assertEqual(user.name, "Existing Name")
        self.assertEqual(user.profile.avatar_url, "")
        complete.assert_called_once_with(user)
