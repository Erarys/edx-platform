"""Exercise real ORM, forms, multipart uploads, permissions and public routes."""

import base64
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
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
from ..forms import MaterialForm, Materials, SimpleTeacherForm


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
        can_moderate_sdg4=views.can_moderate(request.user),
        can_publish_sdg4=views.can_publish(request.user),
        LANGUAGE_CODE=translation.get_language(),
    )
    # Match edxmako's flattened Django context, including True/False/None.
    # A named Mako block forwards these values as **pageargs to its override.
    return HttpResponse(lookup.get_template(f"news/sdg4/{template}.html").render(**Context(context).flatten()))


def payload(published=True):
    return {
        "name_ru": "Иванова Алия Сергеевна",
        "faculty": FACULTIES[0][0],
        "action": "publish" if published else "draft",
        "resource_url": "https://open.kaznu.kz/courses/",
        "resource_title": "Открытый курс",
    }


def pdf(name="Презентация.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n%%EOF", "application/pdf")


def pptx(macro=False):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<presentation/>")
        if macro:
            archive.writestr("ppt/vbaProject.bin", b"macro")
    return SimpleUploadedFile("Лекция.pptx", stream.getvalue())


def png(name="Иллюстрация.png"):
    content = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    return SimpleUploadedFile(name, content, "image/png")


