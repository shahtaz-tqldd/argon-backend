from django.db.models import Exists, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from app.utils.pagination import CustomPagination
from app.utils.response import APIResponse
from notification.models import (
    Notification,
    NotificationRead,
    NotificationRecipientType,
)
from notification.api.v1.client.serializers import NotificationSerializer


class NotificationPaginationMixin:
    pagination_class = CustomPagination

    def paginate_notifications(
        self,
        queryset,
        unread_count=0,
        message="Notifications fetched successfully.",
    ):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, self.request, view=self)
        serializer = NotificationSerializer(
            page,
            many=True,
            context={"request": self.request},
        )
        return APIResponse.success(
            data=serializer.data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(self.request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "unread_count": unread_count,
            },
            message=message,
        )


class NotificationQuerysetMixin:
    def get_user_notifications(self):
        read_receipts = NotificationRead.objects.filter(
            notification=OuterRef("pk"),
            user=self.request.user,
        )
        return (
            Notification.objects.filter(
                Q(
                    recipient_type=NotificationRecipientType.USER,
                    recipient=self.request.user,
                )
                | Q(
                    recipient_type=NotificationRecipientType.GLOBAL,
                    recipient__isnull=True,
                    target_id__isnull=True,
                )
                | Q(
                    recipient_type=NotificationRecipientType.WORKSPACE,
                    workspace__memberships__user=self.request.user,
                    workspace__memberships__is_active=True,
                )
                | Q(
                    recipient_type=NotificationRecipientType.CHATBOT,
                    chatbot__memberships__user=self.request.user,
                    chatbot__memberships__is_active=True,
                    chatbot__workspace__memberships__user=self.request.user,
                    chatbot__workspace__memberships__is_active=True,
                )
            )
            .select_related("recipient", "workspace", "chatbot")
            .distinct()
            .annotate(
                is_read=Exists(read_receipts),
                read_at=Subquery(read_receipts.values("read_at")[:1]),
            )
            .order_by("-created_at")
        )

    def apply_filters(self, queryset, include_unread_filter=True):
        params = self.request.query_params
        recipient_type = params.get("recipient_type")
        if recipient_type:
            queryset = queryset.filter(recipient_type=recipient_type)

        notification_type = params.get("notification_type") or params.get("type")
        if notification_type:
            queryset = queryset.filter(notification_type=notification_type)

        workspace_id = params.get("workspace_id")
        if workspace_id:
            queryset = queryset.filter(workspace_id=workspace_id)

        chatbot_id = params.get("chatbot_id")
        if chatbot_id:
            queryset = queryset.filter(chatbot_id=chatbot_id)

        unread_only = params.get("unread_only", "").lower() in {"1", "true", "yes"}
        if include_unread_filter and unread_only:
            queryset = queryset.filter(is_read=False)

        return queryset


class NotificationListAPIView(
    NotificationPaginationMixin,
    NotificationQuerysetMixin,
    GenericAPIView,
):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = self.get_user_notifications()
        filtered_queryset = self.apply_filters(queryset)
        unread_count = self.apply_filters(
            queryset,
            include_unread_filter=False,
        ).filter(is_read=False).count()
        return self.paginate_notifications(filtered_queryset, unread_count=unread_count)


class NotificationReadAPIView(NotificationQuerysetMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, *args, **kwargs):
        notification = get_object_or_404(
            self.get_user_notifications(),
            pk=kwargs["notification_id"],
        )
        read, _ = NotificationRead.objects.get_or_create(
            notification=notification,
            user=request.user,
            defaults={"created_by": request.user},
        )
        return APIResponse.success(
            data={
                "id": str(notification.id),
                "is_read": True,
                "read_at": read.read_at or timezone.now(),
            },
            message="Notification marked as read.",
        )


class NotificationReadAllAPIView(NotificationQuerysetMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, *args, **kwargs):
        notifications = self.apply_filters(self.get_user_notifications()).filter(
            is_read=False
        )
        receipts = [
            NotificationRead(
                notification=notification,
                user=request.user,
                created_by=request.user,
            )
            for notification in notifications
        ]
        NotificationRead.objects.bulk_create(receipts, ignore_conflicts=True)
        return APIResponse.success(
            data={"marked_read_count": len(receipts)},
            message="Notifications marked as read.",
            status=status.HTTP_200_OK,
        )
