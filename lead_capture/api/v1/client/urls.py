from django.urls import include, path

from lead_capture.api.v1.client import views

lead_config = [
    path("", views.LeadCaptureConfigAPIView.as_view(), name="lead-config"),
    path("create/", views.LeadCaptureConfigCreateAPIView.as_view(),name="lead-config-create"),
    path("update/", views.LeadCaptureConfigUpdateAPIView.as_view(),name="lead-config-update"),
]

leads = [
    path("list/", views.LeadListView.as_view(), name="lead-list"),
    path("details/", views.LeadDetailView.as_view(), name="lead-detail"),
    path("update/", views.LeadUpdateView.as_view(), name="lead-update"),
    path("export/", views.ExportLeadAPIView.as_view(), name="export-lead-data"),
    path("stats/", views.LeadStatsAPIView.as_view(), name="lead-stats"),
]

lead_notes = [
    path("list/", views.LeadNoteListView.as_view(), name="lead-note-list"),
    path("create/", views.LeadNoteCreateView.as_view(), name="lead-note-create"),
    path("details/", views.LeadNoteDetailView.as_view(), name="lead-note-detail"),
    path("update/", views.LeadNoteUpdateView.as_view(), name="lead-note-update"),
    path("delete/", views.LeadNoteDeleteView.as_view(), name="lead-note-delete"),
]

urlpatterns = [
    path("config/", include(lead_config)),
    path("leads/", include(leads)),
    path("notes/", include(lead_notes)),
]
