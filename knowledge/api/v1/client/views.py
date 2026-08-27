from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import GenericAPIView

from app.utils.pagination import CustomPagination
from app.utils.permission import IsChatbotUser
from app.utils.response import APIResponse
from chatbot.models import Chatbot
from chatbot.utils.choices import ChatbotPermissionTypes
from knowledge.api.v1.client.serializers import (
    KNOWLEDGE_API_TYPE_TO_SOURCE_TYPE,
    FileKnowledgeCreateSerializer,
    KnowledgeBaseBasicSerializer,
    KnowledgeBaseQuerySerializer,
    KnowledgeBaseSerializer,
    KnowledgeChatbotQuerySerializer,
    KnowledgeMetadataUpdateSerializer,
    KnowledgeTrainingLogSerializer,
    KnowledgeUpdateQuerySerializer,
    KnowledgeUploadQuerySerializer,
    KnowledgeUsageSerializer,
    TextKnowledgeCreateSerializer,
    TextKnowledgeUpdateSerializer,
    URLKnowledgeCreateSerializer,
)
from knowledge.models import KnowledgeBase, KnowledgeTrainingLog
from knowledge.services import (
    KnowledgeEntitlementError,
    get_knowledge_subscription,
    get_knowledge_usage,
    queue_knowledge_training,
)
from knowledge.utils.choices import (
    KnowledgeTrainingStageTypes,
    StatusTypes,
)


ACTIVE_TRAINING_STAGES = {
    KnowledgeTrainingStageTypes.QUEUED,
    KnowledgeTrainingStageTypes.EXTRACTING,
    KnowledgeTrainingStageTypes.CHUNKING,
    KnowledgeTrainingStageTypes.EMBEDDING,
    KnowledgeTrainingStageTypes.INDEXING,
}


def has_active_training(knowledge_base):
    return knowledge_base.training_logs.filter(
        stage__in=ACTIVE_TRAINING_STAGES,
    ).exists()


