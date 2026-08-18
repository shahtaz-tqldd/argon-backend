from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.models import Workspace, WorkspaceInvitation, WorkspaceRole, WorkspaceUser
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
        self.detail_url = reverse(
            "workspace-detail",
            kwargs={"workspace_slug": self.workspace.slug},
        )
        self.invite_url = reverse(
            "invite-workspace-member",
            kwargs={"workspace_slug": self.workspace.slug},
        )

    def test_direct_registration_creates_workspace_and_owner_membership(self):
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
        workspace = Workspace.objects.get(owner=new_owner)
        self.assertEqual(workspace.slug, "new-owners-workspace")
        self.assertTrue(
            WorkspaceUser.objects.filter(
                workspace=workspace,
                user=new_owner,
                role=WorkspaceRole.ADMIN,
                is_active=True,
            ).exists()
        )

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

    def test_member_can_get_workspace_but_only_owner_can_update_it(self):
        member = User.objects.create_user(
            email="member@example.com",
            password="StrongPass123!",
        )
        WorkspaceUser.objects.create(
            workspace=self.workspace,
            user=member,
            role=WorkspaceRole.MEMBER,
            created_by=self.owner,
        )
        self.client.force_authenticate(member)

        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["slug"], self.workspace.slug)

        response = self.client.patch(self.detail_url, {"name": "Not Allowed"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("current-workspace"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["slug"], self.workspace.slug)

        response = self.client.patch(
            self.detail_url,
            {"name": "Updated Workspace", "industry": "Technology"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.name, "Updated Workspace")
        self.assertEqual(self.workspace.industry, "Technology")
        self.assertEqual(self.workspace.slug, "workspace-owners-workspace")

    def test_invited_user_registers_from_one_time_token_and_joins_workspace(self):
        self.client.force_authenticate(self.owner)
        with patch(
            "workspace.services.invitations._deliver_workspace_invitation"
        ) as deliver:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    self.invite_url,
                    {"email": "INVITED@example.com"},
                    format="json",
                )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("token", response.data["data"])
        token = deliver.call_args.kwargs["token"]

        self.client.force_authenticate(user=None)
        accept_url = reverse("accept-workspace-invitation")
        payload = {
            "token": token,
            "name": "Invited User",
            "password": "StrongPass123!",
            "confirm_password": "StrongPass123!",
        }
        response = self.client.post(accept_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invited_user = User.objects.get(email="invited@example.com")
        self.assertTrue(invited_user.is_email_verified)
        self.assertFalse(Workspace.objects.filter(owner=invited_user).exists())
        self.assertTrue(
            WorkspaceUser.objects.filter(
                workspace=self.workspace,
                user=invited_user,
                role=WorkspaceRole.MEMBER,
                is_active=True,
            ).exists()
        )
        invitation = WorkspaceInvitation.objects.get(
            workspace=self.workspace,
            email="invited@example.com",
        )
        self.assertIsNotNone(invitation.accepted_at)
        self.assertIn("access_token", response.data["data"]["tokens"])

        response = self.client.post(accept_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
