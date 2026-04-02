from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from common.djangoapps.edxmako.shortcuts import render_to_response
from .models import News
from .forms import NewsForm
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count


# from ..commerce.api.v1.models import Course


def news_list(request):
    news = News.objects.all()
    context = {
        'news_list': news,
        'create_url': reverse('news_create'),
    }
    return render_to_response('news/list.html', context, request=request)

def news_detail(request, news_id):  # используем news_id
    news = get_object_or_404(News, pk=news_id)  # все равно используем pk для поиска
    context = {
        'news': news,
        'list_url': reverse('news_list'),
    }
    return render_to_response('news/detail.html', context, request=request)


@user_passes_test(lambda u: u.is_staff)
def news_create(request):
    if request.method == 'POST':
        form = NewsForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('news_list')
    else:
        form = NewsForm()

    context = {
        'form': form,
        'list_url': reverse('news_list'),
    }
    return render_to_response('news/form.html', context, request=request)

from django.db.models import Count
from django.db.models.functions import ExtractYear


def analyze(request):
    courses = CourseOverview.objects.all()

    courses_by_org = (
        CourseOverview.objects
        .values("org")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    context = {
        "courses": courses[:50],
        "courses_count": courses.count(),
        "chart_labels": [c["org"] for c in courses_by_org],
        "chart_data": [c["total"] for c in courses_by_org],
    }

    return render_to_response("news/analyze2.html", context, request=request)

import json

import json
from django.db.models import Count
from django.db.models.functions import ExtractYear
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


def analyze(request):
    courses = CourseOverview.objects.all()

    courses_by_org = (
        CourseOverview.objects
        .values("org")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    courses_by_year = (
        CourseOverview.objects
        .exclude(start__isnull=True)
        .annotate(year=ExtractYear("start"))
        .values("year")
        .annotate(total=Count("id"))
        .order_by("year")
    )

    courses_by_lang = (
        CourseOverview.objects
        .exclude(language__isnull=True)
        .values("language")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    paced_data = [
        CourseOverview.objects.filter(self_paced=True).count(),
        CourseOverview.objects.filter(self_paced=False).count(),
    ]

    context = {
        # список курсов (НЕ для JS — оставляем как есть)
        "courses": courses[:50],
        "courses_count": courses.count(),

        # факультеты
        "org_labels": json.dumps([c["org"] for c in courses_by_org]),
        "org_data": json.dumps([c["total"] for c in courses_by_org]),

        # годы
        "year_labels": json.dumps([c["year"] for c in courses_by_year]),
        "year_data": json.dumps([c["total"] for c in courses_by_year]),

        # языки
        "lang_labels": json.dumps([c["language"] for c in courses_by_lang]),
        "lang_data": json.dumps([c["total"] for c in courses_by_lang]),

        # формат
        "paced_labels": json.dumps(["Self-paced", "Instructor-led"]),
        "paced_data": json.dumps(paced_data),
    }

    return render_to_response("news/analyze3.html", context, request=request)


import jwt
import urllib.parse
import time
from datetime import datetime, timedelta

from django.http import HttpResponseRedirect


SECRET_KEY = "qaZE879dFwPO*#Pox@r$!1"
PROCTORING_URL = "https://farabi-proctoring.kaznu.kz/integration/simple/kaznu_moodle/start/"


def go_to_exam(request):
    # ✅ 1. Берем данные из frontend
    user_id = request.GET.get("user_id", "0")
    username = request.GET.get("username", "unknown")
    unit_url = request.GET.get("unit_url", "/")

    # можно взять реальные данные если нужно
    firstname = "Student"
    lastname = "User"

    exam_id = 12321312
    exam_name = "Тестовый экзамен по Python"

    # 2. Время
    now = datetime.utcnow()
    start_iso = now.isoformat() + "Z"
    end_iso = (now + timedelta(hours=2)).isoformat() + "Z"

    session_id = f"session_{user_id}_{exam_id}_{int(time.time())}"

    # ✅ 3. Payload с динамическим возвратом
    payload = {
        "userId": user_id,
        "lastName": lastname,
        "firstName": firstname,
        "thirdName": username,
        "language": "ru",
        "accountName": "kaznu_moodle",
        "examId": exam_id,
        "examName": exam_name,
        "duration": 60,
        "schedule": False,
        "proctoring": "online",
        "examDesc": "<b>Курс:</b> Тестирование систем<br><b>Преподаватель:</b> AI Assistant",
        "rules": {
            "websites": True,
            "look_away": True,
            "move_away": False,
            "voices": True,
        },
        "startDate": start_iso,
        "endDate": end_iso,
        "sessionId": session_id,

        # 🔥 ВАЖНО: возвращаем туда откуда пришли
        "sessionUrl": unit_url,
        "redirectUrl": unit_url,
    }

    # 4. JWT
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")

    # 5. URL
    encoded_token = urllib.parse.quote(token)
    final_url = f"{PROCTORING_URL}?token={encoded_token}"

    print("Redirecting user to:", final_url)
    print("Return URL:", unit_url)

    return HttpResponseRedirect(final_url)
