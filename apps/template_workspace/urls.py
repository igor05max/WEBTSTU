from django.urls import path
from . import views
from .v2 import views as v2_views

app_name = "template_workspace"
urlpatterns = [
    path("style/jamt/", v2_views.workspace, {"job_kind": "jamt"}, name="jamt_workspace"),
    path("style/jamt/<uuid:job_id>/", v2_views.detail, {"job_kind": "jamt"}, name="jamt_detail"),
    path("style/jamt/<uuid:job_id>/progress/", v2_views.progress, {"job_kind": "jamt"}, name="jamt_progress"),
    path("style/jamt/<uuid:job_id>/files/<str:kind>/", v2_views.download, {"job_kind": "jamt"}, name="jamt_download"),
    path("v2/", v2_views.workspace, name="v2_workspace"),
    path("v2/<uuid:job_id>/", v2_views.detail, name="v2_detail"),
    path("v2/<uuid:job_id>/progress/", v2_views.progress, name="v2_progress"),
    path("v2/<uuid:job_id>/files/<str:kind>/", v2_views.download, name="v2_download"),
    path("", views.workspace, name="workspace"),
    path("<uuid:job_id>/", views.detail, name="detail"),
    path("<uuid:job_id>/progress/", views.progress, name="progress"),
    path("<uuid:job_id>/files/<str:kind>/", views.download, name="download"),
]