def article_payload():
    return {
        "article-TOTAL_FORMS": "5",
        "article-INITIAL_FORMS": "0",
        "article-MIN_NUM_FORMS": "0",
        "article-MAX_NUM_FORMS": "1000",
        "article-0-kind": "text",
        "article-0-text": "Первый абзац\nВторая строка",
        "article-0-ORDER": "1",
        "article-1-kind": "image",
        "article-1-file": png(),
        "article-1-caption": "Подпись к изображению",
        "article-1-ORDER": "2",
        "article-2-kind": "presentation",
        "article-2-file": pdf("Слайды.pdf"),
        "article-2-ORDER": "3",
        "article-3-kind": "audio",
        "article-3-file": SimpleUploadedFile("Лекция.mp3", b"ID3" + b"audio"),
        "article-3-ORDER": "4",
        "article-4-kind": "video",
        "article-4-file": SimpleUploadedFile("Видео.mp4", b"\x00\x00\x00\x18ftypisom" + b"video"),
        "article-4-ORDER": "5",
    }


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
        self.editor = get_user_model().objects.create_user("editor", is_staff=True)
        self.client.force_login(self.editor)

    def publish(self, **overrides):
        data = {**payload(), **overrides}
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.get(title__startswith=NEWS_PREFIX)
        return row, read_document(row), response.url

    def edit_payload(self, row, document):
        data = {
            "faculty": document["faculty"],
            "revision": row.updated_at.isoformat(),
            "action": "save",
        }
        for key in ("name", "position", "degree", "department"):
            data[f"{key}_ru"] = document["profile"].get(key, {}).get("ru", "")
        link = next(
            (item for item in document.get("resources", []) if item.get("kind") == "link"),
            {},
        )
        data.update(resource_url=link.get("file_url", ""), resource_title=link.get("title", ""))
        return data

    def legacy_page(self, video=False):
        """An existing schema-1 document, independent of the new editor."""
        path = default_storage.save("sdg4/legacy/lecture.pdf", pdf())
        courses = []
        for number in range(1, 4):
            item = {
                "id": f"lesson{number}",
                "title": f"Lesson {number}",
                "file_path": path,
                "duration": None,
                "youtube_captions": False,
            }
            if video:
                item = {
                    "id": f"lesson{number}",
                    "title": "Video",
                    "file_url": "https://youtu.be/abcdefghijk",
                    "duration": 125,
                    "youtube_captions": True,
                }
            courses.append(
                {
                    "type": "video" if video else "lecture",
                    "language": "ru",
                    "programme": "6B06102",
                    "license": "by",
                    "topic": {"ru": f"Тема {number}", "en": f"Topic {number}"},
                    "discipline": {"ru": "Дисциплина"},
                    "annotation": {"ru": "Описание"},
                    "published_at": "2026-10-01",
                    "materials": [item],
                }
            )
        document = {
            "schema": 1,
            "faculty": FACULTIES[0][0],
            "slug": "legacy-teacher",
            "published": True,
            "profile": {
                "name": {"ru": "Преподаватель", "en": "Teacher"},
                "mooc_url": "",
            },
            "courses": courses,
            "stats": {"1": {"views": 7, "downloads": 4}},
        }
        row = News.objects.create(
            title=f"{NEWS_PREFIX}{document['faculty']}/{document['slug']}",
            content=json.dumps(document),
            content_type=News.ContentType.SDG4,
            moderation_status=News.ModerationStatus.PUBLISHED,
        )
        return (
            row,
            document,
            reverse("sdg4_teacher", args=[document["faculty"], document["slug"]]),
        )

    def test_link_only_publication_requires_no_course_or_teacher_metadata(self):
        _row, document, url = self.publish()
        self.assertEqual(document["schema"], 3)
        self.assertEqual(_row.content_type, News.ContentType.SDG4)
        self.assertEqual(document["courses"], [])
        self.assertEqual(document["profile"]["position"]["ru"], "")
        self.client.logout()
        self.assertContains(self.client.get(url), "Иванова Алия Сергеевна")
        link = document["resources"][0]
        route = reverse("sdg4_resource", args=[document["faculty"], document["slug"], link["id"]])
        self.assertRedirects(self.client.get(route), link["file_url"], fetch_redirect_response=False)

    def test_pdf_only_publication_multiple_uploads_and_anonymous_download(self):
        row, doc, url = self.publish(
            resource_url="",
            presentations=[pdf("01_Введение.pdf"), pdf("02_Практика.pdf")],
        )
        self.assertEqual([item["title"] for item in doc["resources"]], ["01 Введение", "02 Практика"])
        self.client.logout()
        self.assertContains(self.client.get(url), "01 Введение")
        item = doc["resources"][0]
        args = [doc["faculty"], doc["slug"], item["id"]]
        response = self.client.get(reverse("sdg4_resource", args=args))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF-"))
        response = self.client.get(reverse("sdg4_resource_download", args=args))
        self.assertIn("attachment", response["Content-Disposition"])
        b"".join(response.streaming_content)
        row.refresh_from_db()
        self.assertEqual(read_document(row)["stats"][item["id"]], {"views": 1, "downloads": 1})

    def test_mixed_article_blocks_are_ordered_rendered_and_served(self):
        data = {
            **payload(),
            "resource_url": "",
            "article_title_ru": "Новая статья",
            "cover_image": png("Обложка.png"),
            **article_payload(),
        }
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.get()
        document = read_document(row)
        self.assertEqual(document["schema"], 3)
        self.assertEqual([block["kind"] for block in document["article"]], [
            "text", "image", "presentation", "audio", "video",
        ])
        self.assertEqual(document["article_title"]["ru"], "Новая статья")
        self.assertTrue(all(block.get("id") for block in document["article"]))
        self.assertEqual(row.author, self.editor)
        self.assertIsNotNone(row.published_at)
        self.assertEqual(row.view_count, 0)
        self.assertTrue(row.cover_image.name.startswith(f"sdg4/{row.pk}/cover/"))

        self.client.logout()
        page = self.client.get(response.url)
        self.assertContains(page, "Новая статья")
        self.assertContains(page, "Первый абзац")
        self.assertContains(page, "Подпись к изображению")
        self.assertContains(page, "<audio", html=False)
        self.assertContains(page, "<video", html=False)
        self.assertContains(page, "editor")
        self.assertContains(page, "ago")
        self.assertContains(page, "1 view")
        row.refresh_from_db()
        self.assertEqual(row.view_count, 1)
        self.client.get(response.url)
        row.refresh_from_db()
        self.assertEqual(row.view_count, 2)

        cover = self.client.get(
            reverse("sdg4_cover", args=[document["faculty"], document["slug"]])
        )
        self.assertEqual(cover.status_code, 200)
        self.assertEqual(cover["Content-Type"], "image/png")
        self.assertEqual(cover["X-Content-Type-Options"], "nosniff")
        b"".join(cover.streaming_content)
        for block, content_type in zip(
            document["article"][1:],
            ("image/png", "application/pdf", "audio/mpeg", "video/mp4"),
        ):
            route = reverse(
                "sdg4_article_block",
                args=[document["faculty"], document["slug"], block["id"]],
            )
            media = self.client.get(route)
            self.assertEqual(media.status_code, 200)
            self.assertEqual(media["Content-Type"], content_type)
            self.assertEqual(media["X-Content-Type-Options"], "nosniff")
            b"".join(media.streaming_content)

    def test_first_article_image_is_the_feed_cover_when_no_cover_is_uploaded(self):
        response = self.client.post(
            reverse("sdg4_create"),
            {**payload(), "resource_url": "", **article_payload()},
        )
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.get()
        document = read_document(row)
        first_image = next(block for block in document["article"] if block["kind"] == "image")
        image_url = reverse(
            "sdg4_article_block",
            args=[document["faculty"], document["slug"], first_image["id"]],
        )
        landing = self.client.get(reverse("sdg4_index"))
        self.assertContains(landing, 'class="sdg4-feed-cover"')
        self.assertContains(landing, image_url)

        detail = self.client.get(response.url)
        self.assertNotContains(detail, "Permanent link for the application")
        self.assertNotContains(detail, 'class="sdg4-profile')

    def test_article_text_is_escaped_and_can_publish_without_legacy_resource(self):
        data = {
            **payload(),
            "resource_url": "",
            "article-TOTAL_FORMS": "1",
            "article-INITIAL_FORMS": "0",
            "article-MIN_NUM_FORMS": "0",
            "article-MAX_NUM_FORMS": "1000",
            "article-0-kind": "text",
            "article-0-text": '<img src=x onerror="alert(1)">',
            "article-0-ORDER": "1",
        }
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        page = self.client.get(response.url)
        self.assertNotContains(page, '<img src=x onerror="alert(1)">')
        self.assertContains(page, "&lt;img")

    def test_existing_article_blocks_can_be_reordered_and_removed_without_reupload(self):
        response = self.client.post(
            reverse("sdg4_create"),
            {**payload(), "resource_url": "", **article_payload()},
        )
        self.assertEqual(response.status_code, 302, response.content.decode())
        row = News.objects.get()
        document = read_document(row)
        data = self.edit_payload(row, document)
        data.update(
            {
                "resource_url": "",
                "article-TOTAL_FORMS": "5",
                "article-INITIAL_FORMS": "5",
                "article-MIN_NUM_FORMS": "0",
                "article-MAX_NUM_FORMS": "1000",
            }
        )
        for index, block in enumerate(document["article"]):
            data.update(
                {
                    f"article-{index}-block_id": block["id"],
                    f"article-{index}-kind": block["kind"],
                    f"article-{index}-text": block.get("text", ""),
                    f"article-{index}-title": block.get("title", ""),
                    f"article-{index}-caption": block.get("caption", ""),
                    f"article-{index}-ORDER": str(5 - index),
                }
            )
        data["article-2-DELETE"] = "on"
        response = self.client.post(reverse("sdg4_edit", args=[row.pk]), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        row.refresh_from_db()
        updated = read_document(row)
        self.assertEqual([block["kind"] for block in updated["article"]], ["video", "audio", "image", "text"])
        self.assertEqual(updated["article"][1]["file_path"], document["article"][3]["file_path"])

    def test_article_rejects_renamed_media_and_unpublished_file_routes(self):
        bad = {
            **payload(),
            "resource_url": "",
            "article-TOTAL_FORMS": "1",
            "article-INITIAL_FORMS": "0",
            "article-MIN_NUM_FORMS": "0",
            "article-MAX_NUM_FORMS": "1000",
            "article-0-kind": "audio",
            "article-0-file": SimpleUploadedFile("not-audio.mp3", b"not audio"),
            "article-0-ORDER": "1",
        }
        self.assertContains(self.client.post(reverse("sdg4_create"), bad), "not valid")
        self.assertFalse(News.objects.exists())

        draft = {
            **payload(False),
            "resource_url": "",
            "cover_image": png("Черновик.png"),
            **article_payload(),
        }
        response = self.client.post(reverse("sdg4_create"), draft)
        row = News.objects.get()
        document = read_document(row)
        block = document["article"][1]
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.get(
                reverse("sdg4_article_block", args=[document["faculty"], document["slug"], block["id"]])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("sdg4_cover", args=[document["faculty"], document["slug"]])).status_code,
            404,
        )

    def test_article_editor_exposes_all_block_types(self):
        response = self.client.get(reverse("sdg4_create"))
        for kind in ("text", "image", "presentation", "audio", "video"):
            self.assertContains(response, f'data-add-kind="{kind}"')
        self.assertContains(response, 'id="id_article-TOTAL_FORMS"')
        self.assertContains(response, "sdg4-empty-article-block")

    def test_powerpoint_supported_as_download_only(self):
        _row, doc, url = self.publish(
            resource_url="",
            presentations=[
                pptx(),
                SimpleUploadedFile("Слайды.ppt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"data"),
            ],
        )
        self.assertNotContains(self.client.get(url), "<object")
        for item in doc["resources"]:
            response = self.client.get(reverse("sdg4_resource", args=[doc["faculty"], doc["slug"], item["id"]]))
            self.assertIn("attachment", response["Content-Disposition"])
            b"".join(response.streaming_content)

    def test_invalid_pdf_powerpoint_and_macro_rejected(self):
        for upload in (
            SimpleUploadedFile("fake.pdf", b"bad"),
            SimpleUploadedFile("fake.pptx", b"bad"),
            SimpleUploadedFile("fake.ppt", b"bad"),
            SimpleUploadedFile("bad.html", b"html"),
            pptx(True),
        ):
            response = self.client.post(reverse("sdg4_create"), {**payload(), "presentations": [upload]})
            self.assertContains(response, "Please correct the errors")
            self.assertFalse(News.objects.exists())

    def test_every_upload_validated_and_limits_enforced(self):
        data = {
            **payload(),
            "presentations": [pdf(), SimpleUploadedFile("bad.pdf", b"bad")],
        }
        self.assertEqual(self.client.post(reverse("sdg4_create"), data).status_code, 200)
        with override_settings(SDG4_MAX_UPLOAD_BYTES=2):
            self.assertEqual(
                self.client.post(reverse("sdg4_create"), {**payload(), "presentations": [pdf()]}).status_code,
                200,
            )
        self.assertFalse(News.objects.exists())
        form = SimpleTeacherForm(payload(), {"presentations": [pdf() for _ in range(201)]})
        self.assertFalse(form.is_valid())
        self.assertIn("presentations", form.errors)

    def test_draft_can_have_no_resources_but_publication_cannot(self):
        data = {**payload(), "resource_url": ""}
        self.assertContains(self.client.post(reverse("sdg4_create"), data), "Add an article block")
        self.assertFalse(News.objects.exists())
        data["action"] = "draft"
        response = self.client.post(reverse("sdg4_create"), data)
        self.assertEqual(response.status_code, 302)
        row = News.objects.get()
        doc = read_document(row)
        self.assertFalse(doc["published"])
        self.assertContains(self.client.get(response.url), "Draft saved")
        self.assertEqual(
            self.client.get(reverse("sdg4_teacher", args=[doc["faculty"], doc["slug"]])).status_code,
            404,
        )
        self.assertNotContains(self.client.get(reverse("sdg4_index")), "Иванова Алия Сергеевна")

    def test_contributors_submit_own_materials_and_staff_approve_them(self):
        row, _doc, url = self.publish()
        contributor = get_user_model().objects.create_user("another-teacher")
        self.client.force_login(contributor)
        self.assertEqual(self.client.get(reverse("sdg4_edit", args=[row.pk])).status_code, 403)
        self.assertNotContains(self.client.get(url), reverse("sdg4_edit", args=[row.pk]))
        manage = self.client.get(reverse("sdg4_manage"))
        self.assertEqual(manage.status_code, 200)
        self.assertNotContains(manage, "Иванова Алия Сергеевна")
        self.assertEqual(self.client.get(reverse("sdg4_create")).status_code, 200)

        response = self.client.post(
            reverse("sdg4_create"),
            {**payload(), "name_ru": "Автор на модерации", "action": "publish"},
        )
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertIn("submitted=1", response.url)
        pending = News.objects.exclude(pk=row.pk).get()
        document = read_document(pending)
        self.assertEqual(pending.author, contributor)
        self.assertEqual(pending.moderation_status, News.ModerationStatus.PENDING)
        self.assertFalse(document["published"])
        public_url = reverse("sdg4_teacher", args=[document["faculty"], document["slug"]])
        self.assertEqual(self.client.get(public_url).status_code, 404)
        self.assertNotContains(self.client.get(reverse("sdg4_index")), "Автор на модерации")

        self.client.force_login(self.editor)
        approval = self.edit_payload(pending, document)
        approval["action"] = "approve"
        response = self.client.post(reverse("sdg4_edit", args=[pending.pk]), approval)
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(response.url, public_url)
        pending.refresh_from_db()
        self.assertEqual(pending.moderation_status, News.ModerationStatus.PUBLISHED)
        self.assertTrue(read_document(pending)["published"])
        self.assertContains(self.client.get(public_url), "Автор на модерации")

        # Changes made by the original contributor require a new approval.
        self.client.force_login(contributor)
        document = read_document(pending)
        revision = self.edit_payload(pending, document)
        revision["action"] = "submit"
        response = self.client.post(reverse("sdg4_edit", args=[pending.pk]), revision)
        self.assertIn("submitted=1", response.url)
        pending.refresh_from_db()
        self.assertEqual(pending.moderation_status, News.ModerationStatus.PENDING)
        self.assertFalse(read_document(pending)["published"])
        self.assertEqual(self.client.get(public_url).status_code, 404)

    def test_separate_moderation_page_grants_direct_publishing_and_approves(self):
        contributor = get_user_model().objects.create_user(
            "trusted-author",
            email="trusted@example.com",
        )
        self.client.force_login(contributor)
        response = self.client.post(
            reverse("sdg4_create"),
            {**payload(), "name_ru": "Первая заявка", "action": "publish"},
        )
        self.assertIn("submitted=1", response.url)
        pending = News.objects.get()
        self.assertEqual(pending.moderation_status, News.ModerationStatus.PENDING)
        self.assertEqual(self.client.get(reverse("sdg4_moderation")).status_code, 403)

        self.client.force_login(self.editor)
        moderation = self.client.get(reverse("sdg4_moderation"))
        self.assertContains(moderation, "Первая заявка")
        self.assertContains(moderation, "trusted@example.com")
        access = self.client.post(
            reverse("sdg4_moderation"),
            {"identifier": "trusted@example.com", "action": "grant"},
        )
        self.assertRedirects(
            access,
            reverse("sdg4_moderation") + "?access=granted",
            fetch_redirect_response=False,
        )
        self.assertTrue(views.can_publish(contributor))
        approval = self.client.post(reverse("sdg4_approve", args=[pending.pk]))
        self.assertRedirects(
            approval,
            reverse("sdg4_moderation") + "?approved=1",
            fetch_redirect_response=False,
        )
        pending.refresh_from_db()
        self.assertEqual(pending.moderation_status, News.ModerationStatus.PUBLISHED)
        self.assertTrue(read_document(pending)["published"])

        self.client.force_login(contributor)
        direct = self.client.post(
            reverse("sdg4_create"),
            {**payload(), "name_ru": "Доверенный автор", "action": "publish"},
        )
        directly_published = News.objects.exclude(pk=pending.pk).get()
        self.assertEqual(direct.status_code, 302)
        self.assertEqual(directly_published.moderation_status, News.ModerationStatus.PUBLISHED)
        self.assertTrue(read_document(directly_published)["published"])

        self.client.force_login(self.editor)
        self.client.post(
            reverse("sdg4_moderation"),
            {"identifier": "trusted-author", "action": "revoke"},
        )
        contributor.refresh_from_db()
        self.assertFalse(views.can_publish(contributor))

    def test_author_or_staff_can_delete_with_confirmation_and_files_are_removed(self):
        author = get_user_model().objects.create_user("article-owner")
        stranger = get_user_model().objects.create_user("not-the-owner")
        self.client.force_login(author)
        response = self.client.post(
            reverse("sdg4_create"),
            {
                **payload(False),
                "name_ru": "Удаляемая публикация",
                "cover_image": png("cover.png"),
                **article_payload(),
            },
        )
        row = News.objects.get()
        document = read_document(row)
        paths = views.owned_upload_paths(row, document)
        self.assertTrue(paths)
        self.assertTrue(all(default_storage.exists(path) for path in paths))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse("sdg4_delete", args=[row.pk])).status_code, 403)

        self.client.force_login(author)
        confirmation = self.client.get(reverse("sdg4_delete", args=[row.pk]))
        self.assertContains(confirmation, "Delete publication?")
        self.assertEqual(
            self.client.post(reverse("sdg4_delete", args=[row.pk]), {}).status_code,
            400,
        )
        with self.captureOnCommitCallbacks(execute=True):
            deleted = self.client.post(
                reverse("sdg4_delete", args=[row.pk]),
                {"confirm": "yes"},
            )
        self.assertRedirects(
            deleted,
            reverse("sdg4_manage") + "?deleted=1",
            fetch_redirect_response=False,
        )
        self.assertFalse(News.objects.filter(pk=row.pk).exists())
        self.assertTrue(all(not default_storage.exists(path) for path in paths))

        staff_row, _document, _url = self.publish(name_ru="Удаляет администратор")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse("sdg4_delete", args=[staff_row.pk])).status_code, 403)
        self.client.force_login(self.editor)
        self.assertEqual(
            self.client.get(reverse("sdg4_delete", args=[staff_row.pk]) + "?from=moderation").status_code,
            200,
        )
        self.client.post(
            reverse("sdg4_delete", args=[staff_row.pk]),
            {"confirm": "yes", "from": "moderation"},
        )
        self.assertFalse(News.objects.filter(pk=staff_row.pk).exists())

    def test_filters_are_narrow_and_mobile_safe(self):
        css = (Path(__file__).resolve().parents[4] / "static/sdg4/showcase.css").read_text()
        self.assertIn("max-width: 716px", css)
        self.assertIn("width: 100% !important", css)
        self.assertIn("width: min(100%, 520px)", css)

    def test_guests_cannot_write_or_view_editor_and_csrf_required(self):
        self.client.logout()
        for name in ("sdg4_create", "sdg4_manage"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn("next=", response.url)
        self.assertEqual(self.client.post(reverse("sdg4_create"), payload()).status_code, 302)
        self.assertContains(self.client.get(reverse("sdg4_index")), reverse("sdg4_create"))
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.editor)
        self.assertEqual(secured.post(reverse("sdg4_create"), payload()).status_code, 403)

    def test_editor_has_no_course_fields_and_translations_are_collapsed(self):
        response = self.client.get(reverse("sdg4_create"))
        self.assertContains(response, 'name="name_ru"')
        self.assertNotContains(response, 'name="resource_url"')
        self.assertNotContains(response, 'name="presentations"')
        self.assertNotContains(response, "External link or quick presentation upload")
        self.assertNotContains(response, "A permanent link is created automatically")
        self.assertContains(response, 'class="sdg4-translations"')
        self.assertNotContains(response, 'class="sdg4-translations" open')
        for value in (
            'name="course-',
            'name="materials-',
            'id="id_slug"',
            'name="programme"',
        ):
            self.assertNotContains(response, value)

    def test_translations_can_be_added_later_and_omitted_translations_are_kept(self):
        row, doc, url = self.publish()
        self.assertEqual(doc["profile"]["name"]["en"], "")
        data = self.edit_payload(row, doc)
        data["name_en"] = "English teacher"
        self.assertEqual(self.client.post(reverse("sdg4_edit", args=[row.pk]), data).url, url)
        self.assertContains(self.client.get(url), "English teacher")
        row.refresh_from_db()
        data = self.edit_payload(row, read_document(row))
        data["name_ru"] = "Новое имя"
        self.assertEqual(self.client.post(reverse("sdg4_edit", args=[row.pk]), data).url, url)
        row.refresh_from_db()
        self.assertEqual(read_document(row)["profile"]["name"]["en"], "English teacher")
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "kk"
        self.assertContains(self.client.get(url), "Новое имя")

    def test_edit_retains_files_link_ids_counters_and_fixed_url(self):
        row, doc, url = self.publish(presentations=[pdf()])
        data = self.edit_payload(row, doc)
        data.update(
            name_ru="Новое имя",
            faculty="law",
            slug="changed",
            resource_url="https://example.org/course",
            presentations=[pdf("Новые_слайды.pdf")],
        )
        increment(row.pk, doc["resources"][0]["id"], "views")
        self.assertEqual(self.client.post(reverse("sdg4_edit", args=[row.pk]), data).url, url)
        row.refresh_from_db()
        updated = read_document(row)
        self.assertEqual(updated["faculty"], doc["faculty"])
        self.assertEqual(updated["resources"][0]["id"], doc["resources"][0]["id"])
        self.assertEqual(updated["resources"][1], doc["resources"][1])
        self.assertEqual(updated["stats"][doc["resources"][0]["id"]]["views"], 1)
        self.assertEqual(len(updated["resources"]), 3)

    def test_stale_editor_rejected(self):
        row, doc, _url = self.publish()
        data = self.edit_payload(row, doc)
        self.assertEqual(self.client.post(reverse("sdg4_edit", args=[row.pk]), data).status_code, 302)
        self.assertContains(
            self.client.post(reverse("sdg4_edit", args=[row.pk]), data),
            "edited elsewhere",
        )

    def test_long_auto_generated_slug_does_not_block_later_edits(self):
        row, document, url = self.publish(name_ru="Преподаватель " + "я" * 100)
        self.assertGreater(len(document["slug"]), 70)
        response = self.client.post(reverse("sdg4_edit", args=[row.pk]), self.edit_payload(row, document))
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(response.url, url)

    def test_legacy_courses_remain_available_after_simple_edit(self):
        row, doc, url = self.legacy_page()
        data = self.edit_payload(row, doc)
        data.update(resource_url="https://example.org/course", presentations=[pdf("Новая.pdf")])
        self.assertEqual(self.client.post(reverse("sdg4_edit", args=[row.pk]), data).url, url)
        row.refresh_from_db()
        updated = read_document(row)
        self.assertEqual(updated["courses"], doc["courses"])
        self.assertEqual(updated["stats"], doc["stats"])
        self.assertEqual(updated["schema"], 3)
        self.assertContains(self.client.get(url), "Новая")
        self.client.logout()
        for number in range(1, 4):
            self.assertContains(self.client.get(url + f"?course={number}"), f"Topic {number}")
        item = doc["courses"][0]["materials"][0]
        response = self.client.get(reverse("sdg4_download", args=[doc["faculty"], doc["slug"], 1, item["id"]]))
        self.assertIn("attachment", response["Content-Disposition"])
        b"".join(response.streaming_content)
        self.assertEqual(self.client.get(url + "?course=4").status_code, 404)
        self.assertEqual(self.client.get(url + "?course=bad").status_code, 404)

    def test_legacy_video_embed_and_subtitle_validation_remain_supported(self):
        _row, _doc, url = self.legacy_page(video=True)
        response = self.client.get(url + "?course=2")
        self.assertContains(
            response,
            "https://www.youtube-nocookie.com/embed/abcdefghijk?cc_load_policy=1",
        )
        self.assertContains(response, "2 min 5 sec")
        form = MaterialForm({"file_url": "https://open.kaznu.kz/video.mp4"}, course_type="video")
        self.assertFalse(form.is_valid())
        self.assertIn("duration", form.errors)

    def test_home_lists_real_new_and_legacy_materials_and_all_faculties(self):
        self.publish(presentations=[pdf()])
        self.legacy_page()
        News.objects.create(title="Ordinary news", content="Not a resource")
        response = self.client.get(reverse("sdg4_index"))
        self.assertContains(response, "data-resource-card", count=2)
        self.assertContains(response, "Иванова Алия Сергеевна")
        self.assertContains(response, "Teacher")
        self.assertNotContains(response, "Ordinary news")
        for slug, _name in FACULTIES:
            self.assertContains(response, reverse("sdg4_faculty", args=[slug]))
            self.assertEqual(self.client.get(reverse("sdg4_faculty", args=[slug])).status_code, 200)
        self.assertEqual(self.client.get("/sdg4/unknown/").status_code, 404)

    def test_empty_landing_does_not_show_fake_courses(self):
        response = self.client.get(reverse("sdg4_index"))
        self.assertContains(response, "Materials will appear here")
        self.assertNotContains(response, "data-resource-card")

    def test_unknown_resource_and_legacy_course_on_new_page_return_404(self):
        _row, doc, _url = self.publish()
        self.assertEqual(
            self.client.get(reverse("sdg4_resource", args=[doc["faculty"], doc["slug"], "missing"])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("sdg4_material", args=[doc["faculty"], doc["slug"], 1, "missing"])).status_code,
            404,
        )

    def test_draft_resource_route_is_not_public(self):
        row, doc, _url = self.publish()
        data = self.edit_payload(row, doc)
        data["action"] = "draft"
        self.client.post(reverse("sdg4_edit", args=[row.pk]), data)
        self.assertEqual(
            self.client.get(
                reverse(
                    "sdg4_resource",
                    args=[doc["faculty"], doc["slug"], doc["resources"][0]["id"]],
                )
            ).status_code,
            404,
        )

    def test_safe_external_and_existing_site_links(self):
        for url in ("https://example.org/course", "/asset-v1:kaznu/file.pdf"):
            self.assertEqual(validate_media_url(url), url)
        for url in (
            "javascript:alert(1)",
            "//evil.example/",
            "/\\evil.example/",
            "https://user:pass@example.com/",
            "/file\n.pdf",
            "https://[invalid/file.pdf",
        ):
            with self.assertRaises(ValidationError):
                validate_media_url(url)
        self.assertEqual(
            self.client.post(
                reverse("sdg4_create"),
                {**payload(), "resource_url": "javascript:alert(1)"},
            ).status_code,
            200,
        )
        self.assertEqual(youtube_embed("https://evil.example/embed/abcdefghijk"), "")
        self.assertEqual(youtube_embed("https://youtube.com/watch?v=bad"), "")

    def test_untrusted_teacher_and_link_title_escaped(self):
        _row, _doc, url = self.publish(
            name_ru='<script>alert("x")</script>',
            resource_title="<img src=x onerror=alert(1)>",
        )
        for route in (url, reverse("sdg4_index")):
            response = self.client.get(route)
            self.assertNotContains(response, '<script>alert("x")</script>')
            self.assertNotContains(response, "<img src=x onerror=alert(1)>")
            self.assertContains(response, "&lt;script&gt;")

    def test_language_switch_saves_preference_and_rejects_external_redirect(self):
        with patch.object(views, "save_language_preference") as save:
            response = self.client.post(
                reverse("sdg4_language"),
                {"language": "ru", "next": "https://evil.example/"},
            )
        save.assert_called_once_with(self.editor, "ru")
        self.assertEqual(response.url, "/")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ru")
        self.assertEqual(
            self.client.post(reverse("sdg4_language"), {"language": "fr"}).status_code,
            400,
        )

    def test_alphabetical_order_and_language_fallback(self):
        with translation.override("kk"):
            self.assertEqual(
                sorted(["Болат", "Әлия", "Абай"], key=alphabetical_key),
                ["Абай", "Әлия", "Болат"],
            )
        with translation.override("ru"):
            self.assertEqual(
                sorted(["Жуков", "Ёлкин", "Егоров"], key=alphabetical_key),
                ["Егоров", "Ёлкин", "Жуков"],
            )
        self.assertEqual(language_code("kz-KZ"), "kk")
        self.assertEqual(localized({"ru": "fallback"}, "kk"), "fallback")

    def test_mako_header_hook_accepts_django_pageargs(self):
        html = self.client.get(reverse("sdg4_index")).content.decode()
        head, body = html.split("</head>", 1)
        self.assertIn("sdg4/showcase.css", head)
        self.assertEqual(body.count('rel="stylesheet"'), 0)
        self.assertEqual(body.split("<main", 1)[0].strip(), "<body>")
        self.assertIn("sdg4-showcase.js", body)

    def test_animations_are_progressive_and_respect_reduced_motion(self):
        root = Path(__file__).resolve().parents[4] / "static"
        css = (root / "sdg4/showcase.css").read_text()
        script = (root / "js/sdg4-showcase.js").read_text()
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("prefers-reduced-motion: reduce", script)
        self.assertIn("IntersectionObserver", script)
        self.assertIn(".sdg4 [hidden]", css)

    def test_legacy_formset_cannot_allocate_unbounded_forms(self):
        formset = Materials({"m-TOTAL_FORMS": "10000", "m-INITIAL_FORMS": "0"}, prefix="m")
        self.assertFalse(formset.is_valid())
        self.assertEqual(len(formset.forms), 200)
