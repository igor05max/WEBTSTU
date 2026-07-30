from django.urls import path

from apps.checks import views


app_name = "checks"

urlpatterns = [
    path("ai/", views.ai_settings, name="ai_settings"),
]
