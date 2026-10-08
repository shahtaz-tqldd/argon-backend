from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.models import Workspace
from workspace.services import ensure_personal_workspace

User = get_user_model()


class WorkspaceOnboardingTests(TestCase):
    def test_personal_workspace_provisioning_is_idempotent(self):
        user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
            name="Workspace Owner",
        )

        first_workspace = ensure_personal_workspace(user)
        second_workspace = ensure_personal_workspace(user)

        self.assertEqual(first_workspace, second_workspace)
        self.assertEqual(
            Workspace.objects.filter(owner=user).count(),
            1,
        )
        self.assertEqual(first_workspace.slug, "workspace-owners-workspace")

    def test_personal_workspace_falls_back_to_email_name(self):
        user = User.objects.create_user(
            email="nameless@example.com",
            password="StrongPass123!",
        )

        workspace = ensure_personal_workspace(user)

        self.assertEqual(workspace.name, "nameless's Workspace")
