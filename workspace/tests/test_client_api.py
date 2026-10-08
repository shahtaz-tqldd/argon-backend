from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.models import Workspace
from workspace.services import ensure_personal_workspace

User = get_user_model()


class WorkspaceClientAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
            name="Workspace Owner",
        )
        self.workspace = ensure_personal_workspace(self.owner)
        self.workspace_query = {"workspace": self.workspace.slug}
        self.detail_url = reverse("workspace-detail")
        self.update_url = (
            f'{reverse("workspace-update")}?'
            f"{urlencode(self.workspace_query)}"
        )

    def test_direct_registration_creates_the_account(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            reverse("register"),
            {
                "email": "new-owner@example.com",
                "name": "New Owner",
                "password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_owner = User.objects.get(email="new-owner@example.com")
        self.assertTrue(new_owner.is_active)

    def test_registration_claims_legacy_passwordless_invitation_account(self):
        shell_user = User.objects.create_user(
            email="legacy-invite@example.com",
            password=None,
        )
        self.assertFalse(shell_user.has_usable_password())

        self.client.force_authenticate(user=None)
        response = self.client.post(
            reverse("register"),
            {
                "email": shell_user.email,
                "name": "Claimed User",
                "password": "ClaimedPass123!",
                "confirm_password": "ClaimedPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.filter(email=shell_user.email).count(), 1)
        shell_user.refresh_from_db()
        self.assertEqual(shell_user.name, "Claimed User")
        self.assertTrue(shell_user.check_password("ClaimedPass123!"))

    def test_workspace_slug_is_generated_and_unique(self):
        second_owner = User.objects.create_user(
            email="second@example.com",
            password="StrongPass123!",
            name="Second Owner",
        )
        second = Workspace.objects.create(
            name=self.workspace.name,
            owner=second_owner,
        )

        self.assertEqual(self.workspace.slug, "workspace-owners-workspace")
        self.assertEqual(second.slug, "workspace-owners-workspace-2")

    def test_workspace_create_assigns_owner(self):
        creator = User.objects.create_user(
            email="creator@example.com",
            password="StrongPass123!",
        )
        self.client.force_authenticate(creator)
        response = self.client.post(
            reverse("workspace-create"),
            {"name": "Product Workspace", "industry": "Technology"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = Workspace.objects.get(slug="product-workspace")
        self.assertEqual(created.owner, creator)

        response = self.client.post(
            reverse("workspace-create"),
            {"name": "Second Workspace"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Workspace.objects.filter(owner=creator).count(), 1)

    def test_workspace_list_returns_only_owned_workspaces_with_metadata(self):
        other_owner = User.objects.create_user(
            email="other-owner@example.com",
            password="StrongPass123!",
        )
        ensure_personal_workspace(other_owner)
        self.client.force_authenticate(self.owner)

        response = self.client.get(
            reverse("workspace-list"),
            {"page": 1, "page_size": 2},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["data"][0]["slug"], self.workspace.slug)
        self.assertEqual(response.data["meta"]["count"], 1)
        self.assertEqual(response.data["meta"]["page"], 1)
        self.assertEqual(response.data["meta"]["page_size"], 2)
        self.assertIsNone(response.data["meta"]["next"])
        self.assertIsNone(response.data["meta"]["previous"])

    def test_workspace_list_is_empty_for_user_without_workspace(self):
        workspaceless_user = User.objects.create_user(
            email="workspaceless@example.com",
            password="StrongPass123!",
        )
        self.client.force_authenticate(workspaceless_user)

        response = self.client.get(
            reverse("workspace-list"),
            {"page_size": 10},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"], [])
        self.assertEqual(response.data["meta"]["count"], 0)

    def test_workspace_detail_update_and_delete_are_owner_only(self):
        unrelated_user = User.objects.create_user(
            email="unrelated@example.com",
            password="StrongPass123!",
        )
        delete_url = (
            f'{reverse("workspace-delete")}?'
            f"{urlencode(self.workspace_query)}"
        )

        self.client.force_authenticate(unrelated_user)
        self.assertEqual(
            self.client.get(self.detail_url).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.get(
                f'{reverse("workspace-detail")}?'
                f"{urlencode(self.workspace_query)}"
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.patch(
                self.update_url,
                {"name": "Not Allowed"},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.delete(delete_url).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get(
            f'{reverse("workspace-detail")}?'
            f"{urlencode(self.workspace_query)}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["slug"], self.workspace.slug)

        response = self.client.patch(
            self.update_url,
            {"name": "Updated Workspace", "industry": "Technology"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.name, "Updated Workspace")
        self.assertEqual(self.workspace.industry, "Technology")
        self.assertEqual(self.workspace.slug, "workspace-owners-workspace")

        response = self.client.delete(delete_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.workspace.refresh_from_db()
        self.assertFalse(self.workspace.is_active)
        self.assertEqual(self.workspace.updated_by, self.owner)
