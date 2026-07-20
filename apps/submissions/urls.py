from django.urls import path

from apps.submissions import views

app_name = "submissions"

urlpatterns = [
    path("", views.submission_list, name="list"),
    path("new/", views.submission_create, name="create"),
    path("extract-metadata/", views.extract_submission_metadata_view, name="extract_metadata"),
    path("<int:pk>/", views.submission_detail, name="detail"),
    path("<int:pk>/delete-draft/", views.delete_submission_draft_view, name="delete_draft"),
    path(
        "<int:pk>/versions/<int:version_pk>/preview/",
        views.submission_version_preview,
        name="version_preview",
    ),
    path(
        "<int:pk>/versions/<int:version_pk>/content/",
        views.submission_version_content,
        name="version_content",
    ),
    path("<int:pk>/progress/", views.submission_progress_view, name="progress"),
    path("<int:pk>/checks-report/", views.submission_checks_report_view, name="checks_report"),
    path("<int:pk>/upload-version/", views.upload_submission_version, name="upload_version"),
    path("<int:pk>/submit/", views.submit_submission_view, name="submit"),
    path("<int:pk>/update-route/", views.update_submission_route_view, name="update_route"),
    path("<int:pk>/appeal/", views.submit_submission_appeal_view, name="submit_appeal"),
    path("<int:pk>/appeal/approve/", views.approve_submission_appeal_view, name="approve_appeal"),
    path("<int:pk>/appeal/reject/", views.reject_submission_appeal_view, name="reject_appeal"),
]
