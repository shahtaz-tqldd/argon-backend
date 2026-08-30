from django.urls import include, path

from lead_capture.api.v1.client import views

config_patterns = [
    path("", views.LeadCaptureConfigAPIView.as_view(), name="lead-config"),
    path(
        "create/",
        views.LeadCaptureConfigCreateAPIView.as_view(),
        name="lead-config-create",
    ),
    path(
        "update/",
        views.LeadCaptureConfigUpdateAPIView.as_view(),
        name="lead-config-update",
    ),
]

lead_patterns = [
    path("list/", views.LeadListView.as_view(), name="lead-list"),
    path("details/", views.LeadDetailView.as_view(), name="lead-detail"),
    path("update/", views.LeadUpdateView.as_view(), name="lead-update"),
]

lead_note_patterns = [
    path("list/", views.LeadNoteListView.as_view(), name="lead-note-list"),
    path("create/", views.LeadNoteCreateView.as_view(), name="lead-note-create"),
    path("details/", views.LeadNoteDetailView.as_view(), name="lead-note-detail"),
    path("update/", views.LeadNoteUpdateView.as_view(), name="lead-note-update"),
    path("delete/", views.LeadNoteDeleteView.as_view(), name="lead-note-delete"),
]

urlpatterns = [
    path("config/", include(config_patterns)),
    path("leads/", include(lead_patterns)),
    path("notes/", include(lead_note_patterns)),
]
