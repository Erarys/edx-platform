"""Russian-first forms with optional translations and incremental drafts."""

from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.forms import BaseFormSet, formset_factory
from django.utils.translation import gettext_lazy as _

from .constants import COURSE_TYPES, FACULTIES, LANGUAGES, LICENSES
from .data import validate_media_url, youtube_embed

ARTICLE_BLOCK_TYPES = (
    ("text", _("Text")),
    ("image", _("Image")),
    ("presentation", _("Presentation")),
    ("audio", _("Audio")),
    ("video", _("Video")),
)

ARTICLE_EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".gif"},
    "presentation": {".pdf", ".ppt", ".pptx"},
    "audio": {".mp3", ".m4a", ".ogg", ".wav"},
    "video": {".mp4", ".webm", ".ogv"},
}
MAX_ARTICLE_BLOCKS = max(1, int(getattr(settings, "SDG4_MAX_ARTICLE_BLOCKS", 1000)))

def translated_fields(form, names, initial, publishing=True):
    for name, label, limit, multiline in names:
        for code, short in LANGUAGES:
            key = f"{name}_{code}"
            form.fields[key] = forms.CharField(
                label=f"{label} ({short})",
                max_length=limit,
                widget=forms.Textarea(attrs={"rows": 3}) if multiline else forms.TextInput(),
                initial=initial.get(name, {}).get(code, ""),
                required=code == "ru" and (publishing or name == "name"),
            )
            # A Russian-only update must not erase translations absent from POST.
            if form.is_bound and code != "ru" and form.add_prefix(key) not in form.data:
                form.data = form.data.copy()
                form.data[form.add_prefix(key)] = initial.get(name, {}).get(code, "")


def translations(cleaned_data, name):
    return {code: cleaned_data[f"{name}_{code}"] for code, _label in LANGUAGES}


class TeacherForm(forms.Form):
    faculty = forms.ChoiceField(label=_("Faculty"), choices=(("", _("Select your faculty")), *FACULTIES))
    slug = forms.SlugField(
        label=_("Permanent address"),
        max_length=70,
        required=False,
        widget=forms.HiddenInput(),
        help_text=_("Use a surname and initials in Latin letters. The address is fixed after the first save."),
    )
    photo = forms.ImageField(label=_("Photo (optional)"), required=False)
    mooc_url = forms.CharField(
        label=_("MOOC link (optional)"),
        max_length=1000,
        required=False,
        validators=[validate_media_url],
    )
    published = forms.BooleanField(label=_("Publish this page"), required=False, widget=forms.HiddenInput())

    def __init__(self, *args, document=None, publishing=True, **kwargs):
        self.document = document or {}
        profile = self.document.get("profile", {})
        kwargs["initial"] = {
            "faculty": self.document.get("faculty", ""),
            "slug": self.document.get("slug", ""),
            "mooc_url": profile.get("mooc_url", ""),
            "published": self.document.get("published", False),
        }
        super().__init__(*args, **kwargs)
        if document:
            # Stable even when an administrator changes the teacher's name or department.
            self.fields["faculty"].disabled = True
            self.fields["slug"].disabled = True
        translated_fields(
            self,
            (
                ("name", _("Full name"), 200, False),
                ("position", _("Position"), 200, False),
                ("degree", _("Academic degree / title"), 200, False),
                ("department", _("Department"), 250, False),
            ),
            profile,
            publishing=publishing,
        )

    def clean_photo(self):
        photo = self.cleaned_data["photo"]
        if photo and (photo.size > 10 * 1024 * 1024 or photo.image.format not in {"JPEG", "PNG", "WEBP"}):
            raise ValidationError(_("Upload a JPEG, PNG or WebP image up to 10 MB."))
        return photo


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        if not data:
            return []
        clean_single = super().clean
        return [clean_single(upload, initial) for upload in (data if isinstance(data, (list, tuple)) else [data])]


