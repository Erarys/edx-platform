"""Regression coverage for the teacher-first OpenLektorium showcase."""

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.template import Context
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from mako.lookup import TemplateLookup

from lms.djangoapps.news.models import News

from .. import views
from ..constants import FACULTIES, LANGUAGES, NEWS_PREFIX, PUBLISHERS_GROUP
from ..data import language_code, read_document
from ..forms import MaterialForm


def template_renderer(request, template, context, status=200):
    root = Path(__file__).resolve().parents[4] / "templates"
    lookup = TemplateLookup(directories=[str(root)], input_encoding="utf-8", default_filters=["h"])
    lookup.put_string(
        "/main.html",
        '<!doctype html><html lang="${LANGUAGE_CODE}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>${self.pagetitle()}</title><%block name="head_extra"/></head>'
        '<body>${next.body()}</body></html>',
    )
    lookup.put_string("/static_content.html", '<%def name="url(path)">/static/${path}</%def>')
    context.update(
        request=request,
        csrf_token=get_token(request),
        sdg4_languages=LANGUAGES,
        sdg4_language=language_code(),
        can_manage_sdg4=views.can_manage(request.user),
        can_moderate_sdg4=views.can_moderate(request.user),
        can_publish_sdg4=views.can_publish(request.user),
        LANGUAGE_CODE=translation.get_language(),
    )
    return HttpResponse(
        lookup.get_template(f"news/sdg4/{template}.html").render(**Context(context).flatten()),
        status=status,
    )


def pdf(name="lecture.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n%%EOF", "application/pdf")


def pptx(name="slides.pptx"):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<presentation/>")
    return SimpleUploadedFile(name, stream.getvalue())


