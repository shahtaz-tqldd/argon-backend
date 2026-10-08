from django.urls import include, path

from workspace.api.v1.client import views

urlpatterns = [
    path("", views.WorkspaceDetailView.as_view(), name="workspace-detail"),
    path("list/", views.WorkspaceListView.as_view(), name="workspace-list"),
    path("create/", views.WorkspaceCreateView.as_view(), name="workspace-create"),
    path("update/", views.WorkspaceUpdateView.as_view(), name="workspace-update"),
    path("delete/", views.WorkspaceDeleteView.as_view(), name="workspace-delete"),
]
