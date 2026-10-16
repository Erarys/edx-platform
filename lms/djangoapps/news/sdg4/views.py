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
    teacher_slug,
    youtube_embed,
)
from .forms import SimpleTeacherForm, material_title, translations


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
    cards = []
    for row, document in documents():
        card = teacher_card(row, document)
        for resource in resource_cards(document):
            cards.append({**resource, "teacher": card})
        for number, course in enumerate(document["courses"], 1):
            if course.get("materials"):
                cards.append(
                    {
                        "id": f"legacy-{row.pk}-{number}",
                        "title": localized(course.get("topic")),
                        "kind": "course",
                        "format": "",
                        "teacher": card,
                        "url": f"{public_url(document)}?course={number}#legacy-{number}",
                    }
                )
        if document["profile"].get("mooc_url"):
            cards.append(
                {
                    "id": f"mooc-{row.pk}",
                    "title": _("Teacher's MOOC on Open KazNU"),
                    "kind": "link",
                    "format": "",
                    "teacher": card,
                    "url": document["profile"]["mooc_url"],
                }
            )
    return render(
        request,
        "index",
        {
            "faculties": FACULTIES,
            "preamble": PREAMBLE,
            "target_43": TARGET_43,
            "target_44": TARGET_44,
            "resources": cards,
        },
    )


@require_GET
def faculty(request, faculty):
    if faculty not in dict(FACULTIES):
        raise Http404
    teachers = [teacher_card(row, doc) for row, doc in documents() if doc["faculty"] == faculty]
    teachers.sort(key=lambda card: alphabetical_key(card["name"]))
    return render(
        request,
        "faculty",
        {"faculty_name": dict(FACULTIES)[faculty], "teachers": teachers},
    )


def course_number(request):
    try:
        number = int(request.GET.get("course", "1"))
    except (ValueError, TypeError):
        raise Http404 from None
    if number not in {1, 2, 3}:
        raise Http404
    return number


def resource_cards(document):
    result = []
    for item in document.get("resources", []):
        args = [document["faculty"], document["slug"], item["id"]]
        extension = PurePosixPath(item.get("file_path", "")).suffix.lower()
        result.append(
            {
                **item,
                "format": extension.lstrip(".").upper(),
                "is_pdf": extension == ".pdf",
                "url": reverse("sdg4_resource", args=args),
                "download_url": reverse("sdg4_resource_download", args=args),
            }
        )
    return result


@never_cache
@require_GET
def teacher(request, faculty, teacher):
    row, document = find_page(faculty, teacher)
    if not document["courses"]:
        document = increment(row.pk, "page", "views")
        return render(
            request,
            "profile",
            {
                "teacher_name": localized(document["profile"].get("name")),
                "profile": {
                    key: localized(document["profile"].get(key)) for key in ("position", "degree", "department")
                },
                "photo_url": row.image.url if row.image else "",
                "faculty_name": dict(FACULTIES)[faculty],
                "faculty_slug": faculty,
                "resources": resource_cards(document),
                "edit_url": reverse("sdg4_edit", args=[row.pk]),
                "permanent_url": request.build_absolute_uri(public_url(document)),
            },
        )
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
            "resources": resource_cards(document),
        },
    )


