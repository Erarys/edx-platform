from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from common.djangoapps.edxmako.shortcuts import render_to_response
from .models import News
from .forms import NewsForm
from django.contrib.auth.decorators import user_passes_test
from common.djangoapps.student.models import UserProfile

import json
from django.utils import timezone
from collections import Counter
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


from django.db.models.functions import ExtractYear
from django.db.models import Count

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

#
from django.utils.translation import get_language

FACULTY_TRANSLATIONS = {
    "Биология и биотехнология": {
        "kk": "Биология және биотехнология",
        "en": "Biology and Biotechnology",
    },
    "Востоковедение": {
        "kk": "Шығыстану",
        "en": "Oriental Studies",
    },
    "Высшая школа экономики и бизнеса": {
        "kk": "Экономика және бизнес жоғары мектебі",
        "en": "Higher School of Economics and Business",
    },
    "Довузовское образование": {
        "kk": "Жоғары оқу орнына дейінгі білім беру",
        "en": "Pre-university Education",
    },
    "Журналистика": {
        "kk": "Журналистика",
        "en": "Journalism",
    },
    "Офис академических и цифровых инноваций": {
        "kk": "Академиялық және цифрлық инновациялар кеңсесі",
        "en": "Office of Academic and Digital Innovations",
    },
    "Информационные технологии и искусственный интеллект": {
        "kk": "Ақпараттық технологиялар және жасанды интеллект",
        "en": "Information Technology and Artificial Intelligence",
    },
    "История": {
        "kk": "Тарих",
        "en": "History",
    },
    "Кластер инжиниринга и наукоемких технологий": {
        "kk": "Инжиниринг және жоғары технологиялар кластері",
        "en": "Cluster of Engineering and High Technologies",
    },
    "Механико-математический": {
        "kk": "Механика-математика",
        "en": "Mechanics and Mathematics",
    },
    "Физико-технический": {
        "kk": "Физика-техникалық",
        "en": "Physics and Technology",
    },
    "Филологический": {
        "kk": "Филология",
        "en": "Philology",
    },
    "Философии и политологии": {
        "kk": "Философия және саясаттану",
        "en": "Philosophy and Political Science",
    },
    "Химии и химической технологии": {
        "kk": "Химия және химиялық технологиялар",
        "en": "Chemistry and Chemical Technology",
    },
    "Юридический": {
        "kk": "Заң",
        "en": "Law",
    },
    "Международных отношений": {
        "kk": "Халықаралық қатынастар",
        "en": "International Relations",
    },
    "Медицины и здравоохранения": {
        "kk": "Медицина және денсаулық сақтау",
        "en": "Medicine and Healthcare",
    },
}

DIRECTION_TRANSLATIONS = {
    "Социальные науки, журналистика и информация": {
        "kk": "Әлеуметтік ғылымдар, журналистика және ақпарат",
        "en": "Social sciences, Journalism and Information",
    },
    "Естественные науки, математика и статистика": {
        "kk": "Жаратылыстану ғылымдары, математика және статистика",
        "en": "Natural Sciences, Mathematics and Statistics",
    },
    "Искусство и гуманитарные науки": {
        "kk": "Өнер және гуманитарлық ғылымдар",
        "en": "Arts and Humanities",
    },
    "Бизнес, управление и право": {
        "kk": "Бизнес, басқару және құқық",
        "en": "Business, Management and Law",
    },
    "Педагогические науки": {
        "kk": "Педагогикалық ғылымдар",
        "en": "Pedagogical sciences",
    },
    "Информационно-коммуникационные технологии": {
        "kk": "Ақпараттық-коммуникациялық технологиялар",
        "en": "Information and communication technologies",
    },
    "Инженерные, обрабатывающие и строительные отрасли": {
        "kk": "Инженерлік, өңдеу және құрылыс салалары",
        "en": "Engineering, manufacturing and construction branches",
    },
    "Здравоохранение": {
        "kk": "Денсаулық сақтау",
        "en": "Healthcare",
    },
}


def normalize_language_code():
    language = get_language() or "ru"
    return language.split("-")[0].split("_")[0]


def translate_faculty(value):
    if not value:
        return ""

    language = normalize_language_code()

    if language == "ru":
        return value

    return FACULTY_TRANSLATIONS.get(value, {}).get(language, value)


