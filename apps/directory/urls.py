from django.urls import path

from apps.directory import views

app_name = "directory"

urlpatterns = [
    path("journals/search/", views.journal_search, name="journal_search"),
    path("publication-topics/search/", views.publication_topic_search, name="publication_topic_search"),
    path(
        "formatting-templates/<int:pk>/",
        views.formatting_template_detail,
        name="formatting_template_detail",
    ),
    path(
        "formatting-templates/<int:pk>/download/",
        views.formatting_template_download,
        name="formatting_template_download",
    ),
    path(
        "formatting-templates/<int:pk>/latex/",
        views.formatting_template_latex_download,
        name="formatting_template_latex_download",
    ),
    path(
        "formatting-templates/<int:pk>/latex/preview/",
        views.formatting_template_latex_preview,
        name="formatting_template_latex_preview",
    ),
]
