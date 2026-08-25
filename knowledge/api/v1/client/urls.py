from django.urls import path

from knowledge.api.v1.client import views

urlpatterns = [
    path("upload/", views.KnowledgeUploadView.as_view(), name="knowledge-upload"),
    path("list/", views.KnowledgeListView.as_view(), name="knowledge-list"),
    path("details/", views.KnowledgeDetailView.as_view(), name="knowledge-detail"),
    path("update/", views.KnowledgeUpdateView.as_view(), name="knowledge-update"),
    path("delete/", views.KnowledgeDeleteView.as_view(), name="knowledge-delete"),
    path("training-logs/", views.KnowledgeTrainingListView.as_view(), name="knowledge-training-list"),
]
