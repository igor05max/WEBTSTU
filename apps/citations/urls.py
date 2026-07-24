from django.urls import path

from apps.citations import views


app_name = "citations"

urlpatterns = [
    path("", views.workspace, name="workspace"),
    path("apply/", views.apply_citations, name="apply"),
    path("submission-result/prepare/", views.prepare_submission_result, name="prepare_submission_result"),
    path(
        "submission-result/<str:token>/",
        views.submission_result_preview,
        name="submission_result_preview",
    ),
    path(
        "submission-result/<str:token>/content/",
        views.submission_result_content,
        name="submission_result_content",
    ),
    path(
        "submission-result/<str:token>/download/",
        views.submission_result_download,
        name="submission_result_download",
    ),
    path(
        "submission-result/<str:token>/use/",
        views.use_submission_result,
        name="use_submission_result",
    ),
    path("index-status/", views.index_status, name="index_status"),
]
