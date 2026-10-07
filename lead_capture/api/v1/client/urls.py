from django.urls import include, path

from lead_capture.api.v1.client import views

lead_config_apis = [
    path("", views.LeadCaptureConfigAPIView.as_view(), name="lead-config"),
    path("create/", views.LeadCaptureConfigCreateAPIView.as_view(),name="lead-config-create"),
    path("update/", views.LeadCaptureConfigUpdateAPIView.as_view(),name="lead-config-update"),
]

lead_apis = [
    path("list/", views.LeadListView.as_view(), name="lead-list"),
    path("details/", views.LeadDetailView.as_view(), name="lead-detail"),
    path("update/", views.LeadUpdateView.as_view(), name="lead-update"),
    path("signal-history/", views.LeadSignalHistoryAPIView.as_view(), name="lead-signal-history"),
    path("export/", views.ExportLeadAPIView.as_view(), name="export-lead-data"),
    path("growth/", views.LeadGrowthAPIView.as_view(), name="lead-growth"),
    path("insights/", views.LeadAIInsightListView.as_view(), name="lead-ai-insight-list"),
]

lead_signal_apis = [
    path("list/", views.LeadSignalListAPIView.as_view(), name="lead-signal-leads"),
    path("stats/", views.LeadSignalStatsAPIView.as_view(), name="lead-signal-stats"),
]

lead_note_apis = [
    path("list/", views.LeadNoteListView.as_view(), name="lead-note-list"),
    path("create/", views.LeadNoteCreateView.as_view(), name="lead-note-create"),
    path("details/", views.LeadNoteDetailView.as_view(), name="lead-note-detail"),
    path("update/", views.LeadNoteUpdateView.as_view(), name="lead-note-update"),
    path("delete/", views.LeadNoteDeleteView.as_view(), name="lead-note-delete"),
]


urlpatterns = [
    path("config/", include(lead_config_apis)),
    path("leads/", include(lead_apis)),
    path("notes/", include(lead_note_apis)),
    path("signals/", include(lead_signal_apis)),
]
