from django.urls import path

from apps.directory import views

app_name = "directory"

urlpatterns = [
    path("journals/search/", views.journal_search, name="journal_search"),
]
