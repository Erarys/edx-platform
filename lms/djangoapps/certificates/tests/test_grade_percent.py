"""The diploma must show current progress for its owner and course, not an old snapshot."""

from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from django.db import DatabaseError

from lms.djangoapps.certificates.views.webview import _certificate_grade_percent, _current_certificate_grade_percent
from lms.djangoapps.grades.models import PersistentCourseGrade


class CertificateGradePercentTests(TestCase):
    def test_exact_thresholds(self):
        for saved, expected in (
            ('0', '0'), ('0.4999', '49.99'), ('0.50', '50'), ('0.6999', '69.99'),
            ('0.70', '70'), ('0.8999', '89.99'), ('0.90', '90'), ('0.95', '95'), ('1', '100'),
        ):
            with self.subTest(saved=saved):
                self.assertEqual(_certificate_grade_percent(saved), Decimal(expected))

    def test_missing_or_invalid_grade_is_not_an_achievement(self):
        for saved in (None, '', 'invalid', 'NaN', 'Infinity', '-0.1', '1.1', '95', True):
            with self.subTest(saved=saved):
                self.assertIsNone(_certificate_grade_percent(saved))


class CurrentCertificateGradeTests(TestCase):
    def setUp(self):
        self.certificate = SimpleNamespace(user_id=60831, course_id='course-v1:KazNU+Eng01+2026_C1', grade='0.70')

    def test_latest_grade_replaces_issuance_snapshot(self):
        with patch.object(PersistentCourseGrade.objects, 'get', return_value=SimpleNamespace(percent_grade=0.95)) as query:
            self.assertEqual(_current_certificate_grade_percent(self.certificate), Decimal('95'))
        query.assert_called_once_with(user_id=60831, course_id=self.certificate.course_id)
        self.assertEqual(self.certificate.grade, '0.70')

    def test_each_render_reads_the_current_value_including_zero(self):
        with patch.object(PersistentCourseGrade.objects, 'get', side_effect=[
            SimpleNamespace(percent_grade=0.70), SimpleNamespace(percent_grade=0.99), SimpleNamespace(percent_grade=0.0),
        ]) as query:
            self.assertEqual(_current_certificate_grade_percent(self.certificate), Decimal('70'))
            self.assertEqual(_current_certificate_grade_percent(self.certificate), Decimal('99'))
            self.assertEqual(_current_certificate_grade_percent(self.certificate), Decimal('0'))
        self.assertEqual(query.call_count, 3)

    def test_missing_current_grade_does_not_fall_back_to_old_certificate(self):
        with patch.object(PersistentCourseGrade.objects, 'get', side_effect=PersistentCourseGrade.DoesNotExist):
            self.assertIsNone(_current_certificate_grade_percent(self.certificate))

    def test_preview_does_not_query_a_viewers_grade(self):
        for user_id, course_id in [(None, self.certificate.course_id), (60831, None)]:
            with patch.object(PersistentCourseGrade.objects, 'get') as query:
                self.assertIsNone(_current_certificate_grade_percent(SimpleNamespace(user_id=user_id, course_id=course_id)))
            query.assert_not_called()

    def test_invalid_current_grade_does_not_fall_back_to_old_certificate(self):
        with patch.object(PersistentCourseGrade.objects, 'get', return_value=SimpleNamespace(percent_grade=float('nan'))):
            self.assertIsNone(_current_certificate_grade_percent(self.certificate))

    def test_database_failure_does_not_prevent_rendering(self):
        with patch.object(PersistentCourseGrade.objects, 'get', side_effect=DatabaseError('unavailable')), \
             patch('lms.djangoapps.certificates.views.webview.log') as log:
            self.assertIsNone(_current_certificate_grade_percent(self.certificate))
        log.exception.assert_called_once()
