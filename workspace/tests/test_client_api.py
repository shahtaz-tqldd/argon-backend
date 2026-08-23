from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.models import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceRole,
    WorkspaceUser,
)
from workspace.services import add_workspace_user, ensure_personal_workspace

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
        self.invite_url = (
            f'{reverse("invite-workspace-member")}?'
            f"{urlencode(self.workspace_query)}"
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

    def test_workspace_create_assigns_owner_membership(self):
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
        self.assertTrue(
            WorkspaceUser.objects.filter(
                workspace=created,
                user=creator,
                role=WorkspaceRole.ADMIN,
                is_active=True,
            ).exists()
        )

    def test_workspace_detail_returns_the_users_member_workspace(self):
        member = User.objects.create_user(
            email="member@example.com",
            password="StrongPass123!",
        )
        add_workspace_user(
            workspace=self.workspace,
            user=member,
            added_by=self.owner,
        )
        self.client.force_authenticate(member)

        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["slug"], self.workspace.slug)
        self.assertEqual(
            response.data["data"]["current_user_role"],
            WorkspaceRole.MEMBER,
        )

        response = self.client.patch(
            self.update_url,
            {"name": "Not Allowed"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
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

    def test_workspace_detail_does_not_return_an_unrelated_workspace(self):
        unrelated_user = User.objects.create_user(
            email="unrelated@example.com",
            password="StrongPass123!",
        )
        self.client.force_authenticate(unrelated_user)

        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_workspace_delete_is_separate_and_soft_deletes(self):
        member = User.objects.create_user(
            email="member@example.com",
            password="StrongPass123!",
        )
        add_workspace_user(
            workspace=self.workspace,
            user=member,
            added_by=self.owner,
        )
        delete_url = (
            f'{reverse("workspace-delete")}?'
            f"{urlencode(self.workspace_query)}"
        )
        self.client.force_authenticate(member)
        response = self.client.delete(delete_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        response = self.client.delete(delete_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.workspace.refresh_from_db()
        self.assertFalse(self.workspace.is_active)
        self.assertEqual(self.workspace.updated_by, self.owner)

    def test_workspace_member_list_detail_role_and_remove(self):
        member = User.objects.create_user(
            email="member@example.com",
            password="StrongPass123!",
            name="Workspace Member",
        )
        add_workspace_user(
            workspace=self.workspace,
            user=member,
            added_by=self.owner,
        )
        self.client.force_authenticate(self.owner)

        response = self.client.get(
            reverse("workspace-members"),
            {**self.workspace_query, "page_size": 10},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 2)
        member_data = next(
            item
            for item in response.data["data"]
            if item["user"]["email"] == member.email
        )
        self.assertEqual(member_data["role"], WorkspaceRole.MEMBER)
        self.assertTrue(member_data["is_active"])
        self.assertIsNotNone(member_data["invited_at"])

        member_query = {
            **self.workspace_query,
            "member_email": member.email,
        }
        response = self.client.get(
            reverse("workspace-member-details"),
            member_query,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["user"]["email"], member.email)

        role_url = (
            f'{reverse("workspace-member-role")}?'
            f"{urlencode(member_query)}"
        )
        response = self.client.patch(
            role_url,
            {"role": WorkspaceRole.ADMIN},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = WorkspaceUser.objects.get(
            workspace=self.workspace,
            user=member,
        )
        self.assertEqual(membership.role, WorkspaceRole.ADMIN)

        remove_url = (
            f'{reverse("remove-workspace-member")}?'
            f"{urlencode(member_query)}"
        )
        response = self.client.delete(remove_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership.refresh_from_db()
        self.assertFalse(membership.is_active)
        self.assertTrue(User.objects.filter(pk=member.pk).exists())

    def test_regular_member_cannot_manage_workspace_roles(self):
        member = User.objects.create_user(
            email="member@example.com",
            password="StrongPass123!",
        )
        add_workspace_user(
            workspace=self.workspace,
            user=member,
            added_by=self.owner,
        )
        self.client.force_authenticate(member)
        role_url = (
            f'{reverse("workspace-member-role")}?'
            f'{urlencode({**self.workspace_query, "member_email": self.owner.email})}'
        )

        response = self.client.patch(
            role_url,
            {"role": WorkspaceRole.MEMBER},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_workspace_owner_cannot_be_demoted_or_removed(self):
        self.client.force_authenticate(self.owner)
        owner_query = {
            **self.workspace_query,
            "member_email": self.owner.email,
        }
        role_url = (
            f'{reverse("workspace-member-role")}?'
            f"{urlencode(owner_query)}"
        )
        response = self.client.patch(
            role_url,
            {"role": WorkspaceRole.MEMBER},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        remove_url = (
            f'{reverse("remove-workspace-member")}?'
            f"{urlencode(owner_query)}"
        )
        response = self.client.delete(remove_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

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

        response = self.client.get(
            reverse("workspace-members"),
            {**self.workspace_query, "page_size": 10},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pending_member = next(
            item
            for item in response.data["data"]
            if item["user"]["email"] == "invited@example.com"
        )
        self.assertEqual(pending_member["role"], WorkspaceRole.MEMBER)
        self.assertFalse(pending_member["is_active"])

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
