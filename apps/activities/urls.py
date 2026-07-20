from django.urls import path

from apps.activities import views

app_name = "activities"

urlpatterns = [
    path("", views.activity_list, name="list"),
    path("matrix/", views.activity_matrix, name="matrix"),
    path("statistics/", views.activity_statistics, name="statistics"),
    path("new/", views.activity_create, name="create"),
    path("<int:pk>/edit/", views.activity_edit, name="edit"),
    path("<int:pk>/delete/", views.activity_delete, name="delete"),
]
