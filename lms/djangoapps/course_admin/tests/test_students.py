"""Identity conflicts and partial-batch behavior in course administration."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from lms.djangoapps.course_admin import students

COURSE_ID = 'course-v1:kaznu+C1+2026_C1'


class StudentAdministrationTests(TestCase):
    """Account changes use real ORM transactions; enrollment events are isolated."""

    def setUp(self):
        super().setUp()
        self.actor = get_user_model().objects.create_user('admin', is_staff=True)
        self.overview = self.enterContext(patch.object(students.CourseOverview, 'get_from_id'))
        self.profile = self.enterContext(patch.object(students.UserProfile.objects, 'get_or_create'))
        self.enrollment_lookup = self.enterContext(patch.object(students.CourseEnrollment.objects, 'select_for_update'))
        self.enrollment_lookup.return_value.filter.return_value.first.return_value = None
        self.enroll = self.enterContext(patch.object(students.CourseEnrollment, 'enroll'))
        self.enroll.return_value = SimpleNamespace(is_active=True)

    def process(self, raw, action='create_and_enroll_students', course=COURSE_ID):
        return students.process_students(self.actor, raw, course, action)

    def test_numbered_markdown_and_dotted_logins(self):
        rows = students.parse_students('1. [.Bob@open.edu.kz](mailto:.Bob@open.edu.kz)\n\n..alice')
        self.assertEqual([(row['username'], row['email']) for row in rows], [
            ('Bob', 'Bob@open.edu.kz'), ('alice', 'alice@open.edu.kz'),
        ])

    def test_invalid_and_duplicate_rows_do_not_stop_valid_rows(self):
        rows = self.process('alice\nbad email\nAlice\nbob@open.edu.kz')
        self.assertEqual([row['state'] for row in rows], ['succeeded', 'failed', 'failed', 'succeeded'])
        self.assertEqual(get_user_model().objects.filter(username__in=['alice', 'bob']).count(), 2)
        self.assertEqual(self.enroll.call_count, 2)

    @override_settings(COURSE_ADMIN_MAX_STUDENT_BATCH_SIZE=2)
    def test_batch_limit_is_checked_before_creating_accounts(self):
        with self.assertRaises(ValueError):
            self.process('alice\nbob\ncharlie')
        self.assertFalse(get_user_model().objects.filter(username='alice').exists())

    def test_non_admin_cannot_prepare_accounts(self):
        self.actor.is_staff = False
        with self.assertRaises(PermissionDenied):
            self.process('alice')
        self.assertFalse(get_user_model().objects.filter(username='alice').exists())

    def test_course_must_exist_before_any_account_is_created(self):
        self.overview.side_effect = students.CourseOverview.DoesNotExist
        with self.assertRaises(ValueError):
            self.process('alice')
        self.assertFalse(get_user_model().objects.filter(username='alice').exists())

    def test_create_only_does_not_require_course_or_enroll(self):
        row = self.process('alice', action='create_students', course='')[0]
        user = get_user_model().objects.get(username='alice')
        self.assertEqual(row['state'], 'succeeded')
        self.assertFalse(user.has_usable_password())
        self.overview.assert_not_called()
        self.enroll.assert_not_called()

    def test_enrollment_only_never_creates_missing_account(self):
        row = self.process('alice', action='enroll_students')[0]
        self.assertEqual(row['state'], 'failed')
        self.assertFalse(get_user_model().objects.filter(username='alice').exists())
        self.enroll.assert_not_called()

    def test_existing_password_and_email_are_preserved(self):
        user = get_user_model().objects.create_user('alice', email='alice@example.org', password='original')
        row = self.process('alice@open.edu.kz')[0]
        user.refresh_from_db()
        self.assertEqual(row['user_id'], user.pk)
        self.assertEqual(user.email, 'alice@example.org')
        self.assertTrue(user.check_password('original'))
        self.assertEqual(row['account_state'], 'existing')

    def test_email_owner_with_different_login_is_not_replaced_when_creating(self):
        user = get_user_model().objects.create_user('other', email='alice@open.edu.kz')
        row = self.process('alice@open.edu.kz')[0]
        self.assertEqual(row['state'], 'failed')
        self.assertFalse(get_user_model().objects.filter(username='alice').exists())
        user.refresh_from_db()
        self.assertEqual(user.username, 'other')
        self.enroll.assert_not_called()

    def test_enrollment_only_can_find_existing_unique_email(self):
        user = get_user_model().objects.create_user('other', email='alice@example.org')
        row = self.process('alice@example.org', action='enroll_students')[0]
        self.assertEqual(row['user_id'], user.pk)
        self.assertEqual(row['username'], 'other')
        self.assertEqual(row['state'], 'succeeded')

    def test_conflicting_username_and_email_are_rejected(self):
        get_user_model().objects.create_user('alice', email='alice@example.org')
        get_user_model().objects.create_user('other', email='alice@open.edu.kz')
        self.assertEqual(self.process('alice@open.edu.kz')[0]['state'], 'failed')
        self.enroll.assert_not_called()

    def test_multiple_owners_of_same_email_are_rejected(self):
        get_user_model().objects.create_user('first', email='alice@open.edu.kz')
        get_user_model().objects.create_user('second', email='alice@open.edu.kz')
        self.assertEqual(self.process('alice@open.edu.kz')[0]['state'], 'failed')
        self.enroll.assert_not_called()

    def test_one_legacy_account_is_renamed_preserving_id(self):
        user = get_user_model().objects.create_user('.alice', email='.alice@open.edu.kz')
        row = self.process('..alice')[0]
        user.refresh_from_db()
        self.assertEqual(row['user_id'], user.pk)
        self.assertEqual(row['account_state'], 'renamed')
        self.assertEqual(user.username, 'alice')
        self.assertEqual(user.email, 'alice@open.edu.kz')

    def test_multiple_legacy_accounts_are_not_merged(self):
        get_user_model().objects.create_user('.alice')
        get_user_model().objects.create_user('..alice')
        self.assertEqual(self.process('alice')[0]['state'], 'failed')
        self.assertFalse(get_user_model().objects.filter(username='alice').exists())

    def test_failed_enrollment_rolls_back_creation_and_continues(self):
        self.enroll.side_effect = [RuntimeError('worker failure'), SimpleNamespace(is_active=True)]
        rows = self.process('alice\nbob')
        self.assertEqual([row['state'] for row in rows], ['failed', 'succeeded'])
        self.assertFalse(get_user_model().objects.filter(username='alice').exists())
        self.assertTrue(get_user_model().objects.filter(username='bob').exists())

    def test_failed_enrollment_rolls_back_legacy_rename(self):
        user = get_user_model().objects.create_user('.alice', email='.alice@open.edu.kz')
        self.enroll.side_effect = ValueError('Enrollment prevented')
        self.assertEqual(self.process('alice')[0]['state'], 'failed')
        user.refresh_from_db()
        self.assertEqual(user.username, '.alice')

    def test_inactive_account_is_not_reactivated(self):
        user = get_user_model().objects.create_user('alice', is_active=False)
        self.assertEqual(self.process('alice')[0]['state'], 'failed')
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.enroll.assert_not_called()

    def test_already_enrolled_preserves_track_and_does_not_repeat_events(self):
        get_user_model().objects.create_user('alice')
        self.enrollment_lookup.return_value.filter.return_value.first.return_value = SimpleNamespace(
            is_active=True, mode='verified',
        )
        row = self.process('alice')[0]
        self.assertEqual(row['enrollment_state'], 'already_enrolled')
        self.enroll.assert_not_called()

    def test_reactivation_preserves_existing_track(self):
        get_user_model().objects.create_user('alice')
        self.enrollment_lookup.return_value.filter.return_value.first.return_value = SimpleNamespace(
            is_active=False, mode='honor',
        )
        row = self.process('alice')[0]
        self.assertEqual(row['enrollment_state'], 'reactivated')
        self.assertEqual(self.enroll.call_args.kwargs['mode'], 'honor')
