from django.urls import path

from knowledge.api.v1.client import views

urlpatterns = [
    path("upload/", views.KnowledgeUploadAPIView.as_view(), name="knowledge-upload"),
    path("list/", views.KnowledgeListAPIView.as_view(), name="knowledge-list"),
    path("search/", views.KnowledgeSearchAPIView.as_view(), name="knowledge-search"),
    path("usage/", views.KnowledgeUsageAPIView.as_view(), name="knowledge-usage"),
    path("details/", views.KnowledgeDetailAPIView.as_view(), name="knowledge-detail"),
    path("update/", views.KnowledgeUpdateAPIView.as_view(), name="knowledge-update"),
    path("delete/", views.KnowledgeDeleteAPIView.as_view(), name="knowledge-delete"),
    path("training-logs/", views.KnowledgeTrainingListAPIView.as_view(), name="knowledge-training-list"),
]
