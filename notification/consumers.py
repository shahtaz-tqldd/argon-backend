from channels.generic.websocket import AsyncJsonWebsocketConsumer

from notification.services import global_dashboard_group, user_dashboard_group


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4401)
            return

        self.user_group_name = user_dashboard_group(user.id)
        self.global_group_name = global_dashboard_group()
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.channel_layer.group_add(self.global_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "user_group_name"):
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)
        if hasattr(self, "global_group_name"):
            await self.channel_layer.group_discard(self.global_group_name, self.channel_name)

    async def notification_created(self, event):
        await self.send_json(event["notification"])

    async def trip_message_created(self, event):
        await self.send_json(event["payload"])
