"""Anonymous catalogue and permission-checked editorial workflow."""

import copy
import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.signing import BadSignature, SignatureExpired, dumps, loads
from django.db import transaction
from django.db.models import Q
from django.forms import ImageField
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
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
    PUBLISHERS_GROUP,
    TARGET_43,
    TARGET_44,
)
from .data import (
    alphabetical_key,
    can_edit,
    can_manage,
    can_moderate,
    can_publish,
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
    CourseForm,
    Materials,
    TeacherForm,
    material_title,
    translations,
    validate_article_upload,
    validate_presentation_upload,
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
PRESENTATION_EXTENSIONS = {".pdf", ".ppt", ".pptx"}
logger = logging.getLogger(__name__)
DEFAULT_MAX_TABS = 50
STAGED_UPLOAD_FIELD = "_sdg4_staged_upload"
STAGED_UPLOAD_SALT = "sdg4.chunked-upload.v1"
DEFAULT_UPLOAD_CHUNK_BYTES = 2 * 1024 * 1024
DEFAULT_STAGED_UPLOAD_TTL = 24 * 60 * 60


class PresentationPreviewUnavailable(Exception):
    """The original is valid, but a browser-safe PDF preview cannot be made."""


def upload_chunk_bytes():
    """Keep each request below Tutor's default 4 MB LMS request-body limit."""
    configured = int(getattr(settings, "SDG4_UPLOAD_CHUNK_BYTES", DEFAULT_UPLOAD_CHUNK_BYTES))
    return max(256 * 1024, min(configured, DEFAULT_UPLOAD_CHUNK_BYTES))


def upload_limit_bytes():
    return int(getattr(settings, "SDG4_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024))


def staged_upload_ttl():
    configured = int(getattr(settings, "SDG4_STAGED_UPLOAD_TTL", DEFAULT_STAGED_UPLOAD_TTL))
    return max(60, min(configured, 7 * 24 * 60 * 60))


def upload_api_error(message, status=400):
    return JsonResponse({"error": str(message)}, status=status)


def upload_request_data(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(_("The upload request is invalid.")) from None
    if not isinstance(data, dict):
        raise TypeError(_("The upload request is invalid."))
    return data


def staged_field_kind(field_name, requested_kind=""):
    if field_name == "photo":
        return "image"
    if re.fullmatch(r"course-\d+-lecture_files", field_name):
        return "presentation"
    if re.fullmatch(r"materials-\d+-\d+-file", field_name) and requested_kind in {"presentation", "video"}:
        return requested_kind
    return ""


def staged_upload_path(payload):
    extension = PurePosixPath(payload["name"].lower()).suffix
    return f"sdg4/staging/{payload['user']}/{payload['upload_id']}/file{extension}"


def staged_chunk_path(payload, index):
    return f"sdg4/staging/{payload['user']}/{payload['upload_id']}/chunks/{index:06d}.part"


def staged_chunk_count(payload):
    return max(1, math.ceil(payload["size"] / payload["chunk_size"]))


def load_staged_token(token, user):
    try:
        payload = loads(token, salt=STAGED_UPLOAD_SALT, max_age=staged_upload_ttl())
    except (BadSignature, SignatureExpired):
        raise ValueError(_("The temporary upload has expired. Select the file again.")) from None
    required = {"version", "user", "upload_id", "field", "kind", "name", "size", "chunk_size"}
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError(_("The temporary upload is invalid."))
    if payload["version"] != 1 or payload["user"] != user.pk:
        raise ValueError(_("The temporary upload does not belong to this account."))
    if not re.fullmatch(r"[0-9a-f]{32}", str(payload["upload_id"])):
        raise ValueError(_("The temporary upload is invalid."))
    if staged_field_kind(payload["field"], payload["kind"]) != payload["kind"]:
        raise ValueError(_("The temporary upload is invalid."))
    return payload


def validate_staged_file(upload, kind):
    if kind == "presentation":
        validate_presentation_upload(upload)
    elif kind == "video":
        validate_article_upload(upload, "video")
    elif kind == "image":
        ImageField().clean(upload)
    else:
        raise ValidationError(_("The file type does not match this material."))
    upload.seek(0)


@never_cache
@require_POST
def chunk_upload_start(request):
    """Issue an authenticated token for a bounded, sequential chunk upload."""
    response = require_editor(request)
    if response is not None:
        return response
    try:
        data = upload_request_data(request)
        field_name = str(data.get("field", ""))
        kind = staged_field_kind(field_name, str(data.get("kind", "")))
        filename = PurePosixPath(str(data.get("name", "")).replace("\\", "/")).name[:250]
        size = int(data.get("size", 0))
        if not kind or not filename or size <= 0 or size > upload_limit_bytes():
            raise ValueError(_("The selected file cannot be uploaded."))
        extension = PurePosixPath(filename.lower()).suffix
        allowed = {
            "image": set(COVER_CONTENT_TYPES),
            "presentation": PRESENTATION_EXTENSIONS,
            "video": {".mp4", ".webm", ".ogv"},
        }[kind]
        if extension not in allowed:
            raise ValueError(_("The file type does not match this material."))
        chunk_size = upload_chunk_bytes()
        payload = {
            "version": 1,
            "user": request.user.pk,
            "upload_id": uuid4().hex,
            "field": field_name,
            "kind": kind,
            "name": filename,
            "size": size,
            "chunk_size": chunk_size,
        }
        token = dumps(payload, salt=STAGED_UPLOAD_SALT, compress=True)
    except (TypeError, ValueError) as exc:
        return upload_api_error(exc)
    return JsonResponse({"token": token, "chunk_size": chunk_size, "chunks": staged_chunk_count(payload)})


@never_cache
@require_POST
def chunk_upload_part(request):
    """Store one idempotent raw chunk without parsing a large multipart body."""
    response = require_editor(request)
    if response is not None:
        return response
    try:
        token = request.headers.get("x-sdg4-upload", "")
        payload = load_staged_token(token, request.user)
        index = int(request.headers.get("x-sdg4-chunk-index", "-1"))
        count = staged_chunk_count(payload)
        if index < 0 or index >= count:
            raise ValueError(_("The upload chunk number is invalid."))
        expected = min(payload["chunk_size"], payload["size"] - (index * payload["chunk_size"]))
        body = request.body
        if len(body) != expected:
            raise ValueError(_("The upload chunk is incomplete."))
        path = staged_chunk_path(payload, index)
        if default_storage.exists(path):
            if default_storage.size(path) == expected:
                return JsonResponse({"received": index, "chunks": count})
            default_storage.delete(path)
        stored_path = default_storage.save(path, ContentFile(body))
        if stored_path != path:
            default_storage.delete(stored_path)
            raise ValueError(_("The upload chunk could not be stored."))
    except (TypeError, ValueError, OSError) as exc:
        return upload_api_error(exc)
    return JsonResponse({"received": index, "chunks": count})


@never_cache
@require_POST
def chunk_upload_complete(request):
    """Validate and assemble uploaded chunks into one private staged file."""
    response = require_editor(request)
    if response is not None:
        return response
    try:
        data = upload_request_data(request)
        token = str(data.get("token", ""))
        payload = load_staged_token(token, request.user)
        complete_path = staged_upload_path(payload)
        if default_storage.exists(complete_path) and default_storage.size(complete_path) == payload["size"]:
            return JsonResponse({"token": token})
        count = staged_chunk_count(payload)
        part_paths = [staged_chunk_path(payload, index) for index in range(count)]
        if any(not default_storage.exists(path) for path in part_paths):
            raise ValueError(_("The upload is incomplete. Please try again."))
        with tempfile.NamedTemporaryFile(prefix="sdg4-upload-") as assembled:
            total = 0
            for path in part_paths:
                with default_storage.open(path, "rb") as part:
                    shutil.copyfileobj(part, assembled, length=1024 * 1024)
                total += default_storage.size(path)
            if total != payload["size"]:
                raise ValueError(_("The uploaded file size is invalid."))
            assembled.flush()
            assembled.seek(0)
            upload = File(assembled, name=payload["name"])
            validate_staged_file(upload, payload["kind"])
            if default_storage.exists(complete_path):
                default_storage.delete(complete_path)
            stored_path = default_storage.save(complete_path, upload)
            if stored_path != complete_path:
                default_storage.delete(stored_path)
                raise ValueError(_("The uploaded file could not be assembled."))
        for path in part_paths:
            default_storage.delete(path)
    except (TypeError, ValueError, ValidationError, OSError) as exc:
        return upload_api_error(exc)
    return JsonResponse({"token": token})


def materialize_staged_uploads(request):
    """Attach signed staged files to request.FILES for the existing forms."""
    tokens = request.POST.getlist(STAGED_UPLOAD_FIELD)
    if len(tokens) > 250:
        return [], [], _("Too many temporary uploads were submitted.")
    accepted = []
    opened = []
    try:
        for token in tokens:
            payload = load_staged_token(token, request.user)
            path = staged_upload_path(payload)
            if not default_storage.exists(path) or default_storage.size(path) != payload["size"]:
                raise ValueError(_("The temporary upload is incomplete. Select the file again."))
            source = default_storage.open(path, "rb")
            upload = File(source, name=payload["name"])
            opened.append(upload)
            request.FILES.appendlist(payload["field"], upload)
            accepted.append((token, payload))
    except (ValueError, OSError) as exc:
        return accepted, opened, str(exc)
    return accepted, opened, ""


def close_staged_uploads(opened):
    for upload in opened:
        upload.close()


def remove_staged_uploads(accepted):
    for _token, payload in accepted:
        default_storage.delete(staged_upload_path(payload))
        for index in range(staged_chunk_count(payload)):
            default_storage.delete(staged_chunk_path(payload, index))


def presentation_converter():
    """Return an explicitly configured or system LibreOffice executable."""
    if not getattr(settings, "SDG4_PRESENTATION_PREVIEW_ENABLED", True):
        return ""
    configured = str(getattr(settings, "SDG4_PRESENTATION_CONVERTER", "")).strip()
    candidates = [configured] if configured else ["libreoffice", "soffice"]
    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable:
            return executable
    return ""


def presentation_preview_path(row_id, item_id):
    """Use a deterministic path so legacy presentations need no data migration."""
    return f"sdg4/{row_id}/previews/{item_id}.pdf"


def ensure_presentation_preview(row, item):
    """Return a PDF path, converting PPT/PPTX once through a shell-free command."""
    source_path = item.get("file_path", "")
    extension = PurePosixPath(source_path.lower()).suffix
    if extension == ".pdf":
        return source_path
    if extension not in {".ppt", ".pptx"}:
        raise PresentationPreviewUnavailable
    target_path = presentation_preview_path(row.pk, item["id"])
    if default_storage.exists(target_path):
        return target_path
    converter = presentation_converter()
    if not converter:
        raise PresentationPreviewUnavailable

    timeout = max(10, min(int(getattr(settings, "SDG4_PRESENTATION_PREVIEW_TIMEOUT", 120)), 600))
    try:
        with tempfile.TemporaryDirectory(prefix="sdg4-presentation-") as temporary_directory:
            temporary_path = Path(temporary_directory)
            source_file = temporary_path / f"presentation{extension}"
            output_file = temporary_path / "presentation.pdf"
            profile_directory = temporary_path / "libreoffice-profile"
            profile_directory.mkdir()
            with default_storage.open(source_path, "rb") as source, source_file.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            result = subprocess.run(
                [
                    converter,
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nolockcheck",
                    "--nofirststartwizard",
                    f"-env:UserInstallation={profile_directory.as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(temporary_path),
                    str(source_file),
                ],
                check=False,
                capture_output=True,
                timeout=timeout,
            )
            if result.returncode or not output_file.exists():
                raise PresentationPreviewUnavailable
            with output_file.open("rb") as preview:
                if preview.read(5) != b"%PDF-":
                    raise PresentationPreviewUnavailable
                preview.seek(0)
                stored_path = default_storage.save(target_path, File(preview))
            if stored_path != target_path and default_storage.exists(target_path):
                default_storage.delete(stored_path)
                stored_path = target_path
            return stored_path
    except (OSError, subprocess.SubprocessError, PresentationPreviewUnavailable) as exc:
        logger.warning("Could not create SDG 4 presentation preview for News %s: %s", row.pk, exc)
        raise PresentationPreviewUnavailable from exc


def render(request, template, context, status=200):
    from common.djangoapps.edxmako.shortcuts import render_to_response  # pylint: disable=import-outside-toplevel

    context.update(
        sdg4_languages=LANGUAGES,
        sdg4_language=language_code(),
        can_manage_sdg4=can_manage(request.user),
        can_moderate_sdg4=can_moderate(request.user),
        can_publish_sdg4=can_publish(request.user),
        csrf_token=get_token(request),
    )
    return render_to_response(
        f"news/sdg4/{template}.html",
        context,
        request=request,
        status=status,
    )


def teacher_card(row, document):
    published = (
        document.get("published", False)
        and row.moderation_status == News.ModerationStatus.PUBLISHED
    )
    return {
        "id": row.pk,
        "name": localized(document["profile"].get("name")),
        "position": localized(document["profile"].get("position")),
        "url": public_url(document),
        "edit_url": reverse("sdg4_edit", args=[row.pk]),
        "published": published,
        "moderation_status": row.moderation_status,
        "status_label": row.get_moderation_status_display(),
        "faculty": str(dict(FACULTIES)[document["faculty"]]),
        "delete_url": reverse("sdg4_delete", args=[row.pk]),
    }


def author_identity(document):
    """Group existing publication rows without changing their stored URLs or JSON."""
    name = localized(document.get("profile", {}).get("name"), "ru")
    normalized_name = re.sub(r"\s+", " ", name).strip().casefold()
    return document["faculty"], normalized_name


def document_tab_sources(row, document):
    """Expose legacy courses and newer media-only publications through one tab model."""
    courses = document.get("courses", [])
    if courses:
        return [
            {
                "row": row,
                "document": document,
                "course": course,
                "course_number": number,
                "metric_key": str(number),
            }
            for number, course in enumerate(courses, 1)
        ]
    media = [
        {**item, "source_collection": "article"}
        for item in document.get("article", [])
        if item.get("kind") in {"presentation", "video"}
    ]
    media.extend(
        {**item, "source_collection": "resource"}
        for item in document.get("resources", [])
        if item.get("kind") == "presentation"
    )
    if not media:
        return []
    return [{
        "row": row,
        "document": document,
        "course": None,
        "course_number": None,
        "metric_key": "page",
        "legacy_media": media,
    }]


def author_entries(document, include_drafts=False):
    identity = author_identity(document)
    entries = [
        (row, candidate)
        for row, candidate in documents(include_drafts=include_drafts)
        if author_identity(candidate) == identity
    ]
    return sorted(entries, key=lambda item: (item[0].published_at or item[0].created_at, item[0].pk))


def author_tabs(entries):
    sources = []
    seen_material_ids = set()
    for row, document in entries:
        for source in document_tab_sources(row, document):
            course = source.get("course") or {}
            materials = course.get("materials", source.get("legacy_media", []))
            material_ids = {item.get("id") for item in materials if item.get("id")}
            if material_ids and material_ids.issubset(seen_material_ids):
                continue
            seen_material_ids.update(material_ids)
            sources.append(source)
    return sources


def legacy_editor_course(items, course_type):
    """Convert retained article media into an editable presentation or video tab."""
    material_kind = "presentation" if course_type == "lecture" else "video"
    materials = []
    for item in items:
        if item.get("kind") != material_kind:
            continue
        material = {
            key: copy.deepcopy(item[key])
            for key in (
                "id",
                "title",
                "file_path",
                "file_url",
                "captions_path",
                "captions_url",
                "youtube_captions",
                "duration",
            )
            if key in item
        }
        material.setdefault("title", material_title(item.get("file_path") or item.get("file_url", "")))
        materials.append(material)
    if not materials:
        return None
    return {
        "type": course_type,
        "language": "ru",
        "programme": "",
        "license": "by",
        "topic": {},
        "discipline": {},
        "annotation": {},
        "materials": materials,
    }


def editor_courses(row, document, user):
    """Prefill every retained tab for a teacher, including older multi-row data."""
    entries = [
        (candidate, candidate_document)
        for candidate, candidate_document in author_entries(document, include_drafts=True)
        if can_edit(user, candidate)
    ]
    if not any(candidate.pk == row.pk for candidate, _candidate_document in entries):
        entries.insert(0, (row, document))
    courses = []
    for source in author_tabs(entries):
        if source.get("course"):
            courses.append(copy.deepcopy(source["course"]))
        else:
            media = source.get("legacy_media", [])
            for course_type in ("lecture", "video"):
                course = legacy_editor_course(media, course_type)
                if course:
                    courses.append(course)
    return courses


def owned_author_groups(user):
    """Return a user's existing teacher resource sets in stable canonical order."""
    groups = {}
    for row, document in documents(include_drafts=True):
        if row.author_id == user.id:
            groups.setdefault(author_identity(document), []).append((row, document))
    return [
        sorted(entries, key=lambda item: (item[0].created_at, item[0].pk))
        for entries in groups.values()
    ]


def author_card(entries):
    """One catalogue card per teacher, even when old data uses several News rows."""
    entries = sorted(entries, key=lambda item: (item[0].published_at or item[0].created_at, item[0].pk))
    _row, document = entries[0]
    tabs = author_tabs(entries)
    profile = document.get("profile", {})
    photo_row = next((candidate for candidate, _doc in entries if candidate.image), None)
    return {
        "name": localized(profile.get("name")),
        "position": localized(profile.get("position")),
        "degree": localized(profile.get("degree")),
        "department": localized(profile.get("department")),
        "faculty": str(dict(FACULTIES)[document["faculty"]]),
        "faculty_slug": document["faculty"],
        "url": public_url(document),
        "photo_url": photo_row.image.url if photo_row else "",
        "tab_count": len(tabs),
        "view_count": sum(candidate.view_count for candidate, _doc in entries),
    }


def catalogue_authors(catalogue=None):
    groups = {}
    for row, document in (documents() if catalogue is None else catalogue):
        groups.setdefault(author_identity(document), []).append((row, document))
    cards = [author_card(entries) for entries in groups.values() if author_tabs(entries)]
    return sorted(cards, key=lambda card: alphabetical_key(card["name"]))


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
            "authors": catalogue_authors(),
        },
    )


@require_GET
def faculty(request, faculty):
    if faculty not in dict(FACULTIES):
        raise Http404
    catalogue = [(row, doc) for row, doc in documents() if doc["faculty"] == faculty]
    teachers = catalogue_authors(catalogue)
    return render(
        request,
        "faculty",
        {"faculty_name": dict(FACULTIES)[faculty], "teachers": teachers},
    )


def course_number(request):
    try:
        number = int(request.GET.get("tab", request.GET.get("course", "1")))
    except (ValueError, TypeError):
        raise Http404 from None
    if number < 1:
        raise Http404
    return number


def resource_cards(row, document):
    result = []
    converter_available = bool(presentation_converter())
    for item in document.get("resources", []):
        args = [document["faculty"], document["slug"], item["id"]]
        extension = PurePosixPath(item.get("file_path", "")).suffix.lower()
        converted_preview = extension in {".ppt", ".pptx"} and (
            converter_available
            or default_storage.exists(presentation_preview_path(row.pk, item["id"]))
        )
        result.append(
            {
                **item,
                "format": extension.lstrip(".").upper(),
                "is_pdf": extension == ".pdf",
                "url": reverse("sdg4_resource", args=args),
                "download_url": reverse("sdg4_resource_download", args=args),
                "preview_url": reverse("sdg4_resource_preview", args=args),
                "preview_available": extension == ".pdf" or converted_preview,
            }
        )
    return result


def article_blocks(row, document):
    """Build safe, application-controlled media URLs for article rendering."""
    result = []
    converter_available = bool(presentation_converter())
    for item in document.get("article", []):
        block = dict(item)
        if item.get("kind") != "text":
            args = [document["faculty"], document["slug"], item["id"]]
            block["url"] = reverse("sdg4_article_block", args=args)
            block["download_url"] = reverse("sdg4_article_block_download", args=args)
            block["format"] = PurePosixPath(item.get("file_path", "")).suffix.lstrip(".").upper()
            block["is_pdf"] = block["format"] == "PDF"
            if item.get("kind") == "presentation":
                extension = PurePosixPath(item.get("file_path", "")).suffix.lower()
                block["preview_url"] = reverse("sdg4_article_block_preview", args=args)
                block["preview_available"] = block["is_pdf"] or (
                    extension in {".ppt", ".pptx"}
                    and (
                        converter_available
                        or default_storage.exists(presentation_preview_path(row.pk, item["id"]))
                    )
                )
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


def publication_title(document):
    title = localized(document.get("article_title"))
    if title:
        return title
    teacher_name = localized(document.get("profile", {}).get("name"))
    return _("Educational materials by {name}").format(name=teacher_name)


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
    cover_url = metadata["cover_url"]
    if not cover_url:
        first_image = next(
            (block for block in document.get("article", []) if block.get("kind") == "image"),
            None,
        )
        if first_image:
            cover_url = reverse(
                "sdg4_article_block",
                args=[document["faculty"], document["slug"], first_image["id"]],
            )
    return {
        "id": row.pk,
        "title": publication_title(document),
        "summary": summary or localized(document.get("profile", {}).get("position")),
        "url": public_url(document),
        "teacher": teacher,
        "author": metadata["article_author"],
        "published_at": metadata["article_published_at"],
        "published_ago": metadata["article_published_ago"],
        "reading_minutes": metadata["article_reading_minutes"],
        "view_count": metadata["article_view_count"],
        "cover_url": cover_url,
        "photo_url": row.image.url if row.image else "",
    }


@never_cache
@require_GET
def teacher(request, faculty, teacher):
    _requested_row, requested_document = find_page(faculty, teacher)
    entries = author_entries(requested_document)
    sources = author_tabs(entries)
    number = course_number(request)
    if not sources or number > len(sources):
        raise Http404
    _canonical_row, canonical_document = entries[0]
    selected_source = sources[number - 1]
    row = selected_source["row"]
    document = increment(
        row.pk,
        selected_source["metric_key"],
        "views",
        count_page_view=True,
    )
    row.refresh_from_db(fields=["view_count"])
    # Refresh the selected source after the transactional counter update.
    selected_source["document"] = document
    base_url = public_url(canonical_document)
    courses = [
        {
            "number": index,
            "title": _("Tab {number}").format(number=index),
            "url": f"{base_url}?tab={index}#course-panel",
        }
        for index, _source in enumerate(sources, 1)
    ]
    course = selected_source.get("course")
    material_context = []
    if course:
        for item in course.get("materials", []):
            extension = PurePosixPath(item.get("file_path", urlsplit(item.get("file_url", "")).path)).suffix.lower()
            material_item = {
                **item,
                "kind": "presentation" if course["type"] == "lecture" else "video",
                "format": extension.lstrip(".").upper(),
                "source": file_url(item),
                "embed": youtube_embed(item.get("file_url", "")) if course["type"] == "video" else "",
            }
            if course["type"] == "lecture":
                args = [document["faculty"], document["slug"], selected_source["course_number"], item["id"]]
                view_url = reverse("sdg4_material", args=args)
                stored_file = bool(item.get("file_path"))
                material_item.update(
                    view_url=view_url,
                    download_url=reverse("sdg4_download", args=args),
                    preview_url=reverse("sdg4_material_preview", args=args) if stored_file else view_url,
                    preview_available=extension == ".pdf" or (
                        stored_file
                        and extension in {".ppt", ".pptx"}
                        and (presentation_converter() or default_storage.exists(presentation_preview_path(row.pk, item["id"])))
                    ),
                )
            material_context.append(material_item)
    else:
        article_by_id = {item["id"]: item for item in article_blocks(row, document)}
        resource_by_id = {item["id"]: item for item in resource_cards(row, document)}
        for item in selected_source.get("legacy_media", []):
            if item["source_collection"] == "article":
                material_item = article_by_id[item["id"]]
            else:
                material_item = resource_by_id[item["id"]]
            material_context.append({**material_item, "kind": item["kind"]})
    selected = {
        **(course or {}),
        "number": number,
        "title": _("Tab {number}").format(number=number),
        "materials": material_context,
        "type": course.get("type") if course else "mixed",
        "published_at": (row.published_at or row.created_at).date().isoformat(),
    }
    profile_document = canonical_document
    profile = profile_document["profile"]
    photo_row = next((candidate for candidate, _doc in entries if candidate.image), None)
    stats = document.get("stats", {}).get(selected_source["metric_key"], {})
    selected_stats = {
        "views": stats.get("views", row.view_count if selected_source["metric_key"] == "page" else 0),
        "downloads": stats.get("downloads", 0),
    }
    return render(
        request,
        "teacher",
        {
            "teacher_name": localized(profile["name"]),
            "profile": {key: localized(profile.get(key)) for key in ("position", "degree", "department")},
            "photo_url": photo_row.image.url if photo_row else "",
            "mooc_url": profile.get("mooc_url", ""),
            "faculty_name": dict(FACULTIES)[faculty],
            "faculty_slug": faculty,
            "courses": courses,
            "selected": selected,
            "discipline": localized(course.get("discipline")) if course else "",
            "annotation": localized(course.get("annotation")) if course else "",
            "material_language": dict(LANGUAGES).get(course.get("language"), "") if course else "",
            "license_name": dict(LICENSES).get(course.get("license"), "") if course else "",
            "duration": sum(material.get("duration") or 0 for material in material_context),
            "stats": selected_stats,
            "edit_url": reverse("sdg4_edit", args=[_canonical_row.pk]),
            "can_edit_sdg4": can_edit(request.user, _canonical_row),
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
        extension = PurePosixPath(item["file_path"].lower()).suffix
        content_types = {
            ".pdf": "application/pdf",
            ".ppt": "application/vnd.ms-powerpoint",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        if extension not in content_types:
            raise Http404
        try:
            stream = default_storage.open(item["file_path"], "rb")
        except FileNotFoundError:
            raise Http404 from None
        response = FileResponse(
            stream,
            as_attachment=download or extension != ".pdf",
            filename=f"{item['title']}{extension}",
            content_type=content_types[extension],
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
def material_preview(request, faculty, teacher, course, material_id):
    """Render a PDF preview for a presentation stored in a resource tab."""
    row, document = find_page(faculty, teacher)
    if course < 1 or course > len(document["courses"]) or document["courses"][course - 1]["type"] != "lecture":
        raise Http404
    item = next(
        (candidate for candidate in document["courses"][course - 1]["materials"] if candidate["id"] == material_id),
        None,
    )
    if item is None:
        raise Http404
    return presentation_preview_response(row, {**item, "kind": "presentation"})


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


def presentation_preview_response(row, item):
    """Serve a same-origin PDF suitable for an embedded, scrollable viewer."""
    source_path = item.get("file_path", "")
    if item.get("kind") != "presentation" or PurePosixPath(source_path.lower()).suffix not in PRESENTATION_EXTENSIONS:
        raise Http404
    try:
        preview_path = ensure_presentation_preview(row, item)
        stream = default_storage.open(preview_path, "rb")
    except (FileNotFoundError, PresentationPreviewUnavailable):
        return HttpResponse(
            _("Presentation preview is temporarily unavailable. Download the original file to view it."),
            status=503,
            content_type="text/plain; charset=utf-8",
        )
    response = FileResponse(
        stream,
        as_attachment=False,
        filename=(item.get("title") or str(_("Presentation"))) + ".pdf",
        content_type="application/pdf",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


@never_cache
@require_GET
def resource_preview(request, faculty, teacher, material_id):
    """Preview a legacy resource presentation, including existing PPT/PPTX files."""
    row, _document = find_page(faculty, teacher)
    with transaction.atomic():
        row = sdg4_queryset().select_for_update().get(pk=row.pk)
        document = read_document(row)
        if not document.get("published") or row.moderation_status != News.ModerationStatus.PUBLISHED:
            raise Http404
        item = next(
            (candidate for candidate in document.get("resources", []) if candidate.get("id") == material_id),
            None,
        )
        if item is None:
            raise Http404
        return presentation_preview_response(row, item)


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
def article_block_preview(request, faculty, teacher, block_id):
    """Preview an article presentation without exposing draft uploads."""
    row, _document = find_page(faculty, teacher)
    with transaction.atomic():
        row = sdg4_queryset().select_for_update().get(pk=row.pk)
        document = read_document(row)
        if not document.get("published") or row.moderation_status != News.ModerationStatus.PUBLISHED:
            raise Http404
        item = next(
            (candidate for candidate in document.get("article", []) if candidate.get("id") == block_id),
            None,
        )
        if item is None:
            raise Http404
        return presentation_preview_response(row, item)


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
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
    if not request.user.is_active:
        return render(
            request,
            "account_confirmation",
            {"account_email": request.user.email},
            status=403,
        )
    if not can_manage(request.user):
        raise PermissionDenied
    return None


def require_moderator(request):
    response = require_editor(request)
    if response is not None:
        return response
    if not can_moderate(request.user):
        raise PermissionDenied
    return None


@never_cache
@require_GET
def manage(request):
    response = require_editor(request)
    if response is not None:
        return response
    catalogue = (
        (row, doc)
        for row, doc in documents(include_drafts=True)
        if row.author_id == request.user.id
    )
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
        },
    )


def moderation_card(row, document):
    card = teacher_card(row, document)
    metadata = publication_context(row, document)
    text = next(
        (
            block.get("text", "")
            for block in document.get("article", [])
            if block.get("kind") == "text" and block.get("text", "").strip()
        ),
        "",
    )
    card.update(
        title=publication_title(document),
        author=metadata["article_author"],
        author_username=row.author.get_username() if row.author_id else "",
        author_email=row.author.email if row.author_id else "",
        summary=re.sub(r"\s+", " ", text).strip()[:280],
    )
    return card


def user_by_login_or_email(identifier):
    """Resolve one exact account without guessing when an email is shared."""
    user_model = get_user_model()
    username_field = user_model.USERNAME_FIELD
    matches = list(
        user_model._default_manager.filter(
            Q(**{f"{username_field}__iexact": identifier}) | Q(email__iexact=identifier)
        ).order_by("pk")[:3]
    )
    exact_login = [
        user for user in matches
        if str(getattr(user, username_field, "")).casefold() == identifier.casefold()
    ]
    if len(exact_login) == 1:
        return exact_login[0], ""
    if not matches:
        return None, str(_("No user with this login or email was found."))
    if len(matches) > 1:
        return None, str(_("More than one user has this email. Enter the exact login."))
    return matches[0], ""


def users_by_login_or_email(value):
    """Resolve a bounded, de-duplicated batch without applying partial changes."""
    identifiers = []
    seen = set()
    for identifier in re.split(r"[,;\r\n]+", value):
        identifier = identifier.strip()
        key = identifier.casefold()
        if identifier and key not in seen:
            identifiers.append(identifier)
            seen.add(key)
    if not identifiers:
        return [], [], str(_("Enter one or more logins or email addresses."))
    if len(identifiers) > 50:
        return [], [], str(_("You can update up to 50 accounts at once."))
    users = []
    errors = []
    for identifier in identifiers:
        user, error = user_by_login_or_email(identifier)
        if error:
            errors.append((identifier, error))
        else:
            users.append(user)
    return users, errors, ""


@never_cache
@require_http_methods(["GET", "POST"])
def moderation(request):
    response = require_moderator(request)
    if response is not None:
        return response
    access_error = ""
    access_errors = []
    identifiers_value = ""
    if request.method == "POST":
        action = request.POST.get("action")
        identifiers_value = request.POST.get(
            "identifiers",
            request.POST.get("identifier", ""),
        ).strip()
        if action not in {"grant", "revoke"}:
            return HttpResponseBadRequest()
        users, access_errors, access_error = users_by_login_or_email(identifiers_value)
        if users and not access_errors and not access_error:
            with transaction.atomic():
                group, _created = Group.objects.get_or_create(name=PUBLISHERS_GROUP)
                if action == "grant":
                    group.user_set.add(*users)
                else:
                    group.user_set.remove(*users)
            return redirect(
                reverse("sdg4_moderation")
                + ("?access=granted" if action == "grant" else "?access=revoked")
            )
    pending = [
        moderation_card(row, document)
        for row, document in documents(include_drafts=True)
        if row.moderation_status == News.ModerationStatus.PENDING
    ]
    publisher_group = Group.objects.filter(name=PUBLISHERS_GROUP).first()
    publishers = (
        list(publisher_group.user_set.all().order_by("username"))
        if publisher_group is not None
        else []
    )
    return render(
        request,
        "moderation",
        {
            "pending": pending,
            "publishers": publishers,
            "access_error": access_error,
            "access_errors": access_errors,
            "identifiers_value": identifiers_value,
        },
    )


@require_POST
def approve(request, news_id):
    response = require_moderator(request)
    if response is not None:
        return response
    with transaction.atomic():
        row = get_object_or_404(sdg4_queryset().select_for_update(), pk=news_id)
        if row.moderation_status != News.ModerationStatus.PENDING:
            return HttpResponseBadRequest()
        document = read_document(row)
        document["published"] = True
        row.content = json.dumps(document, ensure_ascii=False)
        row.moderation_status = News.ModerationStatus.PUBLISHED
        row.published_at = timezone.now()
        row.save(update_fields=["content", "moderation_status", "published_at", "updated_at"])
    return redirect(reverse("sdg4_moderation") + "?approved=1")


def owned_upload_paths(row, document):
    """Return only files stored below this publication's private upload prefix."""
    prefix = f"sdg4/{row.pk}/"
    candidates = [row.image.name if row.image else "", row.cover_image.name if row.cover_image else ""]
    uploaded_items = [*document.get("resources", []), *document.get("article", [])]
    candidates.extend(item.get("file_path", "") for item in uploaded_items)
    for item in uploaded_items:
        if item.get("kind") == "presentation" and item.get("id"):
            preview_path = presentation_preview_path(row.pk, item["id"])
            if default_storage.exists(preview_path):
                candidates.append(preview_path)
    for course in document.get("courses", []):
        for item in course.get("materials", []):
            candidates.extend((item.get("file_path", ""), item.get("captions_path", "")))
            if course.get("type") == "lecture" and item.get("id"):
                preview_path = presentation_preview_path(row.pk, item["id"])
                if default_storage.exists(preview_path):
                    candidates.append(preview_path)
    return sorted({path for path in candidates if path.startswith(prefix)})


@never_cache
@require_http_methods(["GET", "POST"])
def delete_publication(request, news_id):
    response = require_editor(request)
    if response is not None:
        return response
    row = get_object_or_404(sdg4_queryset(), pk=news_id)
    if not can_edit(request.user, row):
        raise PermissionDenied
    document = read_document(row)
    return_to_moderation = can_moderate(request.user) and (
        request.GET.get("from") == "moderation" or request.POST.get("from") == "moderation"
    )
    if request.method == "POST":
        if request.POST.get("confirm") != "yes":
            return HttpResponseBadRequest()
        paths = owned_upload_paths(row, document)
        with transaction.atomic():
            row.delete()
            for path in paths:
                transaction.on_commit(lambda file_path=path: default_storage.delete(file_path))
        target = "sdg4_moderation" if return_to_moderation else "sdg4_manage"
        return redirect(reverse(target) + "?deleted=1")
    return render(
        request,
        "delete",
        {
            "row": row,
            "publication_title": publication_title(document),
            "return_to_moderation": return_to_moderation,
        },
    )


def save_upload(upload, row_id, slot, saved):
    name = f"sdg4/{row_id}/{slot}/{uuid4().hex}{PurePosixPath(upload.name.lower()).suffix}"
    path = default_storage.save(name, upload)
    saved.append(path)
    return path


def save_page(row, original, teacher_form, bundles, moderation_status, author=None):
    """Save the active resource tabs while retaining superseded article data."""
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
                    "faculty": teacher_form.cleaned_data["faculty"],
                    "slug": f"{teacher_slug(teacher_form.cleaned_data['name_ru'])}-{row.pk}",
                    "profile": {},
                    "courses": [],
                    "resources": [],
                    "article": [],
                    "stats": {},
                }
            cleaned = teacher_form.cleaned_data
            was_published = (
                bool(document.get("published"))
                and row.moderation_status == News.ModerationStatus.PUBLISHED
            )
            publishing = moderation_status == News.ModerationStatus.PUBLISHED
            document["schema"] = 3
            document["published"] = publishing
            document["profile"].update(
                {key: translations(cleaned, key) for key in ("name", "position", "degree", "department")}
            )
            document["profile"]["mooc_url"] = cleaned.get("mooc_url", "")
            courses = []
            for course_form, formset, deleted in bundles:
                if deleted:
                    continue
                slot = len(courses) + 1
                fields = course_form.cleaned_data
                old_course = course_form.course
                course = {
                    "type": fields.get("type") or old_course.get("type", "lecture"),
                    "language": fields.get("language") or old_course.get("language", "ru"),
                    "programme": fields.get("programme", ""),
                    "license": fields.get("license") or old_course.get("license", "by"),
                }
                course.update({key: translations(fields, key) for key in ("topic", "discipline", "annotation")})
                course["published_at"] = old_course.get("published_at") or (
                    timezone.localdate().isoformat() if publishing else ""
                )
                course["materials"] = []
                for material_form in formset:
                    values = material_form.cleaned_data
                    if not values or values.get("DELETE"):
                        continue
                    item = copy.deepcopy(material_form.material)
                    item.update({key: values.get(key) for key in ("title", "duration")})
                    item.setdefault("id", uuid4().hex)
                    upload = values.get("file")
                    url = values.get("file_url", "")
                    if upload:
                        item["file_path"] = save_upload(upload, row.pk, f"courses/{slot}", saved)
                        item.pop("file_url", None)
                    elif url:
                        item["file_url"] = url
                        item.pop("file_path", None)
                    elif not item.get("file_path"):
                        item.pop("file_url", None)
                    course["materials"].append(item)
                for upload in fields.get("lecture_files", []):
                    course["materials"].append(
                        {
                            "id": uuid4().hex,
                            "title": material_title(upload.name),
                            "file_path": save_upload(upload, row.pk, f"courses/{slot}", saved),
                            "duration": None,
                        }
                    )
                courses.append(course)
            document["courses"] = courses
            if cleaned.get("photo"):
                row.image = save_upload(cleaned["photo"], row.pk, "photo", saved)
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
    response = require_editor(request)
    if response is not None:
        return response
    if news_id is None and request.method == "GET" and request.GET.get("new") != "1":
        groups = owned_author_groups(request.user)
        if len(groups) == 1:
            return redirect("sdg4_edit", news_id=groups[0][0][0].pk)
        if len(groups) > 1:
            return redirect("sdg4_manage")
    row = get_object_or_404(sdg4_queryset(), pk=news_id) if news_id else None
    if row and not can_edit(request.user, row):
        raise PermissionDenied
    document = read_document(row) if row else {}
    if row and request.method == "GET":
        editable_entries = [
            (candidate, candidate_document)
            for candidate, candidate_document in author_entries(document, include_drafts=True)
            if can_edit(request.user, candidate)
        ]
        if editable_entries and editable_entries[0][0].pk != row.pk:
            return redirect("sdg4_edit", news_id=editable_entries[0][0].pk)
    if row:
        document["courses"] = editor_courses(row, document, request.user)
    if row:
        document["revision"] = row.updated_at.isoformat()
    staged_uploads = []
    staged_files = []
    staged_error = ""
    if request.method == "POST":
        staged_uploads, staged_files, staged_error = materialize_staged_uploads(request)
    bound_data = request.POST.copy() if request.method == "POST" else None
    moderation_status = row.moderation_status if row else News.ModerationStatus.DRAFT
    publishing = moderation_status != News.ModerationStatus.DRAFT
    if bound_data is not None:
        action = bound_data.get("action", "save")
        if action == "draft":
            moderation_status = News.ModerationStatus.DRAFT
        elif can_publish(request.user):
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
    teacher_form = TeacherForm(
        bound_data,
        request.FILES or None,
        document=document or None,
        publishing=publishing,
    )
    if staged_error:
        teacher_form.add_error(None, staged_error)
    existing_courses = [*document.get("courses", [])]
    max_tabs = max(1, min(int(getattr(settings, "SDG4_MAX_TABS", DEFAULT_MAX_TABS)), 200))
    invalid_tab_count = False
    if bound_data is not None:
        submitted_count = bound_data.get("course-TOTAL_FORMS")
        if submitted_count is None:
            submitted_slots = [
                int(match.group(1))
                for key in bound_data
                if (match := re.match(r"course-(\d+)-", key))
            ]
            tab_count = max([len(existing_courses), *submitted_slots, 1])
        else:
            try:
                tab_count = int(submitted_count)
            except (TypeError, ValueError):
                tab_count = 0
                invalid_tab_count = True
        if tab_count < 0 or tab_count > max_tabs:
            invalid_tab_count = True
            tab_count = max(0, min(tab_count, max_tabs))
    else:
        tab_count = max(1, len(existing_courses))
    existing_courses.extend({} for _index in range(tab_count - len(existing_courses)))
    bundles = []
    for slot in range(1, tab_count + 1):
        course = existing_courses[slot - 1]
        prefix = f"course-{slot}"
        deleted = bool(bound_data and bound_data.get(f"{prefix}-DELETE"))
        course_form = CourseForm(
            bound_data,
            request.FILES or None,
            prefix=prefix,
            course=course,
            publishing=publishing,
        )
        course_type = request.POST.get(f"{prefix}-type", course.get("type", "lecture"))
        bulk_count = len(request.FILES.getlist(f"{prefix}-lecture_files")) if request.method == "POST" else 0
        formset = Materials(
            request.POST if request.method == "POST" else None,
            request.FILES if request.method == "POST" else None,
            prefix=f"materials-{slot}",
            materials=course.get("materials", []),
            course_type=course_type,
            publishing=publishing and not deleted,
            bulk_count=bulk_count,
        )
        bundles.append((course_form, formset, deleted))
    empty_course_form = CourseForm(prefix="course-__tab__", course={}, publishing=publishing)
    empty_materials = Materials(
        prefix="materials-__tab__",
        materials=[],
        course_type="lecture",
        publishing=publishing,
    )
    if request.method == "POST":
        checks = [teacher_form.is_valid()]
        active_tabs = 0
        for course_form, formset, deleted in bundles:
            if deleted:
                continue
            active_tabs += 1
            checks.extend((course_form.is_valid(), formset.is_valid()))
        if invalid_tab_count:
            teacher_form.add_error(None, _("The number of tabs is invalid."))
            checks.append(False)
        if publishing and not active_tabs:
            teacher_form.add_error(None, _("Add at least one tab before publishing."))
            checks.append(False)
        valid = all(checks)
        if staged_error:
            valid = False
        if row and request.POST.get("revision") != document["revision"]:
            teacher_form.add_error(None, _("This page was edited elsewhere. Reload it before saving."))
            valid = False
        if valid:
            try:
                saved_row, result = save_page(
                    row,
                    document,
                    teacher_form,
                    bundles,
                    moderation_status,
                    request.user,
                )
            except ValueError as exc:
                teacher_form.add_error(None, str(exc))
            else:
                if result["published"]:
                    target = public_url(result)
                else:
                    query = "submitted=1" if moderation_status == News.ModerationStatus.PENDING else "saved=1"
                    target = reverse("sdg4_edit", args=[saved_row.pk]) + f"?{query}"
                close_staged_uploads(staged_files)
                remove_staged_uploads(staged_uploads)
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"redirect": target})
                return redirect(target)
    close_staged_uploads(staged_files)
    return render(
        request,
        "edit",
        {
            "teacher_form": teacher_form,
            "document": document,
            "row": row,
            "moderation_status": moderation_status,
            "bundles": bundles,
            "empty_course_form": empty_course_form,
            "empty_materials": empty_materials,
            "max_tabs": max_tabs,
            "staged_upload_tokens": [token for token, _payload in staged_uploads],
        },
    )
