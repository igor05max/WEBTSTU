from django.urls import path

from apps.submissions import views

app_name = "submissions"

urlpatterns = [
    path("", views.submission_list, name="list"),
    path("new/", views.submission_create, name="create"),
    path("extract-metadata/", views.extract_submission_metadata_view, name="extract_metadata"),
    path("<int:pk>/", views.submission_detail, name="detail"),
    path(
        "<int:pk>/conclusions/<int:conclusion_pk>/download/",
        views.submission_conclusion_document_download,
        name="conclusion_download",
    ),
    path(
        "<int:pk>/conclusions/<int:conclusion_pk>/files/<str:file_kind>/",
        views.submission_conclusion_package_file_download,
        name="conclusion_package_file",
    ),
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
    path(
        "<int:pk>/versions/<int:version_pk>/download/",
        views.submission_version_download,
        name="version_download",
    ),
    path("<int:pk>/progress/", views.submission_progress_view, name="progress"),
    path("<int:pk>/checks-report/", views.submission_checks_report_view, name="checks_report"),
    path(
        "<int:pk>/formatting-rules/",
        views.update_formatting_rules_view,
        name="update_formatting_rules",
    ),
    path(
        "<int:pk>/latex-template/",
        views.submission_latex_template_download_view,
        name="latex_template_download",
    ),
    path(
        "<int:pk>/corrected-document/",
        views.corrected_document_download_view,
        name="corrected_document_download",
    ),
    path(
        "<int:pk>/corrected-document/preview/<int:version_pk>/",
        views.corrected_document_preview_view,
        name="corrected_document_preview",
    ),
    path(
        "<int:pk>/corrected-document/preview/<int:version_pk>/content/",
        views.corrected_document_preview_content_view,
        name="corrected_document_preview_content",
    ),
    path(
        "<int:pk>/corrected-document/preview/<int:version_pk>/submit/",
        views.submit_corrected_document_for_check_view,
        name="submit_corrected_document_for_check",
    ),
    path("<int:pk>/upload-version/", views.upload_submission_version, name="upload_version"),
    path("<int:pk>/submit/", views.submit_submission_view, name="submit"),
    path("<int:pk>/update-route/", views.update_submission_route_view, name="update_route"),
    path("<int:pk>/appeal/", views.submit_submission_appeal_view, name="submit_appeal"),
    path("<int:pk>/appeal/approve/", views.approve_submission_appeal_view, name="approve_appeal"),
    path("<int:pk>/appeal/reject/", views.reject_submission_appeal_view, name="reject_appeal"),
]
