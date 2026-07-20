from django.urls import path

from apps.checks import views


app_name = "checks"

urlpatterns = [
    path("gemini/", views.gemini_settings, name="gemini_settings"),
]
