"""The diploma must use a valid, exact grade snapshot rather than a rounded tier."""

from decimal import Decimal
from unittest import TestCase

from lms.djangoapps.certificates.views.webview import _certificate_grade_percent


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
