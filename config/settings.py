import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_local_env(env_path):
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _read_int_tuple(env_name):
    raw_value = os.getenv(env_name, "")
    result = []
    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError:
            continue
    return tuple(result)


_load_local_env(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-dev-only-key")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ROOT_ADMIN_USERNAME = os.getenv("DJANGO_ROOT_ADMIN_USERNAME", "rootUser")
DEFAULT_USER_PASSWORD = os.getenv("DJANGO_DEFAULT_USER_PASSWORD", "1234")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

DJANGO_APPS = [
    "django.contrib.admin",
    "apps.accounts.auth_apps.AuthRuConfig",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

LOCAL_APPS = [
    "apps.accounts.apps.AccountsConfig",
    "apps.directory.apps.DirectoryConfig",
    "apps.activities.apps.ActivitiesConfig",
    "apps.submissions.apps.SubmissionsConfig",
    "apps.workflow.apps.WorkflowConfig",
    "apps.checks.apps.ChecksConfig",
    "apps.conclusions.apps.ConclusionsConfig",
]

INSTALLED_APPS = [
    *DJANGO_APPS,
    *LOCAL_APPS,
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.user_shell_context",
                "apps.workflow.context_processors.workflow_notifications",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASE_ENGINE = os.getenv("DJANGO_DATABASE_ENGINE", "sqlite").lower()

if DATABASE_ENGINE in {"postgres", "postgresql"}:
    postgres_db = os.getenv("POSTGRES_DB")
    if not postgres_db:
        raise ImproperlyConfigured(
            "POSTGRES_DB must be set when DJANGO_DATABASE_ENGINE=postgres."
        )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": postgres_db,
            "USER": os.getenv("POSTGRES_USER", "postgres"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }
elif DATABASE_ENGINE == "sqlite":
    SQLITE_TIMEOUT_SECONDS = int(os.getenv("DJANGO_SQLITE_TIMEOUT_SECONDS", "30"))
    SQLITE_TRANSACTION_MODE = os.getenv("DJANGO_SQLITE_TRANSACTION_MODE", "IMMEDIATE").upper()
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            # Background checks use a separate process. Waiting for the current
            # writer avoids transient "database is locked" errors in the UI.
            "OPTIONS": {
                "timeout": SQLITE_TIMEOUT_SECONDS,
                "transaction_mode": SQLITE_TRANSACTION_MODE,
            },
        }
    }
else:
    raise ImproperlyConfigured(
        "DJANGO_DATABASE_ENGINE must be either 'sqlite' or 'postgres'."
    )

ENFORCE_STRONG_PASSWORDS = os.getenv(
    "DJANGO_ENFORCE_STRONG_PASSWORDS",
    "0" if DEBUG else "1",
) == "1"

if ENFORCE_STRONG_PASSWORDS:
    AUTH_PASSWORD_VALIDATORS = [
        {
            "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
        },
        {
            "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        },
        {
            "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
        },
        {
            "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
        },
    ]
else:
    AUTH_PASSWORD_VALIDATORS = []

AUTH_USER_MODEL = "accounts.User"
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DOCUMENT_PREVIEW_CONVERT_DOCX_TO_PDF = os.getenv(
    "DOCUMENT_PREVIEW_CONVERT_DOCX_TO_PDF",
    "1",
) == "1"
LIBREOFFICE_BINARY = os.getenv("LIBREOFFICE_BINARY", "").strip()
ARTICLE_RECOMMENDATION_CORPUS_ROOT = Path(
    os.getenv(
        "ARTICLE_RECOMMENDATION_CORPUS_ROOT",
        str(BASE_DIR / "downloads_2022"),
    )
)
ARTICLE_RECOMMENDATION_LIMIT = int(os.getenv("ARTICLE_RECOMMENDATION_LIMIT", "5"))
ARTICLE_RECOMMENDATION_MIN_SCORE = float(os.getenv("ARTICLE_RECOMMENDATION_MIN_SCORE", "0.12"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ARTICLE_RECOMMENDATION_USE_EMBEDDINGS = os.getenv(
    "ARTICLE_RECOMMENDATION_USE_EMBEDDINGS",
    "1",
) == "1"
ARTICLE_RECOMMENDATION_EMBEDDING_MODEL = os.getenv(
    "ARTICLE_RECOMMENDATION_EMBEDDING_MODEL",
    "text-embedding-3-small",
)
ARTICLE_RECOMMENDATION_EMBEDDING_DIMENSIONS = os.getenv(
    "ARTICLE_RECOMMENDATION_EMBEDDING_DIMENSIONS",
    "",
).strip()
ARTICLE_RECOMMENDATION_EMBEDDING_BATCH_SIZE = int(
    os.getenv("ARTICLE_RECOMMENDATION_EMBEDDING_BATCH_SIZE", "32")
)
ARTICLE_RECOMMENDATION_EMBEDDING_CACHE_PATH = Path(
    os.getenv(
        "ARTICLE_RECOMMENDATION_EMBEDDING_CACHE_PATH",
        str(BASE_DIR / "tmp_previews" / "article_embedding_cache.json"),
    )
)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODELS_TIMEOUT = max(30, int(os.getenv("GEMINI_MODELS_TIMEOUT", "30")))
GEMINI_REQUEST_TIMEOUT = int(os.getenv("GEMINI_REQUEST_TIMEOUT", "60"))
SUBMISSION_ROUTE_SUGGESTION_ENABLED = os.getenv(
    "SUBMISSION_ROUTE_SUGGESTION_ENABLED",
    "1",
) == "1"
SUBMISSION_ROUTE_SUGGESTION_MODEL = os.getenv(
    "SUBMISSION_ROUTE_SUGGESTION_MODEL",
    "gemini-2.5-flash",
)
SUBMISSION_ROUTE_SUGGESTION_TIMEOUT = int(
    os.getenv("SUBMISSION_ROUTE_SUGGESTION_TIMEOUT", str(GEMINI_REQUEST_TIMEOUT))
)
SUBMISSION_CHECKS_ASYNC = os.getenv("SUBMISSION_CHECKS_ASYNC", "1") == "1"
SUBMISSION_FILE_MAX_SIZE = int(
    os.getenv("SUBMISSION_FILE_MAX_SIZE", str(50 * 1024 * 1024))
)
SUBMISSION_CONTENT_REVIEW_ENABLED = os.getenv(
    "SUBMISSION_CONTENT_REVIEW_ENABLED",
    "1",
) == "1"
SUBMISSION_CONTENT_REVIEW_MODEL = os.getenv(
    "SUBMISSION_CONTENT_REVIEW_MODEL",
    SUBMISSION_ROUTE_SUGGESTION_MODEL,
)
SUBMISSION_CONTENT_REVIEW_TIMEOUT = int(
    os.getenv("SUBMISSION_CONTENT_REVIEW_TIMEOUT", "60")
)
SUBMISSION_CONTENT_REVIEW_EXCERPT_LIMIT = int(
    os.getenv("SUBMISSION_CONTENT_REVIEW_EXCERPT_LIMIT", "60000")
)
SUBMISSION_PROGRESS_POLL_INTERVAL_MS = int(
    os.getenv("SUBMISSION_PROGRESS_POLL_INTERVAL_MS", "2000")
)
SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS = _read_int_tuple(
    "SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS"
)
CONCLUSION_TEMPLATE_PATH = Path(
    os.getenv(
        "CONCLUSION_TEMPLATE_PATH",
        str(BASE_DIR / "apps" / "conclusions" / "assets" / "conclusion_template.docx"),
    )
)
CONCLUSION_REGISTRATION_PREFIX = os.getenv("CONCLUSION_REGISTRATION_PREFIX", "ЗОП").strip() or "ЗОП"
PLANNING_ROSTER_SOURCE_ROOT = Path(
    os.getenv(
        "PLANNING_ROSTER_SOURCE_ROOT",
        str(BASE_DIR / "2025_2026" / "2025_2026"),
    )
)
PLANNING_ROSTER_ACADEMIC_YEAR = os.getenv("PLANNING_ROSTER_ACADEMIC_YEAR", "2025/2026")
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
