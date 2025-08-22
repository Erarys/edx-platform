# coding=UTF-8
from django.db import models

COURSE_TYPE_CHOICES = [
    (u'MOOC', u'MOOC'),
    (u'SPOC', u'SPOC'),
    (u'none', u'None')
]

DIFFICULTY_LEVEL_CHOICES = [
    (u'базовый', u'базовый'),
    (u'средний', u'средний'),
    (u'продвинутый', u'продвинутый'),
    (u'none', u'None')
]
class retakecatalog(models.Model):
    course_name = models.CharField(max_length=255, verbose_name=u"Course Name", default=u"Not Specified")
    university = models.CharField(max_length=255, null=True, blank=True, verbose_name=u"University")
    content_type = models.CharField(max_length=255, null=True, blank=True, verbose_name=u"Content Type")
    course_type = models.CharField(
        max_length=50,
        choices=COURSE_TYPE_CHOICES,
        verbose_name=u"Course Type (MOOC/SPOC)", default=u"MOOC"
    )
    difficulty_level = models.CharField(
        max_length=50,
        choices=DIFFICULTY_LEVEL_CHOICES,
        verbose_name=u"Difficulty Level", default=u"basic"
    )
    platform = models.CharField(max_length=255, verbose_name=u"Platform", default=u"Not Specified")
    course_link = models.URLField(verbose_name=u"Course Link")
    organization = models.CharField(max_length=255, verbose_name=u"Organization / University Offering the Course", default=u"Not Specified")

    # Workload
    workload_hours = models.FloatField(verbose_name=u"Course Workload in Hours", default=0.0)
    workload_credits = models.FloatField(verbose_name=u"Course Workload in Credits", default=0.0)

    # Education and Field
    education_field = models.CharField(max_length=255, verbose_name=u"Field of Education", default=u"Not Specified")
    training_direction = models.CharField(max_length=255, verbose_name=u"Training Direction", default=u"Not Specified")
    op_code_name = models.TextField(verbose_name=u"Code and Name of the Program", default=u"Not Specified")

    # Additional Course Information
    course_description = models.TextField(verbose_name=u"Course Description", default=u"Description not provided")
    skills_covered = models.TextField(verbose_name=u"Skills Covered", default=u"Skills not specified")
    course_language = models.CharField(max_length=100, verbose_name=u"Course Language", default=u"Russian")
    related_disciplines = models.CharField(max_length=255, verbose_name=u"Discipline(s) for Credit Transfer", default=u"Not Specified")
    credit_workload = models.FloatField(verbose_name=u"Credit Workload", default=0.0)

    def __str__(self):
        return self.course_name

    class Meta:
        app_label = 'retake_catalog'
