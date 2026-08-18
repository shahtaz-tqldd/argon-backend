from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.services.onboarding import create_user_from_workspace_invitation
from workspace.models import Workspace, WorkspaceRole, WorkspaceUser
from workspace.services import ensure_personal_workspace

User = get_user_model()


class WorkspaceOnboardingTests(TestCase):
    def test_direct_signup_provisioning_is_idempotent_and_makes_user_admin(self):
        user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
        )

        first_workspace = ensure_personal_workspace(user)
        second_workspace = ensure_personal_workspace(user)

        self.assertEqual(first_workspace, second_workspace)
        self.assertEqual(
            Workspace.objects.filter(owner=user).count(),
            1,
        )
        membership = WorkspaceUser.objects.get(
            workspace=first_workspace,
            user=user,
        )
        self.assertEqual(membership.role, WorkspaceRole.ADMIN)
        self.assertTrue(membership.is_active)

    def test_invited_new_user_gets_membership_without_personal_workspace(self):
        inviter = User.objects.create_user(
            email="admin@example.com",
            password="StrongPass123!",
        )
        workspace = ensure_personal_workspace(inviter)

        invited_user, membership = create_user_from_workspace_invitation(
            workspace=workspace,
            email="invited@example.com",
            password="StrongPass123!",
            invited_by=inviter,
        )

        self.assertEqual(membership.workspace, workspace)
        self.assertEqual(membership.user, invited_user)
        self.assertEqual(membership.role, WorkspaceRole.MEMBER)
        self.assertFalse(
            Workspace.objects.filter(
                owner=invited_user,
            ).exists()
        )
