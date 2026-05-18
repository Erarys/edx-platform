from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from common.djangoapps.edxmako.shortcuts import render_to_response
from .models import News
from .forms import NewsForm
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count
from common.djangoapps.student.models import UserProfile

from django.db.models import Count
from django.db.models.functions import ExtractYear
import json
from django.db.models import Count
from django.db.models.functions import ExtractYear
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
# from ..commerce.api.v1.models import Course
import logging
import jwt
import urllib.parse
import random
from datetime import datetime, timedelta, timezone

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
    today = timezone.now().date()

    courses_qs = (
        CourseOverview.objects
        .exclude(org__in=course_org_filter)
        .exclude(start__gt=today)
    )
    courses_by_org = (
        courses_qs
        .values("org")
        .annotate(total=Count("id"))
        .order_by("-total")
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
        .values("language")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    paced_data = [
        courses_qs.filter(self_paced=True).count(),
        courses_qs.filter(self_paced=False).count(),
    ]

    context = {
        # список курсов (НЕ для JS — оставляем как есть)
        "courses": courses_qs[:50],
        "courses_count": courses_qs.count(),

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
