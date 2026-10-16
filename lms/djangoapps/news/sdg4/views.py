"""Anonymous catalogue and permission-checked editorial workflow."""

import copy
import json
from pathlib import PurePosixPath
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.i18n import set_language

from ..models import News
from .constants import (
    FACULTIES,
    LANGUAGES,
    LICENSES,
    NEWS_PREFIX,
    PREAMBLE,
    TARGET_43,
    TARGET_44,
)
from .data import (
    alphabetical_key,
    can_manage,
    document_key,
    documents,
    file_url,
    find_page,
    increment,
    language_code,
    localized,
    public_url,
    read_document,
    youtube_embed,
)
from .forms import CourseForm, Materials, TeacherForm, translations


def render(request, template, context):
    from common.djangoapps.edxmako.shortcuts import render_to_response  # pylint: disable=import-outside-toplevel

    context.update(
        sdg4_languages=LANGUAGES,
        sdg4_language=language_code(),
        can_manage_sdg4=can_manage(request.user),
        csrf_token=get_token(request),
    )
    return render_to_response(f"news/sdg4/{template}.html", context, request=request)


def teacher_card(row, document):
    return {
        "name": localized(document["profile"].get("name")),
        "position": localized(document["profile"].get("position")),
        "url": public_url(document),
        "edit_url": reverse("sdg4_edit", args=[row.pk]),
        "published": document.get("published", False),
        "faculty": str(dict(FACULTIES)[document["faculty"]]),
    }


def save_language_preference(user, language):
    """Integrate with the LMS middleware, which otherwise restores the old cookie."""
    from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY  # pylint: disable=import-outside-toplevel
    from openedx.core.djangoapps.user_api.preferences.api import set_user_preference  # pylint: disable=import-outside-toplevel

    set_user_preference(getattr(user, "real_user", user), LANGUAGE_KEY, language)


@require_POST
def change_language(request):
    language = request.POST.get("language")
    if language not in dict(LANGUAGES):
        return HttpResponseBadRequest()
    if request.user.is_authenticated:
        save_language_preference(request.user, language)
    # Django validates the redirect target against the current host.
    response = set_language(request)
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        domain=getattr(settings, "SHARED_COOKIE_DOMAIN", settings.LANGUAGE_COOKIE_DOMAIN),
        path=settings.LANGUAGE_COOKIE_PATH,
        secure=settings.LANGUAGE_COOKIE_SECURE or request.is_secure(),
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )
    return response


@require_GET
def index(request):
    return render(
        request,
        "index",
        {
            "faculties": FACULTIES,
            "preamble": PREAMBLE,
            "target_43": TARGET_43,
            "target_44": TARGET_44,
        },
    )


@require_GET
def faculty(request, faculty):
    if faculty not in dict(FACULTIES):
        raise Http404
    teachers = [teacher_card(row, doc) for row, doc in documents() if doc["faculty"] == faculty]
    teachers.sort(key=lambda card: alphabetical_key(card["name"]))
    return render(request, "faculty", {"faculty_name": dict(FACULTIES)[faculty], "teachers": teachers})


def course_number(request):
    try:
        number = int(request.GET.get("course", "1"))
    except (ValueError, TypeError):
        raise Http404 from None
    if number not in {1, 2, 3}:
        raise Http404
    return number


@never_cache
@require_GET
def teacher(request, faculty, teacher):
    row, document = find_page(faculty, teacher)
    number = course_number(request)
    document = increment(row.pk, number, "views")
    profile = document["profile"]
    courses = []
    for index, course in enumerate(document["courses"], 1):
        courses.append(
            {
                **course,
                "number": index,
                "title": (
                    _("Lecture course: “{topic}”") if course["type"] == "lecture" else _("Video course: “{topic}”")
                ).format(topic=localized(course["topic"])),
                "url": f"{public_url(document)}?course={index}#course-panel",
            }
        )
    course = courses[number - 1]
    material_context = []
    for material in course["materials"]:
        args = [faculty, teacher, number, material["id"]]
        material_context.append(
            {
                **material,
                "source": file_url(material),
                "captions": file_url(material, "captions"),
                "embed": youtube_embed(material.get("file_url", "")) if course["type"] == "video" else "",
                "view_url": reverse("sdg4_material", args=args),
                "download_url": reverse("sdg4_download", args=args),
            }
        )
    course["materials"] = material_context
    return render(
        request,
        "teacher",
        {
            "teacher_name": localized(profile["name"]),
            "profile": {key: localized(profile.get(key)) for key in ("position", "degree", "department")},
            "photo_url": row.image.url if row.image else "",
            "mooc_url": profile.get("mooc_url", ""),
            "faculty_name": dict(FACULTIES)[faculty],
            "faculty_slug": faculty,
            "courses": courses,
            "selected": course,
            "discipline": localized(course["discipline"]),
            "annotation": localized(course["annotation"]),
            "material_language": dict(LANGUAGES)[course["language"]],
            "license_name": dict(LICENSES)[course["license"]],
            "duration": sum(material.get("duration") or 0 for material in course["materials"]),
            "stats": document.get("stats", {}).get(str(number), {}),
            "edit_url": reverse("sdg4_edit", args=[row.pk]),
            "permanent_url": request.build_absolute_uri(public_url(document)),
        },
    )


