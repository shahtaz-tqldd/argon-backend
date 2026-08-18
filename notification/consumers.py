from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from chatbot.models import ChatbotUser
from notification.services import (
    chatbot_dashboard_group,
    global_dashboard_group,
    user_dashboard_group,
    workspace_dashboard_group,
)
from workspace.models import WorkspaceUser


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4401)
            return

        self.group_names = await self.get_group_names(user.id)
        for group_name in self.group_names:
            await self.channel_layer.group_add(group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        for group_name in getattr(self, "group_names", []):
            await self.channel_layer.group_discard(group_name, self.channel_name)

    async def notification_created(self, event):
        await self.send_json(event["notification"])

    @database_sync_to_async
    def get_group_names(self, user_id):
        workspace_ids = WorkspaceUser.objects.filter(
            user_id=user_id,
            is_active=True,
        ).values_list("workspace_id", flat=True)
        chatbot_ids = ChatbotUser.objects.filter(
            user_id=user_id,
            is_active=True,
            chatbot__workspace__memberships__user_id=user_id,
            chatbot__workspace__memberships__is_active=True,
        ).values_list("chatbot_id", flat=True)

        return [
            global_dashboard_group(),
            user_dashboard_group(user_id),
            *(workspace_dashboard_group(item) for item in workspace_ids),
            *(chatbot_dashboard_group(item) for item in chatbot_ids),
        ]