class PaginatedKnowledgeMixin:
    pagination_class = CustomPagination

    def paginated_response(self, queryset, *, message):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, self.request, view=self)
        return APIResponse.success(
            data=self.get_serializer(page, many=True).data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(self.request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message=message,
        )


class KnowledgeChatbotMixin:
    _chatbot = None
    _chatbot_query = None
    chatbot_query_serializer_class = KnowledgeChatbotQuerySerializer

    def get_chatbot_query(self):
        if self._chatbot_query is None:
            serializer = self.chatbot_query_serializer_class(
                data=self.request.query_params,
            )
            serializer.is_valid(raise_exception=True)
            self._chatbot_query = serializer.validated_data
        return self._chatbot_query

    def get_chatbot(self):
        if self._chatbot is None:
            self._chatbot = get_object_or_404(
                Chatbot.objects.select_related("workspace"),
                slug=self.get_chatbot_query()["chatbot"],
                is_deleted=False,
                workspace__is_active=True,
            )
            self.check_object_permissions(self.request, self._chatbot)
        return self._chatbot

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["chatbot"] = self.get_chatbot()
        return context


class KnowledgeObjectMixin:
    _knowledge_base = None
    _knowledge_query = None
    knowledge_query_serializer_class = KnowledgeBaseQuerySerializer

    def get_knowledge_query(self):
        if self._knowledge_query is None:
            serializer = self.knowledge_query_serializer_class(
                data=self.request.query_params,
            )
            serializer.is_valid(raise_exception=True)
            self._knowledge_query = serializer.validated_data
        return self._knowledge_query

    def get_knowledge_base(self):
        if self._knowledge_base is None:
            latest_logs = KnowledgeTrainingLog.objects.order_by("-created_at")
            self._knowledge_base = get_object_or_404(
                KnowledgeBase.objects.select_related(
                    "chatbot",
                    "chatbot__workspace",
                ).prefetch_related(
                    Prefetch(
                        "training_logs",
                        queryset=latest_logs,
                        to_attr="all_training_logs",
                    )
                ),
                pk=self.get_knowledge_query()["knowledge_base_id"],
            )
            self.check_object_permissions(self.request, self._knowledge_base)
        return self._knowledge_base


class KnowledgeUploadView(KnowledgeChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    chatbot_query_serializer_class = KnowledgeUploadQuerySerializer
    serializer_by_type = {
        "file": FileKnowledgeCreateSerializer,
        "url": URLKnowledgeCreateSerializer,
        "custom": TextKnowledgeCreateSerializer,
    }

    def get_serializer_class(self):
        return self.serializer_by_type[self.get_chatbot_query()["type"]]

    def post(self, request, *args, **kwargs):
        try:
            get_knowledge_subscription(self.get_chatbot())
        except KnowledgeEntitlementError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        knowledge_base = serializer.save()
        training_log = queue_knowledge_training(knowledge_base)
        return APIResponse.success(
            data={
                "knowledge_base": KnowledgeBaseSerializer(
                    knowledge_base,
                    context=self.get_serializer_context(),
                ).data,
                "training": KnowledgeTrainingLogSerializer(training_log).data,
            },
            message="Knowledge source uploaded and queued for training.",
            status=status.HTTP_201_CREATED,
        )


class KnowledgeListView(
    KnowledgeChatbotMixin,
    PaginatedKnowledgeMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    serializer_class = KnowledgeBaseBasicSerializer

    def get(self, request, *args, **kwargs):
        queryset = (
            KnowledgeBase.objects.filter(chatbot=self.get_chatbot())
            .only(
                "id",
                "title",
                "url",
                "source_type",
                "original_filename",
                "text_content",
                "file_type",
                "file_size",
                "is_enabled",
                "status",
                "last_crawled_at",
                "processed_at",
                "created_at",
                "updated_at",
            )
            .order_by("-created_at")
        )
        return self.paginated_response(
            queryset,
            message="Knowledge sources fetched successfully.",
        )


class KnowledgeUsageView(KnowledgeChatbotMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = KnowledgeUsageSerializer

    def get(self, request, *args, **kwargs):
        try:
            usage = get_knowledge_usage(self.get_chatbot())
        except KnowledgeEntitlementError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_403_FORBIDDEN,
            )
        return APIResponse.success(
            data=self.get_serializer(usage).data,
            message="Knowledge usage fetched successfully.",
        )


class KnowledgeDetailView(KnowledgeObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = KnowledgeBaseSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_knowledge_base()).data,
            message="Knowledge source fetched successfully.",
        )


class KnowledgeUpdateView(KnowledgeObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    required_chatbot_permission = ChatbotPermissionTypes.SETUP_CONFIGURATION
    knowledge_query_serializer_class = KnowledgeUpdateQuerySerializer

    def get_serializer_class(self):
        if self.get_knowledge_query()["type"] == "custom":
            return TextKnowledgeUpdateSerializer
        return KnowledgeMetadataUpdateSerializer

    def _queue_retraining(self, knowledge_base, *, message):
        try:
            get_knowledge_subscription(knowledge_base.chatbot)
        except KnowledgeEntitlementError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_403_FORBIDDEN,
            )
        if has_active_training(knowledge_base):
            return APIResponse.error(
                message="This knowledge source already has an active training job.",
                status=status.HTTP_409_CONFLICT,
            )
        training = queue_knowledge_training(knowledge_base, force=True)
        existing_logs = getattr(knowledge_base, "all_training_logs", [])
        knowledge_base.all_training_logs = [training, *existing_logs]
        return APIResponse.success(
            data={
                "knowledge_base": KnowledgeBaseSerializer(knowledge_base).data,
                "training": KnowledgeTrainingLogSerializer(training).data,
            },
            message=message,
            status=status.HTTP_202_ACCEPTED,
        )

    def _update(self, request):
        knowledge_base = self.get_knowledge_base()
        api_type = self.get_knowledge_query()["type"]
        expected_source_type = KNOWLEDGE_API_TYPE_TO_SOURCE_TYPE[api_type]
        if knowledge_base.source_type != expected_source_type:
            return APIResponse.error(
                errors={"type": ["Type does not match this knowledge source."]},
                message="Type does not match this knowledge source.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request.data:
            serializer = self.get_serializer(
                knowledge_base,
                data=request.data,
                partial=True,
            )
            serializer.is_valid(raise_exception=True)
            content_changed = "content" in serializer.validated_data

            if content_changed and has_active_training(knowledge_base):
                return APIResponse.error(
                    message="Content cannot be edited while training is active.",
                    status=status.HTTP_409_CONFLICT,
                )

            knowledge_base = serializer.save()
            if content_changed:
                return self._queue_retraining(
                    knowledge_base,
                    message=(
                        "Custom knowledge source updated and queued for training."
                    ),
                )

            return APIResponse.success(
                data=KnowledgeBaseSerializer(knowledge_base).data,
                message="Knowledge source updated successfully.",
            )

        if api_type == "file":
            if has_active_training(knowledge_base):
                return APIResponse.error(
                    message=(
                        "This knowledge source already has an active training job."
                    ),
                    status=status.HTTP_409_CONFLICT,
                )
            if knowledge_base.status != StatusTypes.FAILED:
                return APIResponse.error(
                    message="Only a failed file source can be retrained.",
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return self._queue_retraining(
                knowledge_base,
                message="Failed file source queued for retraining.",
            )

        if api_type == "url":
            return self._queue_retraining(
                knowledge_base,
                message="URL knowledge source queued for retraining.",
            )

        return APIResponse.error(
            errors={"content": ["This field is required."]},
            message="Content is required to update a custom knowledge source.",
            status=status.HTTP_400_BAD_REQUEST,
        )

    def put(self, request, *args, **kwargs):
        return self._update(request)

    def patch(self, request, *args, **kwargs):
        return self._update(request)


class KnowledgeTrainingListView(
    KnowledgeChatbotMixin,
    PaginatedKnowledgeMixin,
    GenericAPIView,
):
    permission_classes = [IsChatbotUser]
    serializer_class = KnowledgeTrainingLogSerializer

    def get(self, request, *args, **kwargs):
        queryset = KnowledgeTrainingLog.objects.filter(
            knowledge_base__chatbot=self.get_chatbot(),
        ).select_related("knowledge_base")
        return self.paginated_response(
            queryset,
            message="Training logs fetched successfully.",
        )


class KnowledgeDeleteView(KnowledgeObjectMixin, GenericAPIView):
    permission_classes = [IsChatbotUser]
    serializer_class = KnowledgeBaseSerializer
    chatbot_admin_only = True

    def delete(self, request, *args, **kwargs):
        knowledge_base = self.get_knowledge_base()
        if has_active_training(knowledge_base):
            return APIResponse.error(
                message="A knowledge source cannot be deleted while training is active.",
                status=status.HTTP_409_CONFLICT,
            )
        source_id = str(knowledge_base.id)
        knowledge_base.delete()
        return APIResponse.success(
            data={"id": source_id},
            message="Knowledge source and vectors deleted successfully.",
        )
