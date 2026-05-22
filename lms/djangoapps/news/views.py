from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from common.djangoapps.edxmako.shortcuts import render_to_response
from .models import News
from .forms import NewsForm
from django.contrib.auth.decorators import user_passes_test
from common.djangoapps.student.models import UserProfile

import json
from django.db.models import Count
from django.db.models import Count, OuterRef, Subquery
from django.utils import timezone
from django.db.models.functions import ExtractYear
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
# from ..commerce.api.v1.models import Course
import logging
import jwt
import urllib.parse
import random
from datetime import datetime, timedelta

from django.http import HttpResponseRedirect

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
logger = logging.getLogger(__name__)

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


def analyze(request):
    course_org_filter = ["Test_kaznu", "rty", "123"]

    now = timezone.now()
    today = now.date()
    current_year = today.year

    max_valid_end = now + timedelta(days=366)

    base_courses_qs = (
        CourseOverview.objects
        .exclude(org__in=course_org_filter)

        # курс уже должен быть запущен
        .exclude(start__isnull=True)
        .exclude(start__gt=now)

        # курс еще не должен закончиться
        .exclude(end__isnull=True)
        .exclude(end__lt=now)

        # скрываем слишком долгие/ошибочные курсы, например до 2028 года
        .exclude(end__gt=max_valid_end)

        # убираем пустые названия
        .exclude(display_name__isnull=True)
        .exclude(display_name="")
    )

    latest_course_ids = (
        base_courses_qs
        .filter(display_name=OuterRef("display_name"))
        .order_by("-start", "-id")
        .values("id")[:1]
    )

    courses_qs = (
        base_courses_qs
        .filter(id__in=Subquery(latest_course_ids))
    )

    current_year_courses_qs = (
        courses_qs
        .exclude(start__isnull=True)
        .filter(start__year=current_year)
        .order_by("-start", "display_name")
    )

    courses_by_faculty = (
        courses_qs
        .exclude(faculty__isnull=True)
        .exclude(faculty="")
        .values("faculty")
        .annotate(total=Count("id"))
        .order_by("-total", "faculty")[:12]
    )

    courses_by_directions = (
        courses_qs
        .exclude(directions__isnull=True)
        .exclude(directions="")
        .values("directions")
        .annotate(total=Count("id"))
        .order_by("-total", "directions")[:12]
    )

    courses_by_year = (
        courses_qs
        .exclude(start__isnull=True)
        .annotate(year=ExtractYear("start"))
        .values("year")
        .annotate(total=Count("id"))
        .order_by("year")
    )

    courses_by_lang = (
        courses_qs
        .exclude(language__isnull=True)
        .exclude(language="")
        .values("language")
        .annotate(total=Count("id"))
        .order_by("-total", "language")
    )

    top_courses = list(current_year_courses_qs[:50])
    courses_json = [
        {
            "id": str(course.id),
            "display_name": course.display_name or str(course.id),
            "faculty": course.faculty or "",
            "directions": course.directions or "",
            "language": course.language or "",
            "start": course.start.strftime("%d.%m.%Y") if course.start else "",
            "url": "/courses/{}/about".format(course.id),
        }
        for course in top_courses
    ]

    context = {
        "courses_count": courses_qs.count(),
        "current_year": current_year,
        "current_year_courses_count": current_year_courses_qs.count(),
        "faculty_count": courses_qs.exclude(faculty__isnull=True).exclude(faculty="").values("faculty").distinct().count(),
        "directions_count": courses_qs.exclude(directions__isnull=True).exclude(directions="").values("directions").distinct().count(),
        "generated_at": timezone.localtime().strftime("%d.%m.%Y %H:%M"),

        "language_summary": [
            {"label": row["language"], "total": row["total"]}
            for row in courses_by_lang
        ],

        "faculty_labels": json.dumps([row["faculty"] for row in courses_by_faculty], ensure_ascii=False),
        "faculty_data": json.dumps([row["total"] for row in courses_by_faculty]),

        "directions_labels": json.dumps([row["directions"] for row in courses_by_directions], ensure_ascii=False),
        "directions_data": json.dumps([row["total"] for row in courses_by_directions]),

        "year_labels": json.dumps([row["year"] for row in courses_by_year]),
        "year_data": json.dumps([row["total"] for row in courses_by_year]),

        "courses_json": json.dumps(courses_json, ensure_ascii=False),
    }

    return render_to_response("news/analyze.html", context, request=request)



PROCTORING_URL = "https://farabi-proctoring.kaznu.kz/integration/simple/kaznu_open/start/"


def go_to_exam(request):
    SECRET_KEY = str(settings.PROCTORING_API_KEY)
    # ✅ 1. Берем данные из frontend
    user_id = request.user.id
    username = request.user.username
    unit_url = request.GET.get("unit_url", "/")
    course_name = request.GET.get("course_name", "empty")
    section_name = request.GET.get("section_name", "empty")


    # можно взять реальные данные если нужно
    try:
        name = request.user.profile.name.strip()
        parts = name.split(maxsplit=1)

        firstname = parts[0] if len(parts) > 0 else ""
        lastname = parts[1] if len(parts) > 1 else ""
    except:
        firstname, lastname = "None", "None"

    exam_id = random.randint(10**7, 10**8 - 1)
    session_id = random.randint(10**10, 10**11 - 1)
    exam_name = f"Экзамен по {course_name}"
    request.session["proctoring_session_id"] = session_id
    logger.info(f"my-log: exam start {session_id}")


    # 2. Время
    now = datetime.utcnow()
    start_iso = now.isoformat() + "Z"
    end_iso = (now + timedelta(hours=2)).isoformat() + "Z"

    # ✅ 3. Payload с динамическим возвратом
    payload = {
        "userId": user_id,
        "lastName": lastname,
        "firstName": firstname,
        "thirdName": username,
        "language": "ru",
        "accountName": "kaznu_open",
        "examId": exam_id,
        "examName": exam_name,
        "duration": 30,
        "schedule": False,
        "proctoring": "online",
        "examDesc": f"<b>Курс:</b> {course_name}<br><b>Модуль:</b> {section_name}<br><br>Ссылка на задание</b> {unit_url}",
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

    logger.info(f"my-log: request get {request.GET}")

    return HttpResponseRedirect(final_url)


def finish_exam(request):
    session_id = request.session.get("proctoring_session_id")
    logger.info(f"my-log: exam finished {session_id}")

    redirect_url = request.GET.get("redirectUrl", "/")
    logger.info(f"my-log: exam finished {redirect_url}")

    url = f"https://farabi-proctoring.kaznu.kz/integration/simple/kaznu_open/finish/{session_id}/?redirectUrl={redirect_url}"

    return HttpResponseRedirect(url)
