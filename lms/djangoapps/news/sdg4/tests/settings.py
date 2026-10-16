"""Run: python -m django test lms.djangoapps.news.sdg4.tests --settings=...tests.settings."""

SECRET_KEY = "sdg4-local-tests-only"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "lms.djangoapps.news",
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
ROOT_URLCONF = "lms.djangoapps.news.sdg4.tests.urls"
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
USE_TZ = True
LANGUAGE_CODE = "en"
LANGUAGES = [("kk", "Kazakh"), ("ru", "Russian"), ("en", "English")]
MEDIA_URL = "/media/"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True}]
