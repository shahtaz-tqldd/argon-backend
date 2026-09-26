from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot
from workspace.models import Workspace, WorkspaceRole, WorkspaceUser

User = get_user_model()


class AdminWorkspaceAPITests(APITestCase):
    list_url = "/api/v1/admin/workspaces/list/"
    detail_url = "/api/v1/admin/workspaces/details/"
    update_url = "/api/v1/admin/workspaces/update/"
    delete_url = "/api/v1/admin/workspaces/delete/"

    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            email="superadmin-workspaces@example.com",
            password="StrongPass123!",
        )
        self.owner = User.objects.create_user(
            email="workspace-owner@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Managed Workspace",
            industry="Technology",
            owner=self.owner,
            created_by=self.owner,
        )
        WorkspaceUser.objects.create(
            workspace=self.workspace,
            user=self.owner,
            role=WorkspaceRole.ADMIN,
            created_by=self.owner,
        )
        Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Workspace Bot",
            created_by=self.owner,
        )
        self.client.force_authenticate(self.superadmin)

    def workspace_query(self):
        return {"workspace": self.workspace.slug}

    def test_list_is_paginated_and_excludes_inactive_workspaces(self):
        second_owner = User.objects.create_user(
            email="second-workspace-owner@example.com",
            password="StrongPass123!",
        )
        second_workspace = Workspace.objects.create(
            name="Second Managed Workspace",
            owner=second_owner,
            created_by=second_owner,
        )
        inactive_owner = User.objects.create_user(
            email="inactive-workspace-owner@example.com",
            password="StrongPass123!",
        )
        Workspace.objects.create(
            name="Inactive Workspace",
            owner=inactive_owner,
            created_by=inactive_owner,
            is_active=False,
        )

        response = self.client.get(self.list_url, {"page_size": 1})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["page_size"], 1)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertIn(
            response.data["data"][0]["slug"],
            {self.workspace.slug, second_workspace.slug},
        )

        full_response = self.client.get(self.list_url, {"page_size": 10})
        managed_workspace = next(
            item
            for item in full_response.data["data"]
            if item["slug"] == self.workspace.slug
        )
        self.assertEqual(managed_workspace["member_count"], 1)
        self.assertEqual(managed_workspace["chatbot_count"], 1)

    def test_superadmin_can_fetch_workspace_details(self):
        response = self.client.get(
            self.detail_url,
            self.workspace_query(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["id"], str(self.workspace.id))
        self.assertEqual(
            response.data["data"]["owner"]["email"],
            self.owner.email,
        )
        self.assertEqual(response.data["data"]["member_count"], 1)
        self.assertEqual(response.data["data"]["chatbot_count"], 1)

    def test_superadmin_can_update_workspace(self):
        response = self.client.patch(
            self.update_url,
            {
                "name": "Updated Managed Workspace",
                "industry": "Healthcare",
            },
            format="json",
            query_params=self.workspace_query(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.name, "Updated Managed Workspace")
        self.assertEqual(self.workspace.industry, "Healthcare")
        self.assertEqual(self.workspace.updated_by, self.superadmin)
        self.assertEqual(
            response.data["data"]["updated_by"]["email"],
            self.superadmin.email,
        )

    def test_superadmin_can_soft_delete_workspace(self):
        response = self.client.delete(
            self.delete_url,
            query_params=self.workspace_query(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.workspace.refresh_from_db()
        self.assertFalse(self.workspace.is_active)
        self.assertEqual(self.workspace.logo, "")
        self.assertEqual(self.workspace.updated_by, self.superadmin)

        response = self.client.get(
            self.detail_url,
            self.workspace_query(),
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_object_endpoints_require_workspace_slug(self):
        for url in (self.detail_url, self.update_url, self.delete_url):
            response = self.client.get(url) if url == self.detail_url else (
                self.client.patch(url, {}, format="json")
                if url == self.update_url
                else self.client.delete(url)
            )
            self.assertEqual(
                response.status_code,
                status.HTTP_400_BAD_REQUEST,
            )

    def test_non_superadmin_cannot_access_admin_workspace_endpoints(self):
        staff_user = User.objects.create_user(
            email="ordinary-workspace-staff@example.com",
            password="StrongPass123!",
            is_staff=True,
        )
        self.client.force_authenticate(staff_user)

        responses = (
            self.client.get(self.list_url),
            self.client.get(self.detail_url, self.workspace_query()),
            self.client.patch(
                self.update_url,
                {"industry": "Forbidden"},
                format="json",
                query_params=self.workspace_query(),
            ),
            self.client.delete(
                self.delete_url,
                query_params=self.workspace_query(),
            ),
        )

        self.assertTrue(
            all(
                response.status_code == status.HTTP_403_FORBIDDEN
                for response in responses
            )
        )