@never_cache
@require_GET
def material(request, faculty, teacher, course, material_id, download=False):
    row, document = find_page(faculty, teacher)
    if course not in {1, 2, 3} or document["courses"][course - 1]["type"] != "lecture":
        raise Http404
    item = next((item for item in document["courses"][course - 1]["materials"] if item["id"] == material_id), None)
    if item is None:
        raise Http404
    if item.get("file_path"):
        try:
            stream = default_storage.open(item["file_path"], "rb")
        except FileNotFoundError:
            raise Http404 from None
        response = FileResponse(
            stream, as_attachment=download, filename=f"{item['title']}.pdf", content_type="application/pdf"
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "SAMEORIGIN"
    else:
        response = redirect(item["file_url"])
    if download:
        increment(row.pk, course, "downloads")
    return response


def require_editor(request, create=False):
    if not can_manage(request.user) or (create and not request.user.has_perm("news.add_news")):
        raise PermissionDenied


@never_cache
@require_GET
def manage(request):
    require_editor(request)
    teachers = sorted(
        (teacher_card(row, doc) for row, doc in documents(include_drafts=True)),
        key=lambda card: alphabetical_key(card["name"]),
    )
    return render(request, "manage", {"teachers": teachers, "can_create": request.user.has_perm("news.add_news")})


def save_upload(upload, row_id, slot, saved):
    name = f"sdg4/{row_id}/{slot}/{uuid4().hex}{PurePosixPath(upload.name.lower()).suffix}"
    path = default_storage.save(name, upload)
    saved.append(path)
    return path


def save_page(row, original, teacher_form, bundles):
    """Keep counters fresh and URLs immutable; clean only new uploads on failure."""
    saved = []
    try:
        with transaction.atomic():
            if row:
                row = News.objects.select_for_update().get(pk=row.pk, title__startswith=NEWS_PREFIX)
                latest = read_document(row)
                if original["revision"] != row.updated_at.isoformat():
                    raise ValueError(_("This page was edited elsewhere. Reload it before saving."))
            else:
                row = News.objects.create(title=NEWS_PREFIX + "draft", content="{}")
                latest = {}
            cleaned = teacher_form.cleaned_data
            document = {
                "schema": 1,
                "faculty": original.get("faculty", cleaned["faculty"]),
                # The primary key suffix guarantees uniqueness without a new database index.
                "slug": original.get("slug") or f"{cleaned['slug']}-{row.pk}",
                "published": cleaned["published"],
                "profile": {key: translations(cleaned, key) for key in ("name", "position", "degree", "department")},
                "courses": [],
                "stats": latest.get("stats", {}),
            }
            document["profile"]["mooc_url"] = cleaned["mooc_url"]
            for slot, (course_form, formset) in enumerate(bundles, 1):
                fields = course_form.cleaned_data
                course = {key: fields[key] for key in ("type", "language", "programme", "license")}
                course.update({key: translations(fields, key) for key in ("topic", "discipline", "annotation")})
                old_courses = latest.get("courses", [])
                old = old_courses[slot - 1] if len(old_courses) >= slot else {}
                course["published_at"] = old.get("published_at") or (
                    timezone.localdate().isoformat() if cleaned["published"] else ""
                )
                course["materials"] = []
                for form in formset:
                    values = form.cleaned_data
                    if not values or values.get("DELETE"):
                        continue
                    item = copy.deepcopy(form.material)
                    item.update({key: values[key] for key in ("title", "duration", "youtube_captions")})
                    item.setdefault("id", uuid4().hex)
                    for upload_field, kind in (("file", "file"), ("captions", "captions")):
                        upload = values.get(upload_field)
                        url = values.get(f"{kind}_url", "")
                        if upload:
                            item[f"{kind}_path"] = save_upload(upload, row.pk, slot, saved)
                            item.pop(f"{kind}_url", None)
                        elif url:
                            item[f"{kind}_url"] = url
                            item.pop(f"{kind}_path", None)
                        elif not item.get(f"{kind}_path"):
                            item.pop(f"{kind}_url", None)
                    course["materials"].append(item)
                document["courses"].append(course)
            if cleaned.get("photo"):
                row.image = save_upload(cleaned["photo"], row.pk, "photo", saved)
            row.title = document_key(document)
            row.content = json.dumps(document, ensure_ascii=False)
            row.save()
            return document
    except Exception:
        for path in saved:
            default_storage.delete(path)
        raise


@never_cache
@require_http_methods(["GET", "POST"])
def edit(request, news_id=None):
    require_editor(request, create=news_id is None)
    row = get_object_or_404(News, pk=news_id, title__startswith=NEWS_PREFIX) if news_id else None
    document = read_document(row) if row else {}
    if row:
        document["revision"] = row.updated_at.isoformat()
    teacher_form = TeacherForm(
        request.POST if request.method == "POST" else None, request.FILES or None, document=document or None
    )
    bundles = []
    for slot in range(1, 4):
        course = document.get("courses", [{}, {}, {}])[slot - 1]
        form = CourseForm(request.POST if request.method == "POST" else None, prefix=f"course-{slot}", course=course)
        kind = request.POST.get(f"course-{slot}-type", course.get("type", "lecture"))
        formset = Materials(
            request.POST if request.method == "POST" else None,
            request.FILES or None,
            prefix=f"materials-{slot}",
            materials=course.get("materials", []),
            course_type=kind,
        )
        bundles.append((form, formset))
    if request.method == "POST":
        checks = [teacher_form.is_valid()]
        for form, formset in bundles:
            checks.extend([form.is_valid(), formset.is_valid()])
        # A stale browser form must never silently overwrite another editor's changes.
        if row and request.POST.get("revision") != document["revision"]:
            teacher_form.add_error(None, _("This page was edited elsewhere. Reload it before saving."))
            checks.append(False)
        if all(checks):
            try:
                result = save_page(row, document, teacher_form, bundles)
            except ValueError as exc:
                teacher_form.add_error(None, str(exc))
            else:
                return redirect(public_url(result) if result["published"] else reverse("sdg4_manage"))
    return render(request, "edit", {"teacher_form": teacher_form, "bundles": bundles, "document": document, "row": row})
