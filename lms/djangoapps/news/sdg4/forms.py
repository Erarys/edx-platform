"""Russian-first forms with optional translations and incremental drafts."""

from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.forms import BaseFormSet, formset_factory
from django.utils.translation import gettext_lazy as _

from .constants import COURSE_TYPES, FACULTIES, LANGUAGES
from .data import validate_media_url, youtube_embed

ARTICLE_EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".gif"},
    "presentation": {".pdf"},
    "audio": {".mp3", ".m4a", ".ogg", ".wav"},
    "video": {".mp4", ".webm", ".ogv"},
}
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "www.youtube-nocookie.com",
}

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


def validate_presentation_upload(upload):
    MaterialForm.validate_upload(upload, ARTICLE_EXTENSIONS["presentation"])


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


class CourseForm(forms.Form):
    DELETE = forms.BooleanField(required=False, widget=forms.HiddenInput)
    type = forms.ChoiceField(label=_("Course type"), choices=COURSE_TYPES)
    language = forms.ChoiceField(label=_("Language of materials"), choices=LANGUAGES)
    programme = forms.CharField(label=_("Educational programme code"), max_length=200)
    lecture_files = MultipleFileField(
        label=_("Upload presentations"),
        required=False,
        help_text=_("Select PDF files. Lesson titles will use the file names."),
        widget=MultipleFileInput(attrs={"accept": ".pdf"}),
    )

    def __init__(self, *args, course=None, publishing=True, **kwargs):
        course = course or {}
        self.course = course
        kwargs["initial"] = {
            "type": "lecture",
            "language": "ru",
            **course,
        }
        super().__init__(*args, **kwargs)
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
        deleted = bool(self.is_bound and self.data.get(self.add_prefix("DELETE")))
        self.fields["programme"].required = publishing and not deleted
        if not publishing or deleted:
            for name, field in self.fields.items():
                if name != "DELETE":
                    field.required = False

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

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        upload, url = cleaned.get("file"), cleaned.get("file_url", "")
        self._validate_new_upload(upload)
        if upload and url:
            # Selecting a new local file is an explicit replacement action.  In
            # particular, this lets an author replace a previously saved URL
            # without having to notice and clear the URL field first.
            url = ""
            cleaned["file_url"] = ""
        if self.publishing and not (upload or url or self.material.get("file_path")):
            raise ValidationError(_("Add a file or a link for every lesson."))
        if not cleaned.get("title"):
            cleaned["title"] = material_title(upload.name if upload else url or self.material.get("file_path", ""))
        self._validate_existing_file(upload, url)
        self._validate_url(url)
        return cleaned

    def _validate_new_upload(self, upload):
        if not upload:
            return
        if self.course_type == "lecture":
            validate_presentation_upload(upload)
        else:
            self.validate_upload(upload, ARTICLE_EXTENSIONS["video"])

    def _validate_existing_file(self, upload, url):
        existing_path = self.material.get("file_path", "") if not (upload or url) else ""
        if existing_path:
            # Keep existing legacy PowerPoint materials editable. New presentation
            # uploads and links are restricted to PDF below.
            expected = (
                {".pdf", ".ppt", ".pptx"}
                if self.course_type == "lecture"
                else ARTICLE_EXTENSIONS["video"]
            )
            if PurePosixPath(existing_path.lower()).suffix not in expected:
                raise ValidationError(_("The file type does not match this material."))

    def _validate_url(self, url):
        source = url or self.material.get("file_url", "")
        host = (urlsplit(source).hostname or "").lower()
        if self.course_type == "lecture":
            if url and PurePosixPath(urlsplit(url).path.lower()).suffix not in ARTICLE_EXTENSIONS["presentation"]:
                raise ValidationError(_("Presentation materials must be PDF files."))
            return
        if host in YOUTUBE_HOSTS:
            if not youtube_embed(source):
                raise ValidationError(_("Use a valid university YouTube video."))
            return
        self._validate_university_video(source, host)

    @staticmethod
    def _validate_university_video(source, host):
        # University media can also use a separately configured storage/CDN host.
        allowed = set(getattr(settings, "SDG4_MEDIA_HOSTS", ()))
        allowed.update(
            filter(
                None,
                (urlsplit(getattr(settings, key, "")).hostname for key in ("MEDIA_URL", "LMS_ROOT_URL")),
            )
        )
        is_university_host = (
            host in {"kaznu.kz", "farabi.university"}
            or host.endswith((".kaznu.kz", ".farabi.university"))
            or host in allowed
        )
        if host and not is_university_host:
            raise ValidationError(_("Use university-hosted video or the university YouTube channel."))
        if source and PurePosixPath(urlsplit(source).path.lower()).suffix not in ARTICLE_EXTENSIONS["video"]:
            raise ValidationError(_("Use an MP4, WebM or OGV video URL."))


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
            raise ValidationError(_("Add at least one lesson to this course."))


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
