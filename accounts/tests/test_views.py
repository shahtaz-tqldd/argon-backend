from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.models import Workspace

User = get_user_model()


class UserDetailsViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="details@example.com",
            password="StrongPass123!",
            name="Details User",
        )
        self.client.force_authenticate(self.user)
        self.url = reverse("user-details")

    def test_returns_owned_workspace_slug(self):
        workspace = Workspace.objects.create(
            owner=self.user,
            name="Personal Workspace",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["workspace_slug"], workspace.slug)

    def test_returns_null_when_user_has_no_owned_workspace(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["data"]["workspace_slug"])
