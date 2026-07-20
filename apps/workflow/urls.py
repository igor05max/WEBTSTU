from django.urls import path

from apps.workflow import views

app_name = "workflow"

urlpatterns = [
    path("inbox/", views.inbox, name="inbox"),
    path("assignment-options/", views.assignment_options_view, name="assignment_options"),
    path("tasks/<int:pk>/", views.task_detail, name="task_detail"),
    path("tasks/<int:pk>/approve/", views.approve_task_view, name="approve_task"),
    path("tasks/<int:pk>/request-revision/", views.request_revision_view, name="request_revision"),
    path("tasks/<int:pk>/reject/", views.reject_task_view, name="reject_task"),
]
