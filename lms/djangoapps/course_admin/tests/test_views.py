"""Authorization, CSRF, and independence of the two admin workflows."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from lms.djangoapps.course_admin import views


class StudentFormTests(SimpleTestCase):
    """Student POSTs use the same staff-only, CSRF-protected route."""

    def request(self, staff=True, csrf=True):
        request = RequestFactory().post('/course-admin/', {
            'action': 'create_and_enroll_students', 'student_list': 'alice',
            'student_course_id': 'course-v1:kaznu+C1+2026_C1',
        })
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=staff, pk=1)
        request.session = {views.FORM_SESSION_KEY: {'start': '2027-01-01T09:00'}}
        request._dont_enforce_csrf_checks = csrf  # pylint: disable=protected-access
        return request

    def test_non_staff_cannot_submit_student_operations(self):
        with patch.object(views.students, 'process_students') as process:
            response = views.course_admin(self.request(staff=False))
        self.assertEqual(response.status_code, 403)
        process.assert_not_called()

    def test_post_requires_csrf_token(self):
        with patch.object(views.students, 'process_students') as process:
            response = views.course_admin(self.request(csrf=False))
        self.assertEqual(response.status_code, 403)
        process.assert_not_called()

    def test_student_post_preserves_course_settings_and_redirects(self):
        request = self.request()
        results = [{'state': 'succeeded', 'username': 'alice'}]
        with patch.object(views.students, 'process_students', return_value=results) as process, \
                patch.object(views, 'reverse', return_value='/course-admin/'):
            response = views.course_admin(request)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith('#students'))
        self.assertEqual(request.session[views.FORM_SESSION_KEY]['start'], '2027-01-01T09:00')
        self.assertEqual(request.session[views.STUDENT_FORM_SESSION_KEY]['student_list'], 'alice')
        self.assertEqual(request.session[views.FEEDBACK_SESSION_KEY]['student_results'], results)
        process.assert_called_once_with(
            request.user, 'alice', 'course-v1:kaznu+C1+2026_C1', 'create_and_enroll_students',
        )

    def test_invalid_course_is_reported_without_running_course_workflow(self):
        request = self.request()
        with patch.object(views.students, 'process_students', side_effect=ValueError('Курс не найден.')), \
                patch.object(views, '_courses') as courses, \
                patch.object(views, 'reverse', return_value='/course-admin/'):
            response = views.course_admin(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.session[views.FEEDBACK_SESSION_KEY]['error'], 'Курс не найден.')
        courses.assert_not_called()
