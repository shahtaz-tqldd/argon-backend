from django.urls import path

from lead_capture.api.v1.client import views


urlpatterns = [
    path("config/", views.LeadCaptureConfigView.as_view(), name="lead-config"),
    path("leads/", views.LeadListView.as_view(), name="lead-list"),
    path("lead/", views.LeadDetailView.as_view(), name="lead-detail"),
    path("notes/", views.LeadNoteView.as_view(), name="lead-note-list-create"),
]
