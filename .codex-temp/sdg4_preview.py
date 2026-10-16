"""Loopback-only preview of the real showcase with disposable SQLite fixtures."""
import os
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
package = ModuleType("lms")
package.__path__ = [str(ROOT / "lms")]
package.__file__ = str(ROOT / "lms/__init__.py")
sys.modules["lms"] = package
os.environ["DJANGO_SETTINGS_MODULE"] = "lms.djangoapps.news.sdg4.tests.settings"

import django
from django.conf import settings

settings.DATABASES["default"]["NAME"] = str(ROOT / ".codex-temp/sdg4-preview.sqlite3")
settings.MEDIA_ROOT = str(ROOT / ".codex-temp/sdg4-preview-media")
settings.LOCALE_PATHS = [str(ROOT / ".codex-temp/sdg4-locales")]
settings.DEBUG = True
django.setup()

from django.contrib.auth import get_user_model
from django.core.handlers.wsgi import WSGIHandler
from django.core.management import call_command
from django.core.servers.basehttp import run
from django.http import HttpResponse
from django.test import Client
from django.urls import path
from django.views.static import serve

from lms.djangoapps.news.sdg4 import views
from lms.djangoapps.news.sdg4.tests.test_showcase import payload, template_renderer
from lms.djangoapps.news.sdg4.tests import urls
from lms.djangoapps.news.models import News

call_command("migrate", verbosity=0)
editor, _created = get_user_model().objects.get_or_create(username="preview-editor", defaults={"is_staff": True, "is_superuser": True})
views.render = template_renderer
client = Client()
client.force_login(editor)
if not News.objects.exists():
    data = payload()
    data.update(name_ru="Иванова Алия Сергеевна", name_kk="Иванова Әлия Сергеевна", name_en="Aliya Ivanova")
    for lang in ("ru", "kk", "en"):
        data[f"position_{lang}"] = {"ru": "Профессор", "kk": "Профессор", "en": "Professor"}[lang]
        data[f"degree_{lang}"] = "PhD"
        data[f"department_{lang}"] = {"ru": "Кафедра информационных систем", "kk": "Ақпараттық жүйелер кафедрасы", "en": "Department of Information Systems"}[lang]
    for slot in range(1, 4):
        for lang in ("ru", "kk", "en"):
            data[f"course-{slot}-topic_{lang}"] = {"ru": "Введение в анализ данных", "kk": "Деректерді талдауға кіріспе", "en": "Introduction to Data Analysis"}[lang]
            data[f"course-{slot}-discipline_{lang}"] = {"ru": "Анализ данных", "kk": "Деректерді талдау", "en": "Data Analysis"}[lang]
            data[f"course-{slot}-annotation_{lang}"] = {"ru": "Открытый курс знакомит с основами анализа данных и практическими методами работы с информацией.", "kk": "Ашық курс деректерді талдау негіздерімен және ақпаратпен жұмыс істеудің практикалық әдістерімен таныстырады.", "en": "An open course introducing the foundations of data analysis and practical methods for working with information."}[lang]
        data[f"materials-{slot}-0-title"] = f"{slot}. Данные и информация"
    response = client.post("/sdg4/manage/new/", data)
    assert response.status_code == 302, response.content.decode()


def preview_editor(request):
    if request.method != "GET":
        return HttpResponse(status=405)
    request.user = editor
    return views.edit(request)


def mobile(request):
    return HttpResponse('<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:#ddd"><iframe title="Mobile preview" src="/sdg4/" style="width:390px;height:1450px;border:0;background:white"></iframe></body></html>')


urls.urlpatterns += [
    path("static/<path:path>", serve, {"document_root": ROOT / "lms/static"}),
    path("media/<path:path>", serve, {"document_root": settings.MEDIA_ROOT}),
    path("preview-editor/", preview_editor),
    path("preview-mobile/", mobile),
]
print("Preview: http://127.0.0.1:8766/sdg4/", flush=True)
run("127.0.0.1", 8766, WSGIHandler())
