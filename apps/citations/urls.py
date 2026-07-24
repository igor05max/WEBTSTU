from django.urls import path

from apps.citations import views


app_name = "citations"

urlpatterns = [
    path("", views.workspace, name="workspace"),
    path("apply/", views.apply_citations, name="apply"),
    path("index-status/", views.index_status, name="index_status"),
]
