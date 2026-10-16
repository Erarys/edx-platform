"""Versioned documents in News.content; no new database tables are required.

Never use News.title for a display name here: it is an immutable, reserved key.
Row locks serialize edits and counters, so concurrent requests cannot lose counts.
"""

import json
import logging
from urllib.parse import parse_qs, urlsplit

from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import Http404
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from ..models import News
from .constants import FACULTIES, NEWS_PREFIX

logger = logging.getLogger(__name__)


def language_code(value=None):
    """Kazakh is kk in Open edX; accept kz for imported content as well."""
    code = (value or get_language() or "ru").lower().replace("_", "-").split("-")[0]
    return "kk" if code == "kz" else code if code in {"kk", "ru", "en"} else "en"


def localized(value, language=None):
    if not isinstance(value, dict):
        return ""
    return value.get(language_code(language)) or value.get("ru") or value.get("en") or value.get("kk") or ""


def alphabetical_key(name):
    """Keep Ё and Kazakh letters in their alphabet positions, not Unicode order."""
    alphabets = {
        "en": "abcdefghijklmnopqrstuvwxyz",
        "ru": "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        "kk": "аәбвгғдеёжзийкқлмнңоөпрстуұүфхһцчшщъыіьэюя",
    }
    order = {letter: index for index, letter in enumerate(alphabets[language_code()])}
    return tuple((0, order[letter]) if letter in order else (1, ord(letter)) for letter in name.casefold())


def read_document(row):
    try:
        document = json.loads(row.content)
        if (
            not isinstance(document, dict)
            or document.get("schema") != 1
            or document.get("faculty") not in dict(FACULTIES)
            or not isinstance(document.get("profile"), dict)
            or not isinstance(document.get("courses"), list)
            or len(document["courses"]) != 3
            or not all(isinstance(course, dict) for course in document["courses"])
            or row.title != document_key(document)
        ):
            raise ValueError("Invalid SDG 4 document")
        return document
    except (ValueError, TypeError, KeyError):
        logger.warning("Invalid SDG 4 content in News %s", row.pk)
        raise Http404 from None


def document_key(document):
    return f"{NEWS_PREFIX}{document['faculty']}/{document['slug']}"


def public_url(document):
    return reverse("sdg4_teacher", kwargs={"faculty": document["faculty"], "teacher": document["slug"]})


def documents(include_drafts=False):
    """Small editorial catalogue; do not mix the reserved rows with ordinary news."""
    result = []
    for row in News.objects.filter(title__startswith=NEWS_PREFIX):
        try:
            document = read_document(row)
        except Http404:
            continue
        if include_drafts or document.get("published"):
            result.append((row, document))
    return result


def find_page(faculty, teacher):
    row = News.objects.filter(title=f"{NEWS_PREFIX}{faculty}/{teacher}").first()
    if row is None:
        raise Http404
    document = read_document(row)
    if not document.get("published"):
        raise Http404
    return row, document


def can_manage(user):
    return user.is_authenticated and user.is_active and user.is_staff and user.has_perm("news.change_news")


def validate_media_url(value):
    """Existing site file URLs and HTTPS media are supported; no server-side fetch."""
    if not value:
        return value
    if "\\" in value or any(ord(char) < 32 for char in value):
        raise ValidationError(_("Use a site-relative file URL or an HTTPS URL."))
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ValidationError(_("Use a site-relative file URL or an HTTPS URL.")) from None
    if value.startswith("/") and not value.startswith("//") and not parsed.netloc:
        return value
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError(_("Use a site-relative file URL or an HTTPS URL."))
    return value


def youtube_embed(value):
    """Build an embed from a video ID, never from editor-supplied iframe HTML."""
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "youtu.be", "www.youtube-nocookie.com"}:
        return ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/")
    elif parsed.path.startswith(("/embed/", "/shorts/")):
        video_id = parsed.path.split("/")[2]
    else:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    if len(video_id) != 11 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in video_id):
        return ""
    return f"https://www.youtube-nocookie.com/embed/{video_id}?cc_load_policy=1"


def file_url(material, kind="file"):
    path = material.get(f"{kind}_path")
    return default_storage.url(path) if path else material.get(f"{kind}_url", "")


@transaction.atomic
def increment(row_id, course_number, metric):
    if metric not in {"views", "downloads"} or course_number not in {1, 2, 3}:
        raise ValueError("Invalid counter")
    row = News.objects.select_for_update().get(pk=row_id, title__startswith=NEWS_PREFIX)
    document = read_document(row)
    if not document.get("published"):
        raise Http404
    stats = document.setdefault("stats", {}).setdefault(str(course_number), {})
    stats[metric] = int(stats.get(metric, 0)) + 1
    row.content = json.dumps(document, ensure_ascii=False)
    # A public visit must not change the editorial update timestamp.
    row.save(update_fields=["content"])
    return document
