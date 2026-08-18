from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import IsSuperAdmin
from app.utils.pagination import CustomPagination
from vector_store.services.vectorize import DestinationVectorService
from app.utils.response import APIResponse
from vector_store.api.v1.admin.serializers import (
    VectorRecordBulkDeleteSerializer,
    VectorRecordListQuerySerializer,
    VectorRecordSerializer,
    VectorSearchResultSerializer,
    VectorSemanticSearchQuerySerializer,
)
from vector_store.models import VectorDocument


class VectorRecordListAPIView(GenericAPIView):
    """List vector chunks without exposing their embedding values."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = VectorRecordListQuerySerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        return VectorDocument.objects.using("vector").order_by("-updated_at")

    def get(self, request, *args, **kwargs):
        query_serializer = self.get_serializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        filters = query_serializer.validated_data

        queryset = self.get_queryset()
        source_types = filters.get("source_type")
        if source_types:
            queryset = queryset.filter(source_type__in=source_types)
        if destination_id := filters.get("destination_id"):
            queryset = queryset.filter(
                metadata__destination_id=str(destination_id),
            )
        if source_id := filters.get("source_id"):
            queryset = queryset.filter(source_id=source_id)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        records = VectorRecordSerializer(page, many=True).data
        return APIResponse.success(
            data=records,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message="Vector records fetched successfully.",
        )


class VectorSemanticSearchAPIView(GenericAPIView):
    """Rank vector chunks with parallel semantic and full-text search."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = VectorSemanticSearchQuerySerializer

    def get(self, request, *args, **kwargs):
        return self._search(request.query_params)

    def post(self, request, *args, **kwargs):
        return self._search(request.data)

    def _search(self, data):
        query_serializer = self.get_serializer(data=data)
        query_serializer.is_valid(raise_exception=True)
        filters = query_serializer.validated_data
        results = DestinationVectorService().search(
            filters["query"],
            limit=filters["limit"],
            source_types=filters.get("source_type"),
            destination_id=filters.get("destination_id"),
        )
        return APIResponse.success(
            data=VectorSearchResultSerializer(results, many=True).data,
            meta={"count": len(results)},
            message="Hybrid search completed successfully.",
        )


class VectorRecordDeleteAPIView(GenericAPIView):
    """Delete one vector chunk by its vector record ID."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get_object(self):
        queryset = VectorDocument.objects.using("vector")
        return get_object_or_404(queryset, pk=self.kwargs["record_id"])

    def delete(self, request, *args, **kwargs):
        record = self.get_object()
        record_id = str(record.id)
        record.delete(using="vector")
        return APIResponse.success(
            data={"id": record_id},
            message="Vector record deleted successfully.",
        )


class VectorRecordBulkDeleteAPIView(GenericAPIView):
    """Delete up to 500 vector chunks in one vector-database transaction."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = VectorRecordBulkDeleteSerializer

    def post(self, request, *args, **kwargs):
        return self._delete(request.data)

    def delete(self, request, *args, **kwargs):
        return self._delete(request.data)

    def _delete(self, data):
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        record_ids = serializer.validated_data["ids"]

        with transaction.atomic(using="vector"):
            queryset = (
                VectorDocument.objects.using("vector")
                .filter(id__in=record_ids)
                .select_for_update()
            )
            existing_ids = set(queryset.values_list("id", flat=True))
            deleted_count, _ = queryset.delete()

        deleted_ids = [
            str(record_id) for record_id in record_ids if record_id in existing_ids
        ]
        not_found_ids = [
            str(record_id) for record_id in record_ids if record_id not in existing_ids
        ]
        return APIResponse.success(
            data={
                "requested_count": len(record_ids),
                "deleted_count": deleted_count,
                "deleted_ids": deleted_ids,
                "not_found_ids": not_found_ids,
            },
            message="Vector records bulk deleted successfully.",
        )
