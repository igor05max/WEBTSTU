from django.urls import path
from . import views

app_name = "template_workspace"
urlpatterns = [
    path("", views.workspace, name="workspace"),
    path("<uuid:job_id>/", views.detail, name="detail"),
    path("<uuid:job_id>/progress/", views.progress, name="progress"),
    path("<uuid:job_id>/files/<str:kind>/", views.download, name="download"),
]
