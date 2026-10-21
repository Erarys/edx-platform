"""Anonymous catalogue and permission-checked editorial workflow."""

import copy
import json
import math
import re
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
from django.utils.timesince import timesince
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
    can_edit,
    can_manage,
    can_moderate,
    document_key,
    documents,
    file_url,
    find_page,
    increment,
    language_code,
    localized,
    public_url,
    read_document,
    sdg4_queryset,
    teacher_slug,
    youtube_embed,
)
from .forms import (
    ARTICLE_EXTENSIONS,
    ArticleBlocks,
    SimpleTeacherForm,
    material_title,
    translations,
)

ARTICLE_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
}
COVER_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def render(request, template, context):
    from common.djangoapps.edxmako.shortcuts import render_to_response  # pylint: disable=import-outside-toplevel

    context.update(
        sdg4_languages=LANGUAGES,
        sdg4_language=language_code(),
        can_manage_sdg4=can_manage(request.user),
        can_moderate_sdg4=can_moderate(request.user),
        csrf_token=get_token(request),
    )
    return render_to_response(f"news/sdg4/{template}.html", context, request=request)


def teacher_card(row, document):
    published = (
        document.get("published", False)
        and row.moderation_status == News.ModerationStatus.PUBLISHED
    )
    return {
        "name": localized(document["profile"].get("name")),
        "position": localized(document["profile"].get("position")),
        "url": public_url(document),
        "edit_url": reverse("sdg4_edit", args=[row.pk]),
        "published": published,
        "moderation_status": row.moderation_status,
        "status_label": row.get_moderation_status_display(),
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
    publications = []
    for row, document in documents():
        publications.append(publication_card(row, document))
    publications.sort(key=lambda item: item["published_at"], reverse=True)
    return render(
        request,
        "index",
        {
            "faculties": FACULTIES,
            "preamble": PREAMBLE,
            "target_43": TARGET_43,
            "target_44": TARGET_44,
            "publications": publications,
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


def article_blocks(document):
    """Build safe, application-controlled media URLs for article rendering."""
    result = []
    for item in document.get("article", []):
        block = dict(item)
        if item.get("kind") != "text":
            args = [document["faculty"], document["slug"], item["id"]]
            block["url"] = reverse("sdg4_article_block", args=args)
            block["download_url"] = reverse("sdg4_article_block_download", args=args)
            block["format"] = PurePosixPath(item.get("file_path", "")).suffix.lstrip(".").upper()
            block["is_pdf"] = block["format"] == "PDF"
        result.append(block)
    return result


def publication_context(row, document):
    """Metadata displayed above a publication, similar to a conventional article card."""
    author_name = localized(document.get("profile", {}).get("name"))
    if row.author_id:
        author_name = row.author.get_full_name().strip() or row.author.get_username()
    published_at = row.published_at or row.created_at
    age = timesince(published_at, timezone.now()).split(",", maxsplit=1)[0]
    words = sum(
        len(re.findall(r"\w+", block.get("text", ""), flags=re.UNICODE))
        for block in document.get("article", [])
        if block.get("kind") == "text"
    )
    return {
        "article_author": author_name,
        "article_published_at": published_at,
        "article_published_ago": _("{age} ago").format(age=age),
        "article_reading_minutes": max(1, math.ceil(words / 200)) if document.get("article") else 0,
        "article_view_count": row.view_count,
        "cover_url": reverse("sdg4_cover", args=[document["faculty"], document["slug"]]) if row.cover_image else "",
    }


def publication_card(row, document):
    """One feed card per publication, regardless of how many files it contains."""
    teacher = teacher_card(row, document)
    metadata = publication_context(row, document)
    text = next(
        (
            block.get("text", "")
            for block in document.get("article", [])
            if block.get("kind") == "text" and block.get("text", "").strip()
        ),
        "",
    )
    summary = re.sub(r"\s+", " ", text).strip()
    if len(summary) > 420:
        summary = summary[:417].rstrip() + "…"
    courses = sum(bool(course.get("materials")) for course in document.get("courses", []))
    attachments = sum(block.get("kind") != "text" for block in document.get("article", []))
    return {
        "id": row.pk,
        "title": localized(document.get("article_title"))
        or _("Educational materials by {name}").format(name=teacher["name"]),
        "summary": summary or localized(document.get("profile", {}).get("position")),
        "url": public_url(document),
        "teacher": teacher,
        "author": metadata["article_author"],
        "published_at": metadata["article_published_at"],
        "published_ago": metadata["article_published_ago"],
        "reading_minutes": metadata["article_reading_minutes"],
        "view_count": metadata["article_view_count"],
        "cover_url": metadata["cover_url"],
        "photo_url": row.image.url if row.image else "",
        "material_count": len(document.get("resources", [])) + courses + attachments,
    }


@never_cache
@require_GET
def teacher(request, faculty, teacher):
    row, document = find_page(faculty, teacher)
    if not document["courses"]:
        document = increment(row.pk, "page", "views", count_page_view=True)
        row.refresh_from_db(fields=["view_count"])
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
                "article_title": localized(document.get("article_title")),
                "article_blocks": article_blocks(document),
                "edit_url": reverse("sdg4_edit", args=[row.pk]),
                "can_edit_sdg4": can_edit(request.user, row),
                "permanent_url": request.build_absolute_uri(public_url(document)),
                **publication_context(row, document),
            },
        )
    number = course_number(request)
    document = increment(row.pk, number, "views", count_page_view=True)
    row.refresh_from_db(fields=["view_count"])
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
            "article_title": localized(document.get("article_title")),
            "article_blocks": article_blocks(document),
            "can_edit_sdg4": can_edit(request.user, row),
            **publication_context(row, document),
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


@never_cache
@require_GET
def article_block(request, faculty, teacher, block_id, download=False):
    """Serve uploaded article media only after the containing page is published."""
    _row, document = find_page(faculty, teacher)
    item = next((block for block in document.get("article", []) if block.get("id") == block_id), None)
    if item is None or item.get("kind") == "text" or not item.get("file_path"):
        raise Http404
    extension = PurePosixPath(item["file_path"].lower()).suffix
    if extension not in ARTICLE_CONTENT_TYPES or extension not in ARTICLE_EXTENSIONS.get(item.get("kind"), set()):
        raise Http404
    try:
        stream = default_storage.open(item["file_path"], "rb")
    except FileNotFoundError:
        raise Http404 from None
    title = item.get("title") or str(_("Material"))
    response = FileResponse(
        stream,
        as_attachment=download or (item["kind"] == "presentation" and extension != ".pdf"),
        filename=title + extension,
        content_type=ARTICLE_CONTENT_TYPES[extension],
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


@never_cache
@require_GET
def cover_image(request, faculty, teacher):
    """Serve a cover only when its SDG 4 publication is public."""
    row, _document = find_page(faculty, teacher)
    if not row.cover_image:
        raise Http404
    extension = PurePosixPath(row.cover_image.name.lower()).suffix
    if extension not in COVER_CONTENT_TYPES:
        raise Http404
    try:
        stream = default_storage.open(row.cover_image.name, "rb")
    except FileNotFoundError:
        raise Http404 from None
    response = FileResponse(stream, content_type=COVER_CONTENT_TYPES[extension])
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


def require_editor(request):
    if not can_manage(request.user):
        raise PermissionDenied


@never_cache
@require_GET
def manage(request):
    require_editor(request)
    catalogue = documents(include_drafts=True)
    if not can_moderate(request.user):
        catalogue = ((row, doc) for row, doc in catalogue if row.author_id == request.user.id)
    teachers = sorted(
        (teacher_card(row, doc) for row, doc in catalogue),
        key=lambda card: (
            card["moderation_status"] != News.ModerationStatus.PENDING,
            alphabetical_key(card["name"]),
        ),
    )
    return render(
        request,
        "manage",
        {
            "teachers": teachers,
            "can_create": True,
            "pending_count": sum(
                card["moderation_status"] == News.ModerationStatus.PENDING for card in teachers
            ),
        },
    )


def save_upload(upload, row_id, slot, saved):
    name = f"sdg4/{row_id}/{slot}/{uuid4().hex}{PurePosixPath(upload.name.lower()).suffix}"
    path = default_storage.save(name, upload)
    saved.append(path)
    return path


def save_page(row, original, form, moderation_status, article_formset=None, author=None):
    """Save an ordered article while retaining legacy lessons, counters and URLs."""
    saved = []
    try:
        with transaction.atomic():
            if row:
                row = sdg4_queryset().select_for_update().get(pk=row.pk)
                document = copy.deepcopy(read_document(row))
                if original["revision"] != row.updated_at.isoformat():
                    raise ValueError(_("This page was edited elsewhere. Reload it before saving."))
            else:
                row = News.objects.create(
                    title=NEWS_PREFIX + "draft",
                    content="{}",
                    content_type=News.ContentType.SDG4,
                    author=author,
                )
                document = {
                    "schema": 3,
                    "faculty": form.cleaned_data["faculty"],
                    "slug": f"{teacher_slug(form.cleaned_data['name_ru'])}-{row.pk}",
                    "profile": {},
                    "courses": [],
                    "resources": [],
                    "stats": {},
                }
            cleaned = form.cleaned_data
            was_published = (
                bool(document.get("published"))
                and row.moderation_status == News.ModerationStatus.PUBLISHED
            )
            publishing = moderation_status == News.ModerationStatus.PUBLISHED
            document["schema"] = 3
            document["published"] = publishing
            document["article_title"] = translations(cleaned, "article_title")
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
            if article_formset is not None:
                existing_blocks = {item.get("id"): item for item in document.get("article", [])}
                article = []
                for block_form in article_formset.ordered_forms:
                    block_cleaned = block_form.cleaned_data
                    old = copy.deepcopy(existing_blocks.get(block_cleaned.get("block_id"), {}))
                    kind = block_cleaned["kind"]
                    block = {
                        "id": old.get("id") or uuid4().hex,
                        "kind": kind,
                    }
                    if kind == "text":
                        block["text"] = block_cleaned["text"].strip()
                    else:
                        upload = block_cleaned.get("file")
                        block["file_path"] = (
                            save_upload(upload, row.pk, f"article/{kind}", saved)
                            if upload
                            else old["file_path"]
                        )
                        block["title"] = block_cleaned.get("title") or old.get("title") or str(_("Material"))
                        block["caption"] = block_cleaned.get("caption", "")
                    article.append(block)
                document["article"] = article
            if cleaned.get("photo"):
                row.image = save_upload(cleaned["photo"], row.pk, "photo", saved)
            if cleaned.get("cover_image"):
                row.cover_image = save_upload(cleaned["cover_image"], row.pk, "cover", saved)
            if not row.author_id and author is not None:
                row.author = author
            if publishing and not was_published:
                row.published_at = timezone.now()
            elif not publishing:
                row.published_at = None
            row.title = document_key(document)
            row.content = json.dumps(document, ensure_ascii=False)
            row.content_type = News.ContentType.SDG4
            row.moderation_status = moderation_status
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
    row = get_object_or_404(sdg4_queryset(), pk=news_id) if news_id else None
    if row and not can_edit(request.user, row):
        raise PermissionDenied
    document = read_document(row) if row else {}
    if row:
        document["revision"] = row.updated_at.isoformat()
    bound_data = request.POST.copy() if request.method == "POST" else None
    moderation_status = row.moderation_status if row else News.ModerationStatus.DRAFT
    publishing = moderation_status != News.ModerationStatus.DRAFT
    if bound_data is not None:
        action = bound_data.get("action", "save")
        if action == "draft":
            moderation_status = News.ModerationStatus.DRAFT
        elif can_moderate(request.user):
            if action in {"approve", "publish"}:
                moderation_status = News.ModerationStatus.PUBLISHED
            elif action == "submit":
                moderation_status = News.ModerationStatus.PENDING
        else:
            # Never trust the button value sent by a contributor. Any attempt to
            # publish or save a complete article must still pass staff review.
            moderation_status = News.ModerationStatus.PENDING
        publishing = moderation_status != News.ModerationStatus.DRAFT
        bound_data["published"] = (
            "on" if moderation_status == News.ModerationStatus.PUBLISHED else ""
        )
    form = SimpleTeacherForm(
        bound_data,
        request.FILES or None,
        document=document or None,
        publishing=publishing,
    )
    article_submitted = request.method == "POST" and "article-TOTAL_FORMS" in request.POST
    article_formset = ArticleBlocks(
        request.POST if article_submitted else None,
        request.FILES if article_submitted else None,
        blocks=document.get("article", []),
        prefix="article",
    )
    if request.method == "POST":
        form_valid = form.is_valid()
        article_valid = article_formset.is_valid() if article_submitted else True
        valid = form_valid and article_valid
        if valid and publishing:
            article_has_content = bool(
                article_formset.ordered_forms if article_submitted else document.get("article")
            )
            resources_have_content = bool(
                form.cleaned_data.get("resource_url")
                or form.cleaned_data.get("presentations")
                or any(item.get("kind") != "link" for item in document.get("resources", []))
                or document.get("profile", {}).get("mooc_url")
                or any(course.get("materials") for course in document.get("courses", []))
            )
            if not (article_has_content or resources_have_content):
                form.add_error(
                    None,
                    _("Add an article block, a link, or a presentation to publish. You can also save a draft."),
                )
                valid = False
        if row and request.POST.get("revision") != document["revision"]:
            form.add_error(None, _("This page was edited elsewhere. Reload it before saving."))
            valid = False
        if valid:
            try:
                saved_row, result = save_page(
                    row,
                    document,
                    form,
                    moderation_status,
                    article_formset if article_submitted else None,
                    request.user,
                )
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                if result["published"]:
                    return redirect(public_url(result))
                query = "submitted=1" if moderation_status == News.ModerationStatus.PENDING else "saved=1"
                return redirect(reverse("sdg4_edit", args=[saved_row.pk]) + f"?{query}")
    return render(
        request,
        "edit",
        {
            "teacher_form": form,
            "document": document,
            "row": row,
            "moderation_status": moderation_status,
            "existing_resources": resource_cards(document) if document else [],
            "article_formset": article_formset,
        },
    )