def course_payload(action="publish", name="Иванова Алия Сергеевна"):
    data = {
        "name_ru": name,
        "position_ru": "Старший преподаватель",
        "degree_ru": "Магистр",
        "department_ru": "Кафедра информатики",
        "faculty": FACULTIES[0][0],
        "mooc_url": "",
        "action": action,
    }
    for slot in range(1, 4):
        prefix = f"course-{slot}"
        data.update(
            {
                f"{prefix}-type": "lecture",
                f"{prefix}-language": "ru",
                f"{prefix}-programme": "6B06102",
                f"{prefix}-license": "by",
                f"{prefix}-topic_ru": f"Тема {slot}",
                f"{prefix}-discipline_ru": "Информатика",
                f"{prefix}-annotation_ru": f"Аннотация {slot}",
                f"{prefix}-lecture_files": pdf(f"lecture-{slot}.pdf"),
                f"materials-{slot}-TOTAL_FORMS": "0",
                f"materials-{slot}-INITIAL_FORMS": "0",
                f"materials-{slot}-MIN_NUM_FORMS": "1",
                f"materials-{slot}-MAX_NUM_FORMS": "200",
            }
        )
    return data


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
        self.staff = get_user_model().objects.create_user("staff", is_staff=True)
        self.client.force_login(self.staff)

    def publish(self, **overrides):
        data = course_payload()
        data.update(overrides)
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.order_by("-pk").first()
        return row, read_document(row), response.url

    def create_media_publication(self, slug, kind="presentation", name="Один Автор", author=None):
        extension = ".pdf" if kind == "presentation" else ".mp4"
        content = b"%PDF-1.4\n%%EOF" if kind == "presentation" else b"\x00\x00\x00\x18ftypisomvideo"
        path = default_storage.save(f"sdg4/legacy/{slug}{extension}", SimpleUploadedFile(slug + extension, content))
        document = {
            "schema": 3,
            "faculty": FACULTIES[0][0],
            "slug": slug,
            "published": True,
            "profile": {
                "name": {"ru": name},
                "position": {"ru": "Преподаватель"},
                "degree": {"ru": "Магистр"},
                "department": {"ru": "Кафедра"},
            },
            "article_title": {"ru": f"Скрытое название {slug}"},
            "courses": [],
            "resources": [],
            "article": [{"id": slug, "kind": kind, "file_path": path, "title": slug}],
            "stats": {},
        }
        return News.objects.create(
            title=f"{NEWS_PREFIX}{document['faculty']}/{slug}",
            content=json.dumps(document),
            content_type=News.ContentType.SDG4,
            moderation_status=News.ModerationStatus.PUBLISHED,
            published_at=timezone.now(),
            author=author or self.staff,
        )

    def test_editor_exposes_three_tabs_and_only_presentation_or_video(self):
        response = self.client.get(reverse("sdg4_create"))
        self.assertContains(response, "Tab 1")
        self.assertContains(response, "Tab 2")
        self.assertContains(response, "Tab 3")
        self.assertContains(response, "Presentation course", count=3)
        self.assertContains(response, "Video course", count=3)
        self.assertNotContains(response, 'data-add-kind="text"')
        self.assertNotContains(response, 'data-add-kind="image"')
        self.assertNotContains(response, 'data-add-kind="audio"')
        self.assertContains(response, 'data-upload-limit="1073741824"')
        self.assertContains(response, "data-upload-progress")

    def test_publishing_creates_exactly_three_generic_public_tabs(self):
        row, document, url = self.publish()
        self.assertEqual(document["schema"], 3)
        self.assertEqual(len(document["courses"]), 3)
        self.assertEqual(row.moderation_status, News.ModerationStatus.PUBLISHED)
        response = self.client.get(url)
        self.assertContains(response, "Tab 1")
        self.assertContains(response, "Tab 2")
        self.assertContains(response, "Tab 3")
        self.assertNotContains(response, "Тема 1")
        self.assertContains(response, "Open presentation")
        self.assertContains(response, "Иванова Алия Сергеевна")

    def test_home_and_faculty_list_authors_not_publication_titles(self):
        self.create_media_publication("one")
        self.create_media_publication("two")
        response = self.client.get(reverse("sdg4_index"))
        self.assertContains(response, "Один Автор", count=2)  # byline and heading in one author card
        self.assertContains(response, "1 teachers")
        self.assertNotContains(response, "Скрытое название")
        self.assertContains(response, "Search teachers")
        faculty = self.client.get(reverse("sdg4_faculty", args=[FACULTIES[0][0]]))
        self.assertContains(faculty, "Один Автор", count=1)

    def test_three_existing_publications_are_three_tabs_for_one_author(self):
        rows = [self.create_media_publication(f"media-{number}") for number in range(1, 4)]
        document = read_document(rows[0])
        response = self.client.get(reverse("sdg4_teacher", args=[document["faculty"], document["slug"]]))
        for number in range(1, 4):
            self.assertContains(response, f"Tab {number}")
        self.assertNotContains(response, "Скрытое название")
        second = self.client.get(response.request["PATH_INFO"] + "?tab=2")
        self.assertContains(second, "media-2")

    def test_add_resources_reopens_and_prefills_all_existing_teacher_tabs(self):
        author = get_user_model().objects.create_user("lecturer")
        rows = [
            self.create_media_publication(f"existing-{number}", author=author)
            for number in range(1, 4)
        ]
        self.client.force_login(author)

        response = self.client.get(reverse("sdg4_create"))
        self.assertRedirects(response, reverse("sdg4_edit", args=[rows[0].pk]), fetch_redirect_response=False)
        sibling = self.client.get(reverse("sdg4_edit", args=[rows[1].pk]))
        self.assertRedirects(sibling, reverse("sdg4_edit", args=[rows[0].pk]), fetch_redirect_response=False)

        editor = self.client.get(response.url)
        self.assertContains(editor, f'action="{reverse("sdg4_edit", args=[rows[0].pk])}"')
        for number in range(1, 4):
            self.assertContains(editor, f'value="existing-{number}"')

    def test_upload_script_uses_unshadowed_form_action_attribute(self):
        script = (Path(__file__).resolve().parents[4] / "static" / "js" / "sdg4-editor.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("form.getAttribute('action')", script)
        self.assertNotIn("form.action || window.location.href", script)

    def test_video_tab_renders_player_and_requires_accessible_metadata(self):
        data = course_payload()
        data.pop("course-1-lecture_files")
        data["course-1-type"] = "video"
        data["materials-1-TOTAL_FORMS"] = "1"
        data.update(
            {
                "materials-1-0-title": "Видео 1",
                "materials-1-0-file_url": "https://youtu.be/abcdefghijk",
                "materials-1-0-youtube_captions": "on",
                "materials-1-0-duration": "125",
            }
        )
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        page = self.client.get(response.url)
        self.assertContains(page, "youtube-nocookie.com/embed/abcdefghijk")
        self.assertContains(page, "2 min 5 sec")

    def test_pptx_is_saved_and_public_page_uses_preview_route(self):
        data = course_payload()
        data["course-1-lecture_files"] = pptx()
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.get()
        document = read_document(row)
        item = document["courses"][0]["materials"][0]
        with patch.object(views, "presentation_converter", return_value="soffice"):
            page = self.client.get(response.url)
        preview_url = reverse(
            "sdg4_material_preview",
            args=[document["faculty"], document["slug"], 1, item["id"]],
        )
        self.assertContains(page, preview_url)

    def test_one_gigabyte_limit_is_the_default(self):
        class LargeUpload:
            name = "large.mp4"
            size = 1024 * 1024 * 1024 + 1

            @staticmethod
            def read(_size):
                return b""

            @staticmethod
            def seek(_position):
                return None

        with self.assertRaisesMessage(ValidationError, "1 GB"):
            MaterialForm.validate_upload(LargeUpload(), {".mp4"})

    def test_contributor_submits_complete_resources_then_staff_approves(self):
        author = get_user_model().objects.create_user("author")
        self.client.force_login(author)
        response = self.client.post(reverse("sdg4_create"), course_payload(action="submit"))
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.get()
        self.assertEqual(row.moderation_status, News.ModerationStatus.PENDING)
        self.assertEqual(self.client.get(reverse("sdg4_index")).status_code, 200)
        self.assertNotContains(self.client.get(reverse("sdg4_index")), "Иванова")
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse("sdg4_approve", args=[row.pk])).status_code, 302)
        self.assertContains(self.client.get(reverse("sdg4_index")), "Иванова")

    def test_trusted_publisher_can_publish_directly(self):
        author = get_user_model().objects.create_user("trusted")
        group = self.staff.groups.model.objects.create(name=PUBLISHERS_GROUP)
        group.user_set.add(author)
        self.client.force_login(author)
        response = self.client.post(reverse("sdg4_create"), course_payload())
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(News.objects.get().moderation_status, News.ModerationStatus.PUBLISHED)

    def test_anonymous_is_sent_to_login_and_inactive_user_sees_confirmation(self):
        self.client.logout()
        response = self.client.get(reverse("sdg4_create"))
        self.assertEqual(response.status_code, 302)
        inactive = get_user_model().objects.create_user("inactive", email="inactive@example.com", is_active=False)
        request = RequestFactory().get(reverse("sdg4_create"))
        request.user = inactive
        response = views.edit(request)
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "inactive@example.com", status_code=403)

    def test_author_can_delete_own_resource_and_files(self):
        row, document, _url = self.publish()
        path = document["courses"][0]["materials"][0]["file_path"]
        self.assertTrue(default_storage.exists(path))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("sdg4_delete", args=[row.pk]), {"confirm": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(News.objects.filter(pk=row.pk).exists())
        self.assertFalse(default_storage.exists(path))

    def test_views_and_downloads_are_counted_per_tab(self):
        row, document, url = self.publish()
        self.client.get(url)
        row.refresh_from_db()
        updated = read_document(row)
        self.assertEqual(updated["stats"]["1"]["views"], 1)
        item = updated["courses"][0]["materials"][0]
        download = reverse("sdg4_download", args=[document["faculty"], document["slug"], 1, item["id"]])
        response = self.client.get(download)
        response.close()
        row.refresh_from_db()
        self.assertEqual(read_document(row)["stats"]["1"]["downloads"], 1)

    def test_ajax_upload_returns_redirect_json(self):
        response = self.client.post(
            reverse("sdg4_create"),
            course_payload(),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertIn("redirect", response.json())

    def test_incomplete_submission_can_be_saved_as_draft_but_not_published(self):
        draft = {
            "name_ru": "Черновик",
            "faculty": FACULTIES[0][0],
            "action": "draft",
        }
        for slot in range(1, 4):
            draft.update(
                {
                    f"materials-{slot}-TOTAL_FORMS": "0",
                    f"materials-{slot}-INITIAL_FORMS": "0",
                    f"materials-{slot}-MIN_NUM_FORMS": "1",
                    f"materials-{slot}-MAX_NUM_FORMS": "200",
                }
            )
        response = self.client.post(reverse("sdg4_create"), draft)
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.get()
        self.assertEqual(row.moderation_status, News.ModerationStatus.DRAFT)
        document = read_document(row)
        self.assertEqual(len(document["courses"]), 3)
        self.assertEqual(self.client.get(reverse("sdg4_teacher", args=[document["faculty"], document["slug"]])).status_code, 404)