class SimpleTeacherForm(TeacherForm):
    """Only a teacher, faculty and a link or presentation are needed to publish."""

    resource_url = forms.CharField(
        label=_("Course or material link"),
        max_length=2000,
        required=False,
        validators=[validate_media_url],
    )
    resource_title = forms.CharField(label=_("Link title (optional)"), max_length=250, required=False)
    cover_image = forms.ImageField(
        label=_("Article cover (optional)"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp"}),
    )
    presentations = MultipleFileField(
        label=_("Upload presentations"),
        required=False,
        widget=MultipleFileInput(attrs={"accept": ".pdf,.ppt,.pptx", "aria-describedby": "sdg4-upload-help"}),
    )

    def __init__(self, *args, document=None, publishing=True, **kwargs):
        self.publishing = publishing
        super().__init__(*args, document=document, publishing=False, **kwargs)
        self.fields.pop("mooc_url")
        self.fields.pop("slug")
        # The link is a single editable field, not a course/lesson formset.
        resources = self.document.get("resources", [])
        link = next((item for item in resources if item.get("kind") == "link"), {})
        self.initial.update(resource_url=link.get("file_url", ""), resource_title=link.get("title", ""))
        translated_fields(
            self,
            (("article_title", _("Article title (optional)"), 250, False),),
            self.document,
            publishing=False,
        )

    def clean_presentations(self):
        uploads = self.cleaned_data["presentations"]
        existing = sum(item.get("kind") != "link" for item in self.document.get("resources", []))
        if len(uploads) + existing > 200:
            raise ValidationError(_("You can upload up to 200 presentations per teacher."))
        for upload in uploads:
            validate_presentation_upload(upload)
        return uploads

    def clean_cover_image(self):
        cover = self.cleaned_data["cover_image"]
        if cover and (cover.size > 20 * 1024 * 1024 or cover.image.format not in {"JPEG", "PNG", "WEBP"}):
            raise ValidationError(_("Upload a JPEG, PNG or WebP cover up to 20 MB."))
        return cover

    def clean(self):
        cleaned = super().clean()
        return cleaned


def validate_presentation_upload(upload):
    MaterialForm.validate_upload(upload, ARTICLE_EXTENSIONS["presentation"])
    extension = PurePosixPath(upload.name.lower()).suffix
    try:
        if extension == ".ppt":
            if upload.read(8) != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                raise ValidationError(_("Upload a valid PowerPoint presentation."))
        elif extension == ".pptx":
            with ZipFile(upload) as archive:
                names = set(archive.namelist())
                if not {"[Content_Types].xml", "ppt/presentation.xml"} <= names or any(
                    "vbaproject" in name.lower() for name in names
                ):
                    raise ValidationError(_("Upload a valid PowerPoint presentation."))
    except (BadZipFile, OSError):
        raise ValidationError(_("Upload a valid PowerPoint presentation.")) from None
    finally:
        upload.seek(0)


def validate_article_upload(upload, kind):
    """Validate enough of each browser-served format to reject renamed files."""
    extensions = ARTICLE_EXTENSIONS[kind]
    MaterialForm.validate_upload(upload, extensions)
    extension = PurePosixPath(upload.name.lower()).suffix
    if kind == "presentation":
        validate_presentation_upload(upload)
        return
    if kind == "image":
        try:
            forms.ImageField().clean(upload)
        except ValidationError:
            raise ValidationError(_("Upload a valid image file.")) from None
        finally:
            upload.seek(0)
        return
    header = upload.read(64)
    upload.seek(0)
    valid = {
        ".mp3": header.startswith(b"ID3") or (len(header) > 1 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0),
        ".m4a": len(header) >= 12 and header[4:8] == b"ftyp",
        ".ogg": header.startswith(b"OggS"),
        ".wav": header.startswith(b"RIFF") and header[8:12] == b"WAVE",
        ".mp4": len(header) >= 12 and header[4:8] == b"ftyp",
        ".webm": header.startswith(b"\x1aE\xdf\xa3"),
        ".ogv": header.startswith(b"OggS"),
    }.get(extension, False)
    if not valid:
        raise ValidationError(_("The uploaded media file is not valid."))


class ArticleBlockForm(forms.Form):
    """One ordered article block; existing file paths always come from the database."""

    block_id = forms.CharField(required=False, widget=forms.HiddenInput())
    kind = forms.ChoiceField(label=_("Block type"), choices=ARTICLE_BLOCK_TYPES)
    text = forms.CharField(
        label=_("Text"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": _("Write the article text here…")}),
    )
    title = forms.CharField(label=_("File title (optional)"), max_length=250, required=False)
    caption = forms.CharField(label=_("Caption (optional)"), max_length=500, required=False)
    file = forms.FileField(label=_("File"), required=False)

    def __init__(self, *args, block=None, **kwargs):
        self.block = block or {}
        if self.block:
            kwargs["initial"] = {
                "block_id": self.block.get("id", ""),
                "kind": self.block.get("kind", "text"),
                "text": self.block.get("text", ""),
                "title": self.block.get("title", ""),
                "caption": self.block.get("caption", ""),
                **kwargs.get("initial", {}),
            }
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        block_id = cleaned.get("block_id", "")
        expected_id = self.block.get("id", "")
        if block_id != expected_id:
            raise ValidationError(_("The article block is no longer current. Reload the page."))
        kind = cleaned.get("kind")
        upload = cleaned.get("file")
        if kind == "text":
            if not cleaned.get("text", "").strip():
                self.add_error("text", _("Enter text for this block."))
            if upload:
                self.add_error("file", _("A text block cannot contain a file."))
            return cleaned
        existing_path = self.block.get("file_path", "")
        if not upload and not existing_path:
            self.add_error("file", _("Choose a file for this block."))
            return cleaned
        if not upload:
            extension = PurePosixPath(existing_path.lower()).suffix
            if kind != self.block.get("kind") or extension not in ARTICLE_EXTENSIONS[kind]:
                self.add_error("file", _("Choose a file that matches the selected block type."))
            return cleaned
        try:
            validate_article_upload(upload, kind)
        except ValidationError as exc:
            self.add_error("file", exc)
            return cleaned
        if not cleaned.get("title"):
            cleaned["title"] = material_title(upload.name)
        return cleaned


class ArticleBlockFormSet(BaseFormSet):
    ordering_widget = forms.HiddenInput

    def __init__(self, *args, blocks=None, **kwargs):
        self.blocks = blocks or []
        kwargs["initial"] = self.blocks
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        if index is not None and index < len(self.blocks):
            kwargs["block"] = self.blocks[index]
        return kwargs


ArticleBlocks = formset_factory(
    ArticleBlockForm,
    formset=ArticleBlockFormSet,
    extra=0,
    can_delete=True,
    can_order=True,
    max_num=MAX_ARTICLE_BLOCKS,
    validate_max=True,
    absolute_max=MAX_ARTICLE_BLOCKS,
)


class CourseForm(forms.Form):
    type = forms.ChoiceField(label=_("Course type"), choices=COURSE_TYPES)
    language = forms.ChoiceField(label=_("Language of materials"), choices=LANGUAGES)
    programme = forms.CharField(label=_("Educational programme code"), max_length=200)
    license = forms.ChoiceField(label=_("Creative Commons license"), choices=LICENSES)
    lecture_files = MultipleFileField(
        label=_("Upload presentations"),
        required=False,
        help_text=_("Select PDF, PPT or PPTX files. Lesson titles will use the file names."),
        widget=MultipleFileInput(attrs={"accept": ".pdf,.ppt,.pptx"}),
    )

    def __init__(self, *args, course=None, publishing=True, **kwargs):
        course = course or {}
        kwargs["initial"] = {
            "type": "lecture",
            "language": "ru",
            "license": "by",
            **course,
        }
        super().__init__(*args, **kwargs)
        self.fields["programme"].required = publishing
        if not publishing:
            for name in ("type", "language", "license"):
                self.fields[name].required = False
        translated_fields(
            self,
            (
                ("topic", _("Topic"), 250, False),
                ("discipline", _("Discipline"), 250, False),
                ("annotation", _("Annotation (up to 500 characters)"), 500, True),
            ),
            course,
            publishing=publishing,
        )

    def clean_lecture_files(self):
        files = self.cleaned_data["lecture_files"]
        if files and self.cleaned_data.get("type") != "lecture":
            raise ValidationError(_("The file type does not match this material."))
        for upload in files:
            validate_presentation_upload(upload)
        return files


def material_title(filename):
    return PurePosixPath(unquote(urlsplit(filename).path)).stem.replace("_", " ").strip()[:250] or str(_("Lesson"))


class MaterialForm(forms.Form):
    title = forms.CharField(label=_("Lesson title"), max_length=250, required=False)
    file = forms.FileField(label=_("Presentation or video file"), required=False)
    file_url = forms.CharField(
        label=_("Existing file URL or YouTube link"),
        max_length=2000,
        required=False,
        validators=[validate_media_url],
    )
    captions = forms.FileField(label=_("Subtitles (.vtt)"), required=False)
    captions_url = forms.CharField(
        label=_("Existing subtitles URL"),
        max_length=2000,
        required=False,
        validators=[validate_media_url],
    )
    youtube_captions = forms.BooleanField(
        label=_("I verified that this university YouTube video has subtitles."),
        required=False,
    )
    duration = forms.IntegerField(label=_("Video duration in seconds"), required=False, min_value=1)

    def __init__(self, *args, material=None, course_type="lecture", publishing=True, **kwargs):
        self.material = material or {}
        self.course_type = course_type
        self.publishing = publishing
        super().__init__(*args, **kwargs)
        if material:
            # Initial values must also be set on bound forms, for empty-form detection.
            self.initial.update({key: value for key, value in material.items() if key in self.fields})

    @staticmethod
    def validate_upload(upload, extensions):
        if not upload:
            return
        extension = PurePosixPath(upload.name.lower()).suffix
        if extension not in extensions:
            raise ValidationError(_("The file type does not match this material."))
        if upload.size > getattr(settings, "SDG4_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024):
            raise ValidationError(_("The file exceeds the 1 GB upload limit."))
        header = upload.read(1024)
        upload.seek(0)
        if extension == ".pdf" and not header.startswith(b"%PDF-"):
            raise ValidationError(_("Upload a valid PDF file."))
        if extension == ".vtt" and not header.lstrip(b"\xef\xbb\xbf").startswith(b"WEBVTT"):
            raise ValidationError(_("Upload WebVTT subtitles starting with WEBVTT."))

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        upload, url = cleaned.get("file"), cleaned.get("file_url", "")
        if self.course_type == "lecture" and upload:
            validate_presentation_upload(upload)
        else:
            self.validate_upload(upload, {".mp4", ".webm", ".ogv"})
        self.validate_upload(cleaned.get("captions"), {".vtt"})
        if upload and url:
            raise ValidationError(_("Choose either an uploaded file or an existing URL."))
        if self.publishing and not (upload or url or self.material.get("file_path")):
            raise ValidationError(_("Add a file or a link for every lesson."))
        if not cleaned.get("title"):
            cleaned["title"] = material_title(upload.name if upload else url or self.material.get("file_path", ""))
        if cleaned.get("captions") and cleaned.get("captions_url"):
            raise ValidationError(_("Choose either uploaded subtitles or a subtitles URL."))
        source = url or self.material.get("file_url", "")
        existing_path = self.material.get("file_path", "") if not (upload or url) else ""
        if existing_path:
            expected = ARTICLE_EXTENSIONS["presentation"] if self.course_type == "lecture" else {".mp4", ".webm", ".ogv"}
            if PurePosixPath(existing_path.lower()).suffix not in expected:
                raise ValidationError(_("The file type does not match this material."))
        host = (urlsplit(source).hostname or "").lower()
        youtube_host = host in {
            "youtube.com",
            "www.youtube.com",
            "youtu.be",
            "www.youtube-nocookie.com",
        }
        if self.course_type == "lecture":
            if source and PurePosixPath(urlsplit(source).path.lower()).suffix not in ARTICLE_EXTENSIONS["presentation"]:
                raise ValidationError(_("Presentation materials must be PDF, PPT or PPTX files."))
        else:
            if self.publishing and not cleaned.get("duration"):
                self.add_error("duration", _("Enter the video duration."))
            if youtube_host:
                if not youtube_embed(source) or (self.publishing and not cleaned.get("youtube_captions")):
                    raise ValidationError(_("Use a valid university YouTube video and confirm its subtitles."))
            else:
                # University media can also use a separately configured storage/CDN host.
                allowed = set(getattr(settings, "SDG4_MEDIA_HOSTS", ()))
                allowed.update(
                    filter(
                        None,
                        (urlsplit(getattr(settings, key, "")).hostname for key in ("MEDIA_URL", "LMS_ROOT_URL")),
                    )
                )
                if host and not (
                    host in {"kaznu.kz", "farabi.university"}
                    or host.endswith((".kaznu.kz", ".farabi.university"))
                    or host in allowed
                ):
                    raise ValidationError(_("Use university-hosted video or the university YouTube channel."))
                if source and PurePosixPath(urlsplit(source).path.lower()).suffix not in {".mp4", ".webm", ".ogv"}:
                    raise ValidationError(_("Use an MP4, WebM or OGV video URL."))
                if self.publishing and not (
                    cleaned.get("captions") or cleaned.get("captions_url") or self.material.get("captions_path")
                ):
                    raise ValidationError(_("Subtitles are required for each video."))
        return cleaned


class MaterialFormSet(BaseFormSet):
    """Bind existing file references by their server-side row index, never hidden paths."""

    def __init__(
        self,
        *args,
        materials=None,
        course_type="lecture",
        publishing=True,
        bulk_count=0,
        **kwargs,
    ):
        self.materials = materials or []
        self.course_type = course_type
        self.publishing = publishing
        self.bulk_count = bulk_count
        self.min_num = 1 if publishing and not bulk_count else 0
        kwargs["initial"] = self.materials
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs.update(course_type=self.course_type, publishing=self.publishing)
        if index is not None and index < len(self.materials):
            kwargs["material"] = self.materials[index]
        return kwargs

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        count = (
            sum(bool(form.cleaned_data) and not form.cleaned_data.get("DELETE") for form in self.forms)
            + self.bulk_count
        )
        if count > self.max_num:
            raise ValidationError(_("A course can contain at most 200 lessons."))
        if self.publishing and not count:
            raise ValidationError(_("Add at least one lesson to each of the three courses."))


Materials = formset_factory(
    MaterialForm,
    formset=MaterialFormSet,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
    max_num=200,
    validate_max=True,
    absolute_max=200,
)
