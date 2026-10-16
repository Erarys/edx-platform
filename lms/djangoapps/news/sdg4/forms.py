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

    def clean_presentations(self):
        uploads = self.cleaned_data["presentations"]
        existing = sum(item.get("kind") != "link" for item in self.document.get("resources", []))
        if len(uploads) + existing > 200:
            raise ValidationError(_("You can upload up to 200 presentations per teacher."))
        for upload in uploads:
            MaterialForm.validate_upload(upload, {".pdf", ".ppt", ".pptx"})
            extension = PurePosixPath(upload.name.lower()).suffix
            try:
                if extension == ".ppt":
                    if upload.read(8) != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                        raise ValidationError(_("Upload a valid PowerPoint presentation."))
                elif extension == ".pptx":
                    # Inspect the directory only; never extract or execute an Office file.
                    with ZipFile(upload) as archive:
                        names = set(archive.namelist())
                        if not {
                            "[Content_Types].xml",
                            "ppt/presentation.xml",
                        } <= names or any("vbaproject" in name.lower() for name in names):
                            raise ValidationError(_("Upload a valid PowerPoint presentation."))
            except (BadZipFile, OSError):
                raise ValidationError(_("Upload a valid PowerPoint presentation.")) from None
            finally:
                upload.seek(0)
        return uploads

    def clean(self):
        cleaned = super().clean()
        legacy = self.document.get("courses", [])
        existing = any(item.get("kind") != "link" for item in self.document.get("resources", []))
        has_legacy = self.document.get("profile", {}).get("mooc_url") or any(
            course.get("materials") for course in legacy
        )
        if (
            self.publishing
            and not self.errors
            and not (cleaned.get("resource_url") or cleaned.get("presentations") or existing or has_legacy)
        ):
            raise ValidationError(_("Add a link or upload a presentation to publish. You can also save a draft."))
        return cleaned


class CourseForm(forms.Form):
    type = forms.ChoiceField(label=_("Course type"), choices=COURSE_TYPES)
    language = forms.ChoiceField(label=_("Language of materials"), choices=LANGUAGES)
    programme = forms.CharField(label=_("Educational programme code"), max_length=200)
    license = forms.ChoiceField(label=_("Creative Commons license"), choices=LICENSES)
    lecture_files = MultipleFileField(
        label=_("Upload PDF files"),
        required=False,
        help_text=_("Select several files at once. Lesson titles will use the file names."),
        widget=MultipleFileInput(attrs={"accept": ".pdf"}),
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
            MaterialForm.validate_upload(upload, {".pdf"})
        return files


def material_title(filename):
    return PurePosixPath(unquote(urlsplit(filename).path)).stem.replace("_", " ").strip()[:250] or str(_("Lesson"))


class MaterialForm(forms.Form):
    title = forms.CharField(label=_("Lesson title"), max_length=250, required=False)
    file = forms.FileField(label=_("PDF or video file"), required=False)
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
        if upload.size > getattr(settings, "SDG4_MAX_UPLOAD_BYTES", 512 * 1024 * 1024):
            raise ValidationError(_("The file exceeds the configured upload limit."))
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
        self.validate_upload(
            upload,
            {".pdf"} if self.course_type == "lecture" else {".mp4", ".webm", ".ogv"},
        )
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
            expected = {".pdf"} if self.course_type == "lecture" else {".mp4", ".webm", ".ogv"}
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
            if source and PurePosixPath(urlsplit(source).path.lower()).suffix != ".pdf":
                raise ValidationError(_("Lecture materials must be PDF files."))
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
                    host == "kaznu.kz"
                    or host.endswith(".kaznu.kz")
                    or host == "farabi.university"
                    or host.endswith(".farabi.university")
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