def translate_direction(value):
    if not value:
        return ""

    language = normalize_language_code()

    if language == "ru":
        return value

    return DIRECTION_TRANSLATIONS.get(value, {}).get(language, value)
#
def analyze(request):
    course_org_filter = ["Test_kaznu", "rty", "123", "AI Tools in Action: Boosting Productivity with Modern Workflows", "Demo"]

    now = timezone.now()
    today = now.date()
    current_year = today.year

    max_valid_end = now + timedelta(days=366)

    base_courses_qs = (
        CourseOverview.objects
        .exclude(org__in=course_org_filter)

        # курс еще не должен закончиться
        # .exclude(end__isnull=True)
        # .exclude(end__lt=now)

        # скрываем слишком долгие/ошибочные курсы, например до 2028 года
        .exclude(end__gt=max_valid_end)

        # убираем пустые названия
        .filter(start__lte=now)
        .exclude(display_name__isnull=True)
        .exclude(display_name="")
        .order_by("display_name", "-start", "-id")
    )

    unique_courses = {}
    for course in base_courses_qs:
        if course.display_name not in unique_courses:
            unique_courses[course.display_name] = course

    courses = list(unique_courses.values())

    current_year_courses = [
        course for course in courses
        if course.start and course.start.year == current_year
    ]
    # Rewrite code to test it #
    current_year_courses = courses
    #
    max_year = current_year + 1

    courses_by_year_qs = (
        CourseOverview.objects
        .exclude(org__in=course_org_filter)
        .exclude(start__isnull=True)
        # .filter(start__year__lte=max_year)
        .annotate(year=ExtractYear("start"))
        .values("year")
        .annotate(total=Count("id"))
        .order_by("year")
    )
    courses_by_year = [
        {"year": row["year"], "total": row["total"]}
        for row in courses_by_year_qs
    ]

    #
    faculty_counter = Counter(
        course.faculty for course in courses
        if course.faculty
    )

    directions_counter = Counter(
        course.directions for course in courses
        if course.directions
    )

    language_counter = Counter(
        course.language for course in courses
        if course.language
    )

    courses_by_faculty = [
        {"faculty": faculty, "total": total}
        for faculty, total in faculty_counter.most_common(12)
    ]

    courses_by_directions = [
        {"directions": directions, "total": total}
        for directions, total in directions_counter.most_common(12)
    ]

    courses_by_lang = [
        {"language": language, "total": total}
        for language, total in language_counter.most_common()
    ]

    top_courses = sorted(
        current_year_courses,
        key=lambda course: course.start,
        reverse=True
    )[:50]

    course_run_counts = dict(
        CourseOverview.objects
        .exclude(org__in=course_org_filter)
        .exclude(display_name__isnull=True)
        .exclude(display_name="")
        .exclude(start__isnull=True)
        .values("display_name")
        .annotate(total=Count("id", distinct=True))
        .values_list("display_name", "total")
    )

    courses_json = [
        {
            "id": str(course.id),
            "display_name": course.display_name or str(course.id),
            "faculty": translate_faculty(course.faculty),
            "directions": translate_direction(course.directions),
            "language": course.language or "",
            "start": course.start.strftime("%d.%m.%Y") if course.start else "",
            "run_count": course_run_counts.get(course.display_name, 1),
            "url": "/courses/{}/about".format(course.id),
        }
        for course in top_courses
    ]

    context = {
        "courses_count": len(courses),
        "current_year": current_year,
        "current_year_courses_count": len(current_year_courses),
        "faculty_count": len(set(course.faculty for course in courses if course.faculty)),
        "directions_count": len(set(course.directions for course in courses if course.directions)),
        "generated_at": timezone.localtime().strftime("%d.%m.%Y %H:%M"),

        "language_summary": [
            {"label": row["language"], "total": row["total"]}
            for row in courses_by_lang
        ],

        "faculty_labels": json.dumps(
            [translate_faculty(row["faculty"]) for row in courses_by_faculty],
            ensure_ascii=False
        ),
        "faculty_data": json.dumps([row["total"] for row in courses_by_faculty]),

        "directions_labels": json.dumps(
            [translate_direction(row["directions"]) for row in courses_by_directions],
            ensure_ascii=False
        ),
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