@never_cache
@require_GET
def material(request, faculty, teacher, course, material_id, download=False):
    row, document = find_page(faculty, teacher)
    if course < 1 or course > len(document["courses"]) or document["courses"][course - 1]["type"] != "lecture":
        raise Http404
    item = next(
        (item for item in document["courses"][course - 1]["materials"] if item["id"] == material_id),
        None,
    )
    if item is None:
        raise Http404
    if item.get("file_path"):
        try:
            stream = default_storage.open(item["file_path"], "rb")
        except FileNotFoundError:
            raise Http404 from None
        response = FileResponse(
            stream,
            as_attachment=download,
            filename=f"{item['title']}.pdf",
            content_type="application/pdf",
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "SAMEORIGIN"
    else:
        response = redirect(item["file_url"])
    if download:
        increment(row.pk, course, "downloads")
    return response


@never_cache
@require_GET
def resource(request, faculty, teacher, material_id, download=False):
    row, document = find_page(faculty, teacher)
    item = next(
        (item for item in document.get("resources", []) if item["id"] == material_id),
        None,
    )
    if item is None:
        raise Http404
    if item.get("kind") == "link":
        response = redirect(item["file_url"])
    else:
        path = item.get("file_path", "")
        extension = PurePosixPath(path).suffix.lower()
        types = {
            ".pdf": "application/pdf",
            ".ppt": "application/vnd.ms-powerpoint",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        if extension not in types:
            raise Http404
        try:
            stream = default_storage.open(path, "rb")
        except FileNotFoundError:
            raise Http404 from None
        response = FileResponse(
            stream,
            as_attachment=download or extension != ".pdf",
            filename=item["title"] + extension,
            content_type=types[extension],
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "SAMEORIGIN"
    increment(row.pk, material_id, "downloads" if download else "views")
    return response


def require_editor(request):
    if not can_manage(request.user):
        raise PermissionDenied


@never_cache
@require_GET
def manage(request):
    require_editor(request)
    teachers = sorted(
        (teacher_card(row, doc) for row, doc in documents(include_drafts=True)),
        key=lambda card: alphabetical_key(card["name"]),
    )
    return render(request, "manage", {"teachers": teachers, "can_create": True})


def save_upload(upload, row_id, slot, saved):
    name = f"sdg4/{row_id}/{slot}/{uuid4().hex}{PurePosixPath(upload.name.lower()).suffix}"
    path = default_storage.save(name, upload)
    saved.append(path)
    return path


def save_page(row, original, form):
    """Append presentations without touching legacy lessons, counters or fixed URLs."""
    saved = []
    try:
        with transaction.atomic():
            if row:
                row = News.objects.select_for_update().get(pk=row.pk, title__startswith=NEWS_PREFIX)
                document = copy.deepcopy(read_document(row))
                if original["revision"] != row.updated_at.isoformat():
                    raise ValueError(_("This page was edited elsewhere. Reload it before saving."))
            else:
                row = News.objects.create(title=NEWS_PREFIX + "draft", content="{}")
                document = {
                    "schema": 2,
                    "faculty": form.cleaned_data["faculty"],
                    "slug": f"{teacher_slug(form.cleaned_data['name_ru'])}-{row.pk}",
                    "profile": {},
                    "courses": [],
                    "resources": [],
                    "stats": {},
                }
            cleaned = form.cleaned_data
            document["schema"] = 2
            document["published"] = cleaned["published"]
            document["profile"].update(
                {key: translations(cleaned, key) for key in ("name", "position", "degree", "department")}
            )
            resources = document.setdefault("resources", [])
            link = next((item for item in resources if item.get("kind") == "link"), {})
            resources[:] = [item for item in resources if item.get("kind") != "link"]
            if cleaned["resource_url"]:
                resources.insert(
                    0,
                    {
                        "id": link.get("id") or uuid4().hex,
                        "kind": "link",
                        "title": cleaned["resource_title"] or str(_("Open course or material")),
                        "file_url": cleaned["resource_url"],
                    },
                )
            for upload in cleaned["presentations"]:
                resources.append(
                    {
                        "id": uuid4().hex,
                        "kind": "presentation",
                        "title": material_title(upload.name),
                        "file_path": save_upload(upload, row.pk, "presentations", saved),
                    }
                )
            if cleaned.get("photo"):
                row.image = save_upload(cleaned["photo"], row.pk, "photo", saved)
            row.title = document_key(document)
            row.content = json.dumps(document, ensure_ascii=False)
            row.save()
            return row, document
    except Exception:
        for path in saved:
            default_storage.delete(path)
        raise


@never_cache
@require_http_methods(["GET", "POST"])
def edit(request, news_id=None):
    require_editor(request)
    row = get_object_or_404(News, pk=news_id, title__startswith=NEWS_PREFIX) if news_id else None
    document = read_document(row) if row else {}
    if row:
        document["revision"] = row.updated_at.isoformat()
    bound_data = request.POST.copy() if request.method == "POST" else None
    publishing = bool(document.get("published"))
    if bound_data is not None:
        action = bound_data.get("action", "save")
        if action in {"draft", "publish"}:
            publishing = action == "publish"
        bound_data["published"] = "on" if publishing else ""
    form = SimpleTeacherForm(
        bound_data,
        request.FILES or None,
        document=document or None,
        publishing=publishing,
    )
    if request.method == "POST":
        valid = form.is_valid()
        if row and request.POST.get("revision") != document["revision"]:
            form.add_error(None, _("This page was edited elsewhere. Reload it before saving."))
            valid = False
        if valid:
            try:
                saved_row, result = save_page(row, document, form)
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                return redirect(
                    public_url(result)
                    if result["published"]
                    else reverse("sdg4_edit", args=[saved_row.pk]) + "?saved=1"
                )
    return render(
        request,
        "edit",
        {
            "teacher_form": form,
            "document": document,
            "row": row,
            "existing_resources": resource_cards(document) if document else [],
        },
    )
