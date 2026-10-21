"""Stable identifiers and translatable labels for the SDG 4 showcase."""

from django.utils.translation import gettext_lazy as _

NEWS_PREFIX = "__sdg4__:"
PUBLISHERS_GROUP = "sdg4_publishers"
LANGUAGES = (("kk", "KZ"), ("ru", "RU"), ("en", "EN"))
FACULTIES = (
    ("information-technology-ai", _("Faculty of Information Technology and Artificial Intelligence")),
    ("biology-biotechnology", _("Faculty of Biology and Biotechnology")),
    ("geography", _("Faculty of Geography and Environmental Sciences")),
    ("journalism", _("Faculty of Journalism")),
    ("law", _("Faculty of Law")),
    ("medicine", _("Faculty of Medicine and Health Care")),
    ("mechanics-mathematics", _("Faculty of Mechanics and Mathematics")),
    ("history", _("Faculty of History")),
    ("pre-higher-education", _("Faculty of Pre-Higher Education")),
    ("physics-technology", _("Faculty of Physics and Technology")),
    ("philology", _("Faculty of Philology")),
    ("philosophy-political-science", _("Faculty of Philosophy and Political Science")),
    ("international-relations", _("Faculty of International Relations")),
    ("chemistry", _("Faculty of Chemistry and Chemical Technology")),
    ("oriental-studies", _("Faculty of Oriental Studies")),
    ("economics-business", _("Higher School of Economics and Business")),
    ("al-farabi-business-school", _("Al-Farabi Business School")),
)
COURSE_TYPES = (("lecture", _("Lecture course")), ("video", _("Video course")))
LICENSES = tuple(
    (key, label)
    for key, label in (
        ("by", "CC BY 4.0"),
        ("by-sa", "CC BY-SA 4.0"),
        ("by-nd", "CC BY-ND 4.0"),
        ("by-nc", "CC BY-NC 4.0"),
        ("by-nc-sa", "CC BY-NC-SA 4.0"),
        ("by-nc-nd", "CC BY-NC-ND 4.0"),
    )
)
PREAMBLE = _(
    "This section is part of NJSC Al-Farabi KazNU's contribution to UN Sustainable Development Goal 4, "
    "Quality Education. The University offers open access to its faculty's e-learning resources — lecture "
    "courses and video courses in Kazakh, Russian and English. Students, schoolchildren, professionals "
    "and lecturers from other universities can learn at their own pace, from anywhere in the world. "
    "In this way the University contributes to the following SDG 4 targets:"
)
TARGET_43 = _(
    "4.3. By 2030, ensure equal access for all women and men to affordable and quality technical, "
    "vocational and tertiary education, including university."
)
TARGET_44 = _(
    "4.4. By 2030, substantially increase the number of youth and adults who have relevant skills, "
    "including technical and vocational skills, for employment, decent jobs and entrepreneurship."
)
