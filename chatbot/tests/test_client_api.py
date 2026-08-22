from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from chatbot.models import Chatbot, ChatbotInvitation, ChatbotUser
from chatbot.services import create_chatbot
from chatbot.utils.choices import ChatbotPermissionTypes, ChatbotRoleTypes
from workspace.models import Workspace, WorkspaceUser
from workspace.services import add_workspace_user, ensure_personal_workspace

User = get_user_model()


class ChatbotClientAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
        )
        self.member = User.objects.create_user(
            email="member@example.com",
            password="StrongPass123!",
        )
        self.workspace = ensure_personal_workspace(self.owner)
        add_workspace_user(
            workspace=self.workspace,
            user=self.member,
            added_by=self.owner,
        )
        self.chatbot = create_chatbot(
            workspace=self.workspace,
            name="Support Bot",
            created_by=self.owner,
        )
        ChatbotUser.objects.create(chatbot=self.chatbot, user=self.member)
        self.client.force_authenticate(self.owner)

    def test_chatbot_detail_uses_chatbot_query_parameter(self):
        response = self.client.get(
            reverse("chatbot-detail"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["slug"], self.chatbot.slug)

    def test_chatbot_create_accepts_core_settings_and_creates_widget_settings(self):
        response = self.client.post(
            reverse("chatbot-create"),
            {
                "workspace": self.workspace.slug,
                "name": "Configured Bot",
                "welcome_message": "Welcome!",
                "fallback_message": "Please try again.",
                "instructions": "Be concise.",
                "language": "bn",
                "timezone": "Asia/Dhaka",
                "ai_enabled": False,
                "knowledge_base_enabled": False,
                "human_handoff_enabled": True,
                "other_settings": {"response_tone": "friendly"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        chatbot = Chatbot.objects.get(name="Configured Bot")
        self.assertEqual(chatbot.welcome_message, "Welcome!")
        self.assertEqual(chatbot.fallback_message, "Please try again.")
        self.assertEqual(chatbot.instructions, "Be concise.")
        self.assertEqual(chatbot.language, "bn")
        self.assertEqual(chatbot.timezone, "Asia/Dhaka")
        self.assertFalse(chatbot.ai_enabled)
        self.assertFalse(chatbot.knowledge_base_enabled)
        self.assertEqual(chatbot.other_settings["response_tone"], "friendly")
        self.assertTrue(chatbot.widget_settings.public_key)

    def test_chatbot_list_returns_page_metadata(self):
        create_chatbot(
            workspace=self.workspace,
            name="Sales Bot",
            created_by=self.owner,
        )

        response = self.client.get(
            reverse("chatbot-list"),
            {"page": 1, "page_size": 1},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["page"], 1)
        self.assertEqual(response.data["meta"]["page_size"], 1)
        self.assertEqual(response.data["meta"]["num_pages"], 2)
        self.assertIsNotNone(response.data["meta"]["next"])
        self.assertIsNone(response.data["meta"]["previous"])

    def test_chatbot_member_list_returns_page_metadata(self):
        response = self.client.get(
            reverse("chatbot-members"),
            {
                "chatbot": self.chatbot.slug,
                "page": 1,
                "page_size": 1,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["meta"]["count"], 2)
        self.assertEqual(response.data["meta"]["page"], 1)
        self.assertEqual(response.data["meta"]["page_size"], 1)
        self.assertEqual(response.data["meta"]["num_pages"], 2)

    def test_chatbot_member_list_returns_profile_and_activity_data(self):
        avatar = "https://example.com/member-avatar.png"
        self.member.profile.avatar_url = avatar
        self.member.profile.save(update_fields=["avatar_url"])
        self.member.last_active = timezone.now()
        self.member.last_login = timezone.now()
        self.member.save(update_fields=["last_active", "last_login"])

        response = self.client.get(
            reverse("chatbot-members"),
            {"chatbot": self.chatbot.slug, "page_size": 10},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        member_data = next(
            item
            for item in response.data["data"]
            if item["user"]["email"] == self.member.email
        )
        self.assertEqual(
            member_data["user"],
            {
                "email": self.member.email,
                "name": self.member.name,
                "avatar": avatar,
            },
        )
        self.assertFalse(member_data["all_permissions"])
        self.assertNotIn("effective_permissions", member_data)
        self.assertIsNotNone(member_data["last_active"])
        self.assertIsNotNone(member_data["last_login"])
        self.assertIsNotNone(member_data["invited_at"])

        admin_data = next(
            item
            for item in response.data["data"]
            if item["user"]["email"] == self.owner.email
        )
        self.assertTrue(admin_data["all_permissions"])

    def test_chatbot_member_detail_uses_chatbot_and_email_query_parameters(self):
        response = self.client.get(
            reverse("chatbot-member-details"),
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"]["user"]["email"],
            self.member.email,
        )

    @patch("chatbot.api.v1.client.serializers.issue_chatbot_invitation")
    def test_invite_accepts_email_and_permissions_payload(self, issue_invitation):
        permissions = [
            ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT,
            ChatbotPermissionTypes.SETUP_CONFIGURATION,
        ]
        invitation = ChatbotInvitation.objects.create(
            chatbot=self.chatbot,
            email=self.member.email,
            token_hash="test-token-hash",
            expires_at=timezone.now() + timedelta(hours=1),
            permissions=permissions,
            created_by=self.owner,
        )
        issue_invitation.return_value = invitation
        query = urlencode({"chatbot": self.chatbot.slug})

        response = self.client.post(
            f'{reverse("invite-chatbot-member")}?{query}',
            {
                "email": self.member.email,
                "permissions": permissions,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        issue_invitation.assert_called_once_with(
            chatbot=self.chatbot,
            email=self.member.email,
            permissions=permissions,
            invited_by=self.owner,
        )

    def test_invited_permissions_are_applied_when_invitation_is_accepted(self):
        invitee_email = "invitee@example.com"
        invitee_name = "Invited Member"
        invitee_password = "StrongInvitePass123!"
        permissions = [
            ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT,
            ChatbotPermissionTypes.SETUP_CONFIGURATION,
        ]
        query = urlencode({"chatbot": self.chatbot.slug})
        with patch(
            "chatbot.services.invitations._deliver_chatbot_invitation"
        ) as deliver:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f'{reverse("invite-chatbot-member")}?{query}',
                    {
                        "email": invitee_email,
                        "permissions": permissions,
                    },
                    format="json",
                )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["permissions"], permissions)
        invited_at = response.data["data"]["invited_at"]
        invitee = User.objects.get(email=invitee_email)
        self.assertFalse(invitee.has_usable_password())
        self.assertFalse(
            WorkspaceUser.objects.filter(user=invitee).exists()
        )
        self.assertFalse(Workspace.objects.filter(owner=invitee).exists())
        token = deliver.call_args.kwargs["token"]

        response = self.client.get(
            reverse("chatbot-members"),
            {"chatbot": self.chatbot.slug, "page_size": 10},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 3)
        pending_members = [
            item
            for item in response.data["data"]
            if item["user"]["email"] == invitee_email
        ]
        self.assertEqual(len(pending_members), 1)
        pending_member = pending_members[0]
        self.assertEqual(pending_member["role"], ChatbotRoleTypes.MEMBER)
        self.assertEqual(pending_member["permissions"], permissions)
        self.assertFalse(pending_member["all_permissions"])
        self.assertFalse(pending_member["is_active"])
        self.assertEqual(pending_member["invited_at"], invited_at)

        self.client.force_authenticate(user=None)
        response = self.client.post(
            reverse("accept-chatbot-invitation"),
            {
                "name": invitee_name,
                "password": invitee_password,
                "confirm_password": invitee_password,
                "token": token,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("access_token", response.data["data"])
        self.assertIn("refresh_token", response.data["data"])
        membership = ChatbotUser.objects.get(
            chatbot=self.chatbot,
            user=invitee,
        )
        self.assertEqual(membership.permissions, permissions)
        invitee.refresh_from_db()
        self.assertEqual(invitee.name, invitee_name)
        self.assertTrue(invitee.check_password(invitee_password))
        self.assertTrue(invitee.is_email_verified)
        self.assertIsNotNone(invitee.last_login)
        self.assertFalse(
            WorkspaceUser.objects.filter(user=invitee).exists()
        )

        self.client.credentials(
            HTTP_AUTHORIZATION=(
                f'Bearer {response.data["data"]["access_token"]}'
            )
        )
        response = self.client.get(
            reverse("chatbot-detail"),
            {"chatbot": self.chatbot.slug},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.patch(
            (
                f'{reverse("chatbot-update")}?'
                f'{urlencode({"chatbot": self.chatbot.slug})}'
            ),
            {"description": "Updated by chatbot-only member"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.get(reverse("chatbot-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)
        response = self.client.get(
            reverse("chatbot-members"),
            {"chatbot": self.chatbot.slug, "page_size": 10},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 3)
        accepted_members = [
            item
            for item in response.data["data"]
            if item["user"]["email"] == invitee_email
        ]
        self.assertEqual(len(accepted_members), 1)
        self.assertTrue(accepted_members[0]["is_active"])
        self.assertEqual(accepted_members[0]["invited_at"], invited_at)

    def test_invitation_acceptance_rejects_mismatched_passwords(self):
        invitee_email = "password-mismatch@example.com"
        query = urlencode({"chatbot": self.chatbot.slug})
        with patch(
            "chatbot.services.invitations._deliver_chatbot_invitation"
        ) as deliver:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f'{reverse("invite-chatbot-member")}?{query}',
                    {"email": invitee_email, "permissions": []},
                    format="json",
                )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        token = deliver.call_args.kwargs["token"]

        self.client.force_authenticate(user=None)
        response = self.client.post(
            reverse("accept-chatbot-invitation"),
            {
                "name": "Invited Member",
                "password": "StrongInvitePass123!",
                "confirm_password": "DifferentPass123!",
                "token": token,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("confirm_password", response.data["errors"])
        invitation = ChatbotInvitation.objects.get(
            chatbot=self.chatbot,
            email=invitee_email,
        )
        self.assertIsNone(invitation.accepted_at)
        invitee = User.objects.get(email=invitee_email)
        self.assertFalse(invitee.has_usable_password())
        self.assertFalse(
            ChatbotUser.objects.filter(
                chatbot=self.chatbot,
                user=invitee,
            ).exists()
        )

    def test_jwt_activity_updates_last_active(self):
        self.client.force_authenticate(user=None)
        access_token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        response = self.client.get(reverse("chatbot-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.owner.refresh_from_db()
        self.assertIsNotNone(self.owner.last_active)

    def test_remove_member_deletes_only_membership_and_marks_account_orphan(self):
        query = urlencode(
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            }
        )
        response = self.client.delete(
            f'{reverse("remove-chatbot-member")}?{query}',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            ChatbotUser.objects.filter(
                chatbot=self.chatbot,
                user=self.member,
            ).exists()
        )
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_orphan)

    def test_member_detail_requires_both_query_parameters(self):
        response = self.client.get(
            reverse("chatbot-member-details"),
            {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("member_email", response.data)

    def test_admin_can_manage_only_permissions_available_to_the_chatbot(self):
        query = urlencode(
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            }
        )
        url = f'{reverse("chatbot-member-permissions")}?{query}'

        response = self.client.patch(
            url,
            {
                "permissions": [
                    ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT,
                    ChatbotPermissionTypes.LEAD_MANAGEMENT,
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        permissions = [
            ChatbotPermissionTypes.SETUP_CONFIGURATION,
        ]
        response = self.client.patch(
            url,
            {"permissions": permissions},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = ChatbotUser.objects.get(
            chatbot=self.chatbot,
            user=self.member,
        )
        self.assertEqual(membership.permissions, permissions)
        owner_membership = ChatbotUser.objects.get(
            chatbot=self.chatbot,
            user=self.owner,
        )
        self.assertTrue(owner_membership.all_permissions)
        self.assertFalse(
            owner_membership.has_permission(ChatbotPermissionTypes.LEAD_MANAGEMENT)
        )
        self.assertEqual(
            response.data["data"]["member"]["effective_permissions"],
            permissions,
        )
        self.assertEqual(
            {
                item["code"]
                for item in response.data["data"]["available_permissions"]
            },
            {
                ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT,
                ChatbotPermissionTypes.SETUP_CONFIGURATION,
            },
        )

    def test_setup_permission_is_enforced_for_chatbot_updates(self):
        update_url = (
            f'{reverse("chatbot-update")}?'
            f'{urlencode({"chatbot": self.chatbot.slug})}'
        )
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            update_url,
            {"description": "Member update"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        permission_query = urlencode(
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.member.email,
            }
        )
        response = self.client.patch(
            (
                f'{reverse("chatbot-member-permissions")}?'
                f"{permission_query}"
            ),
            {
                "permissions": [
                    ChatbotPermissionTypes.SETUP_CONFIGURATION,
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(self.member)
        response = self.client.patch(
            update_url,
            {"description": "Member update"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.chatbot.refresh_from_db()
        self.assertEqual(self.chatbot.description, "Member update")

    def test_regular_member_cannot_manage_permissions(self):
        query = urlencode(
            {
                "chatbot": self.chatbot.slug,
                "member_email": self.owner.email,
            }
        )
        self.client.force_authenticate(self.member)

        response = self.client.patch(
            f'{reverse("chatbot-member-permissions")}?{query}',
            {"permissions": []},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
