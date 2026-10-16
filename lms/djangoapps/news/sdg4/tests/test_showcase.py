"""Exercise real ORM, forms, multipart uploads, permissions and public routes."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.template import Context
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import translation
from mako.lookup import TemplateLookup

from lms.djangoapps.news.models import News

from .. import views
from ..constants import FACULTIES, LANGUAGES, NEWS_PREFIX
from ..data import (
    alphabetical_key,
    increment,
    language_code,
    localized,
    read_document,
    validate_media_url,
    youtube_embed,
)
from ..forms import Materials


def template_renderer(request, template, context):
    """Render real showcase templates, substituting only the full LMS shell."""
    root = Path(__file__).resolve().parents[4] / "templates"
    lookup = TemplateLookup(directories=[str(root)], input_encoding="utf-8", default_filters=["h"])
    lookup.put_string(
        "/main.html",
        '<!doctype html><html lang="${LANGUAGE_CODE}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${self.pagetitle()}</title><%block name="head_extra"/></head><body>${next.body()}</body></html>',
    )
    lookup.put_string("/static_content.html", '<%def name="url(path)">/static/${path}</%def>')
    context.update(
        request=request,
        csrf_token=get_token(request),
        sdg4_languages=LANGUAGES,
        sdg4_language=language_code(),
        can_manage_sdg4=views.can_manage(request.user),
        LANGUAGE_CODE=translation.get_language(),
    )
    # Match edxmako's flattened Django context, including True/False/None.
    # A named Mako block forwards these values as **pageargs to its override.
    return HttpResponse(lookup.get_template(f"news/sdg4/{template}.html").render(**Context(context).flatten()))


def payload(published=True, course_type="lecture"):
    result = {"faculty": FACULTIES[0][0], "slug": "ivanov-i-i", "published": "on" if published else ""}
    for name in ("name", "position", "degree", "department"):
        for lang, _label in LANGUAGES:
            result[f"{name}_{lang}"] = f"{name} {lang}"
    for slot in range(1, 4):
        prefix = f"course-{slot}"
        result.update(
            {
                f"{prefix}-type": course_type,
                f"{prefix}-language": "ru",
                f"{prefix}-programme": "6B06102",
                f"{prefix}-license": "by",
            }
        )
        for name in ("topic", "discipline", "annotation"):
            for lang, _label in LANGUAGES:
                result[f"{prefix}-{name}_{lang}"] = f"{name} {lang} {slot}"
        mp = f"materials-{slot}"
        result.update(
            {
                f"{mp}-TOTAL_FORMS": "1",
                f"{mp}-INITIAL_FORMS": "0",
                f"{mp}-MIN_NUM_FORMS": "1",
                f"{mp}-MAX_NUM_FORMS": "200",
                f"{mp}-0-title": f"Lesson {slot}",
            }
        )
        if course_type == "lecture":
            result[f"{mp}-0-file"] = SimpleUploadedFile(f"lecture-{slot}.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")
        else:
            result.update(
                {
                    f"{mp}-0-file_url": "https://www.youtube.com/watch?v=abcdefghijk",
                    f"{mp}-0-youtube_captions": "on",
                    f"{mp}-0-duration": "125",
                }
            )
    return result


def russian_only(data):
    return {key: value for key, value in data.items() if not key.endswith(("_kk", "_en")) and key != "slug"}


class ShowcaseTests(TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[5] / "test_root"
        temp_root.mkdir(exist_ok=True)
        self.files = tempfile.TemporaryDirectory(prefix="sdg4-", dir=temp_root)
        self.addCleanup(self.files.cleanup)
        self.media = override_settings(MEDIA_ROOT=self.files.name)
        self.media.enable()
        self.addCleanup(self.media.disable)
        self.renderer = patch.object(views, "render", template_renderer)
        self.renderer.start()
        self.addCleanup(self.renderer.stop)
        self.editor = get_user_model().objects.create_superuser("editor", "editor@example.com", "test")
        self.client.force_login(self.editor)

    def publish(self, course_type="lecture"):
        response = self.client.post(reverse("sdg4_create"), payload(course_type=course_type))
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.get(title__startswith=NEWS_PREFIX)
        return row, read_document(row), response.url

    def edit_payload(self, row, document):
        data = payload(published=document["published"])
        data["revision"] = row.updated_at.isoformat()
        data["slug"] = document["slug"]
        for slot, course in enumerate(document["courses"], 1):
            prefix = f"materials-{slot}"
            data[f"{prefix}-INITIAL_FORMS"] = "1"
            data.pop(f"{prefix}-0-file", None)
        return data

    def test_create_upload_and_anonymous_pdf_access_with_counts(self):
        row, document, url = self.publish()
        self.assertEqual(len(document["courses"]), 3)
        self.assertTrue(document["slug"].endswith(f"-{row.pk}"))
        self.client.logout()
        response = self.client.get(url)
        self.assertContains(response, "topic en 1")
        self.assertContains(response, 'aria-current="page"', count=1)
        self.assertContains(response, "6B06102")
        item = document["courses"][0]["materials"][0]
        args = [document["faculty"], document["slug"], 1, item["id"]]
        response = self.client.get(reverse("sdg4_material", args=args))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF-"))
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
        response = self.client.get(reverse("sdg4_download", args=args))
        self.assertIn("attachment", response["Content-Disposition"])
        b"".join(response.streaming_content)
        row.refresh_from_db()
        self.assertEqual(read_document(row)["stats"]["1"], {"views": 1, "downloads": 1})
        self.assertEqual(self.client.get(url + "?course=4").status_code, 404)
        self.assertEqual(self.client.get(url + "?course=bad").status_code, 404)

    def test_all_faculties_even_without_teachers(self):
        self.client.logout()
        response = self.client.get(reverse("sdg4_index"))
        self.assertEqual(response.status_code, 200)
        for faculty, _label in FACULTIES:
            self.assertContains(response, reverse("sdg4_faculty", args=[faculty]))
            self.assertEqual(self.client.get(reverse("sdg4_faculty", args=[faculty])).status_code, 200)
        self.assertEqual(self.client.get("/sdg4/unknown/").status_code, 404)

    def test_editor_available_to_every_signed_in_user_and_requires_csrf(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("sdg4_manage")).status_code, 403)
        ordinary_user = get_user_model().objects.create_user("teacher")
        self.client.force_login(ordinary_user)
        self.assertEqual(self.client.get(reverse("sdg4_manage")).status_code, 200)
        self.assertEqual(self.client.get(reverse("sdg4_create")).status_code, 200)
        row, _document, url = self.publish()
        self.assertContains(self.client.get(url), reverse("sdg4_edit", args=[row.pk]))
        other_user = get_user_model().objects.create_user("another-teacher")
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(reverse("sdg4_edit", args=[row.pk])).status_code, 200)
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.editor)
        self.assertEqual(secured.post(reverse("sdg4_create"), payload()).status_code, 403)

    def test_three_complete_courses_required_and_annotation_limit(self):
        data = payload()
        data["course-3-topic_ru"] = ""
        data["course-1-annotation_ru"] = "x" * 501
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please correct the errors")
        self.assertEqual(News.objects.count(), 0)

    def test_russian_only_publication_and_fallback_until_translations_added(self):
        data = russian_only(payload())
        data["action"] = "publish"
        for slot in range(1, 4):
            data.pop(f"course-{slot}-language")
            data.pop(f"course-{slot}-license")
        data["name_ru"] = "Иванова Алия Сергеевна"
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.get()
        document = read_document(row)
        self.assertEqual(document["slug"], f"ivanova-aliya-sergeevna-{row.pk}")
        self.assertEqual(document["profile"]["name"]["en"], "")
        self.assertEqual([course["language"] for course in document["courses"]], ["ru"] * 3)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "kk"
        self.assertContains(self.client.get(response.url), "Иванова Алия Сергеевна")
        self.assertContains(self.client.get(response.url), "topic ru 1")

    def test_save_incomplete_draft_with_bulk_pdf_upload(self):
        data = {"name_ru": "Иванов И. И.", "faculty": FACULTIES[0][0], "action": "draft"}
        for slot in range(1, 4):
            data.update({f"materials-{slot}-TOTAL_FORMS": "0", f"materials-{slot}-INITIAL_FORMS": "0"})
        data["course-1-lecture_files"] = [
            SimpleUploadedFile("01_Введение.pdf", b"%PDF-1.4\n%%EOF"),
            SimpleUploadedFile("02_Практика.pdf", b"%PDF-1.4\n%%EOF"),
        ]
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.get()
        document = read_document(row)
        self.assertFalse(document["published"])
        self.assertEqual(
            [item["title"] for item in document["courses"][0]["materials"]], ["01 Введение", "02 Практика"]
        )
        self.assertEqual(document["courses"][1]["materials"], [])
        self.assertContains(self.client.get(response.url), "Draft saved")
        self.assertEqual(
            self.client.get(reverse("sdg4_teacher", args=[document["faculty"], document["slug"]])).status_code, 404
        )

    def test_bulk_pdf_can_publish_without_individual_lesson_forms(self):
        data = russian_only(payload())
        data["action"] = "publish"
        for slot in range(1, 4):
            upload = data.pop(f"materials-{slot}-0-file")
            data.pop(f"materials-{slot}-0-title")
            data[f"materials-{slot}-TOTAL_FORMS"] = "0"
            data[f"course-{slot}-lecture_files"] = [upload]
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertTrue(read_document(News.objects.get())["published"])

    def test_bulk_pdf_validates_every_file(self):
        data = russian_only(payload())
        data["course-1-lecture_files"] = [
            SimpleUploadedFile("good.pdf", b"%PDF-1.4\n%%EOF"),
            SimpleUploadedFile("bad.pdf", b"not a PDF"),
        ]
        self.assertContains(self.client.post(reverse("sdg4_create"), data), "valid PDF")
        self.assertFalse(News.objects.exists())

    def test_add_translations_later_without_changing_url_or_erasing_them(self):
        response = self.client.post(reverse("sdg4_create"), russian_only(payload()))
        self.assertEqual(response.status_code, 302, response.content.decode())
        url = response.url
        row = News.objects.get()
        document = read_document(row)
        data = russian_only(self.edit_payload(row, document))
        data.update({"name_en": "English name", "course-1-topic_en": "English topic", "action": "save"})
        response = self.client.post(reverse("sdg4_edit", args=[row.pk]), data)
        self.assertEqual(response.url, url)
        self.assertContains(self.client.get(url), "English name")
        row.refresh_from_db()
        document = read_document(row)
        data = russian_only(self.edit_payload(row, document))
        data.update({"name_ru": "Новое имя", "action": "save"})
        self.assertEqual(self.client.post(reverse("sdg4_edit", args=[row.pk]), data).status_code, 302)
        row.refresh_from_db()
        updated = read_document(row)
        self.assertEqual(updated["profile"]["name"]["en"], "English name")
        self.assertEqual(updated["courses"][0]["topic"]["en"], "English topic")
        self.assertTrue(updated["published"])

    def test_simple_editor_uses_optional_translations_and_multiple_upload(self):
        response = self.client.get(reverse("sdg4_create"))
        self.assertContains(response, '<input type="text" name="name_ru"')
        self.assertContains(response, 'class="sdg4-translations"')
        self.assertNotContains(response, 'class="sdg4-translations" open')
        self.assertRegex(response.content.decode(), r'<input[^>]*name="course-1-lecture_files"[^>]*\bmultiple\b')
        self.assertNotContains(response, 'id="id_slug"')

    def test_empty_or_deleted_materials_cannot_be_published(self):
        for deleted in (False, True):
            data = payload()
            if deleted:
                data["materials-3-0-DELETE"] = "on"
            else:
                data["materials-3-TOTAL_FORMS"] = "0"
            self.assertEqual(self.client.post(reverse("sdg4_create"), data).status_code, 200)
            self.assertFalse(News.objects.exists())

    def test_video_subtitles_duration_and_safe_embed(self):
        data = payload(course_type="video")
        data.pop("materials-2-0-youtube_captions")
        self.assertEqual(self.client.post(reverse("sdg4_create"), data).status_code, 200)
        self.assertFalse(News.objects.exists())
        _row, _doc, url = self.publish(course_type="video")
        self.client.logout()
        response = self.client.get(url + "?course=2")
        self.assertContains(response, "https://www.youtube-nocookie.com/embed/abcdefghijk?cc_load_policy=1")
        self.assertContains(response, "2 min 5 sec")
        self.assertNotContains(response, "Download PDF")

    def test_draft_hidden_and_url_fixed_after_name_change(self):
        response = self.client.post(reverse("sdg4_create"), payload(published=False))
        row = News.objects.get()
        self.assertRedirects(response, reverse("sdg4_edit", args=[row.pk]) + "?saved=1")
        doc = read_document(row)
        url = reverse("sdg4_teacher", args=[doc["faculty"], doc["slug"]])
        self.assertEqual(self.client.get(url).status_code, 404)
        data = self.edit_payload(row, doc)
        data.update(published="on", faculty="law", slug="different", name_en="New name")
        response = self.client.post(reverse("sdg4_edit", args=[row.pk]), data)
        self.assertEqual(response.url, url)
        self.assertContains(self.client.get(url), "New name")

    def test_edit_preserves_files_counters_and_rejects_stale_revision(self):
        row, doc, url = self.publish()
        data = self.edit_payload(row, doc)
        increment(row.pk, 1, "views")
        response = self.client.post(reverse("sdg4_edit", args=[row.pk]), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        row.refresh_from_db()
        current = read_document(row)
        self.assertEqual(current["stats"]["1"]["views"], 1)
        self.assertEqual(
            current["courses"][0]["materials"][0]["file_path"], doc["courses"][0]["materials"][0]["file_path"]
        )
        self.assertEqual(current["courses"][0]["published_at"], doc["courses"][0]["published_at"])
        response = self.client.post(reverse("sdg4_edit", args=[row.pk]), data)
        self.assertContains(response, "edited elsewhere")

    def test_material_type_change_needs_replacement_and_pdf_signature(self):
        row, doc, _url = self.publish()
        data = self.edit_payload(row, doc)
        data["course-1-type"] = "video"
        self.assertContains(self.client.post(reverse("sdg4_edit", args=[row.pk]), data), "file type does not match")
        data = payload()
        data["materials-1-0-file"] = SimpleUploadedFile("fake.pdf", b"not pdf", "application/pdf")
        self.assertContains(self.client.post(reverse("sdg4_create"), data), "valid PDF")

    def test_language_switch_keeps_selected_course(self):
        _row, _doc, url = self.publish()
        self.client.logout()
        response = self.client.post(reverse("sdg4_language"), {"language": "kk", "next": url + "?course=3"})
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response.url)
        self.assertContains(response, "topic kk 3")
        self.assertContains(response, 'lang="kk"')

    def test_logged_in_language_switch_updates_platform_preference(self):
        with patch.object(views, "save_language_preference") as save:
            response = self.client.post(reverse("sdg4_language"), {"language": "ru", "next": "https://evil.example/"})
        save.assert_called_once_with(self.editor, "ru")
        self.assertEqual(response.url, "/")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ru")
        self.assertEqual(self.client.post(reverse("sdg4_language"), {"language": "fr"}).status_code, 400)

    def test_uploaded_video_requires_webvtt_and_renders_track(self):
        data = payload(course_type="video")
        for slot in range(1, 4):
            prefix = f"materials-{slot}-0"
            data.pop(f"{prefix}-file_url")
            data[f"{prefix}-file"] = SimpleUploadedFile("video.mp4", b"video bytes", "video/mp4")
            data[f"{prefix}-captions"] = SimpleUploadedFile(
                "captions.vtt", b"WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n", "text/vtt"
            )
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertContains(self.client.get(response.url), '<track kind="captions"')

    def test_oversized_and_missing_video_metadata_rejected(self):
        with override_settings(SDG4_MAX_UPLOAD_BYTES=2):
            self.assertContains(self.client.post(reverse("sdg4_create"), payload()), "upload limit")
        data = payload(course_type="video")
        data["materials-1-0-file_url"] = "https://open.kaznu.kz/media/video.mp4"
        data.pop("materials-1-0-duration")
        self.assertContains(self.client.post(reverse("sdg4_create"), data), "Subtitles are required")

    def test_malicious_urls_and_youtube_ids(self):
        for value in (
            "javascript:alert(1)",
            "//evil.example/video.mp4",
            "/\\evil.example/",
            "https://user:pass@example.com/file.pdf",
            "/file\n.pdf",
            "https://[invalid/file.pdf",
        ):
            with self.assertRaises(ValidationError):
                validate_media_url(value)
        self.assertEqual(youtube_embed("https://evil.example/embed/abcdefghijk"), "")
        self.assertEqual(youtube_embed("https://youtube.com/watch?v=bad"), "")
        self.assertTrue(youtube_embed("https://youtu.be/abcdefghijk").endswith("?cc_load_policy=1"))
        self.assertEqual(language_code("kz-KZ"), "kk")
        self.assertEqual(localized({"ru": "fallback"}, "kk"), "fallback")

    def test_alphabetical_order_in_kazakh_and_russian(self):
        with translation.override("kk"):
            self.assertEqual(sorted(["Болат", "Әлия", "Абай"], key=alphabetical_key), ["Абай", "Әлия", "Болат"])
        with translation.override("ru"):
            self.assertEqual(sorted(["Жуков", "Ёлкин", "Егоров"], key=alphabetical_key), ["Егоров", "Ёлкин", "Жуков"])

    def test_header_hook_accepts_django_context_pageargs_without_stray_title(self):
        response = self.client.get(reverse("sdg4_index"))
        html = response.content.decode()
        head, body = html.split("</head>", 1)
        self.assertIn("sdg4/showcase.css", head)
        self.assertEqual(body.count('rel="stylesheet"'), 0)
        self.assertTrue(body.split("<main", 1)[0].strip() == "<body>")

    def test_mixed_courses_preserve_material_order(self):
        data = payload()
        data["course-2-type"] = "video"
        data.pop("materials-2-0-file")
        data.update(
            {
                "materials-2-0-file_url": "https://youtu.be/abcdefghijk",
                "materials-2-0-youtube_captions": "on",
                "materials-2-0-duration": "60",
            }
        )
        data.update(
            {
                "materials-1-TOTAL_FORMS": "2",
                "materials-1-1-title": "Second lecture",
                "materials-1-1-file_url": "/media/second.pdf",
            }
        )
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        document = read_document(News.objects.get())
        self.assertEqual([course["type"] for course in document["courses"]], ["lecture", "video", "lecture"])
        self.assertEqual(
            [item["title"] for item in document["courses"][0]["materials"]], ["Lesson 1", "Second lecture"]
        )

    def test_formset_limit_blocks_unbounded_post(self):
        formset = Materials({"m-TOTAL_FORMS": "10000", "m-INITIAL_FORMS": "0"}, prefix="m")
        self.assertFalse(formset.is_valid())
        self.assertEqual(len(formset.forms), 200)

    def test_untrusted_text_escaped_in_templates(self):
        data = payload()
        data["name_en"] = '<script>alert("x")</script>'
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response.url)
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, "&lt;script&gt;")
