from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.models import Workspace, WorkspaceRole, WorkspaceUser
from workspace.services import ensure_personal_workspace, join_workspace_from_invitation

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

    def test_existing_user_keeps_personal_workspace_when_joining_another(self):
        inviter = User.objects.create_user(
            email="admin@example.com",
            password="StrongPass123!",
        )
        workspace = ensure_personal_workspace(inviter)

        invited_user = User.objects.create_user(
            email="invited@example.com",
            password="StrongPass123!",
        )
        personal_workspace = ensure_personal_workspace(invited_user)
        membership = join_workspace_from_invitation(
            workspace=workspace,
            user=invited_user,
            invited_by=inviter,
        )

        self.assertEqual(membership.workspace, workspace)
        self.assertEqual(membership.user, invited_user)
        self.assertEqual(membership.role, WorkspaceRole.MEMBER)
        self.assertEqual(
            set(
                WorkspaceUser.objects.filter(
                    user=invited_user,
                    is_active=True,
                ).values_list("workspace_id", flat=True)
            ),
            {personal_workspace.id, workspace.id},
        )
