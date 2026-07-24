from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.accounts import views as account_views

urlpatterns = [
    path("", account_views.dashboard, name="home"),
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("register/", account_views.register, name="register"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("authors/", account_views.author_directory, name="author_directory"),
    path("authors/<int:pk>/", account_views.author_profile, name="author_profile"),
    path("profile/", account_views.author_profile, {"pk": None}, name="profile"),
    path("directory/", include("apps.directory.urls")),
    path("activities/", include("apps.activities.urls")),
    path("submissions/", include("apps.submissions.urls")),
    path("workflow/", include("apps.workflow.urls")),
    path("settings/", include("apps.checks.urls")),
    path("citations/", include("apps.citations.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
