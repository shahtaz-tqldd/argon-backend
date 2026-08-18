from django.urls import path

from vector_store.api.v1.admin.views import (
    VectorRecordBulkDeleteAPIView,
    VectorRecordDeleteAPIView,
    VectorRecordListAPIView,
    VectorSemanticSearchAPIView,
)


urlpatterns = [
    path("records/", VectorRecordListAPIView.as_view(), name="vector-record-list"),
    path("search/", VectorSemanticSearchAPIView.as_view(), name="vector-record-search"),
    path(
        "records/bulk-delete/",
        VectorRecordBulkDeleteAPIView.as_view(),
        name="vector-record-bulk-delete",
    ),
    path(
        "records/<uuid:record_id>/delete/",
        VectorRecordDeleteAPIView.as_view(),
        name="vector-record-delete",
    ),
]
