"""Staff/CSRF checks, preview binding, and append-only modulestore behavior."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch
import uuid

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404, HttpResponse
from django.test import RequestFactory, SimpleTestCase
from opaque_keys.edx.locator import LibraryLocator

from lms.djangoapps.library_tools import excel, questions, views


class LibraryToolsTests(SimpleTestCase):
    """No existing question can be updated or deleted through these tools."""

    def setUp(self):
        self.key = LibraryLocator('KazNU', 'ST2026')
        self.library = SimpleNamespace(location=self.key.for_branch('library').make_usage_key('library', 'library'),
                                       display_name='Банк', children=[])
        self.store = Mock()
        self.store.get_library.return_value = self.library
        self.store.get_libraries.return_value = [self.library]
        self.store.bulk_operations.side_effect = lambda *args, **kwargs: nullcontext()
        self.question = questions.Question('Тест', 'Вопрос?', 'single', ['Да', 'Нет'], ['A'], 2)
        self.preview = {'library': str(self.key), 'token': str(uuid.uuid4()), 'questions': [self.question.to_dict()]}

    def request(self, method='get', data=None, staff=True, csrf=True):
        request = getattr(RequestFactory(), method)('/library-tools/', data=data or {})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=staff, id=1)
        request.session = {}
        request._dont_enforce_csrf_checks = csrf  # pylint: disable=protected-access
        return request

    def run_view(self, request):
        with patch.object(views, 'modulestore', return_value=self.store), \
             patch.object(views, 'has_studio_read_access', return_value=True), \
             patch.object(views, 'has_studio_write_access', return_value=True), \
             patch.object(views, 'reverse', side_effect=lambda name: '/'+name.replace('_', '-')+'/'), \
             patch.object(views, 'render_to_response', return_value=HttpResponse()) as render:
            result = views.library_tools(request)
        return result, render

    def test_non_staff_and_inactive_admin_denied_before_store_access(self):
        for active in (True, False):
            request = self.request(staff=not active)
            request.user.is_active = active
            with patch.object(views, 'modulestore') as store:
                self.assertEqual(views.library_tools(request).status_code, 403)
            store.assert_not_called()

    def test_post_requires_csrf(self):
        response, _ = self.run_view(self.request('post', {'library': str(self.key)}, csrf=False))
        self.assertEqual(response.status_code, 403)
        self.store.get_library.assert_not_called()

    def test_library_keys_are_canonical_and_use_library_branch(self):
        views._library(self.store, Mock(), str(self.key))
        self.store.get_library.assert_called_once_with(self.key.for_branch('library'), remove_branch=False)
        with self.assertRaises(Http404):
            views._library(self.store, Mock(), 'course-v1:KazNU+C1+2026_C1')

    def test_read_only_library_cannot_import(self):
        with patch.object(views, 'has_studio_read_access', return_value=True), \
             patch.object(views, 'has_studio_write_access', return_value=False), self.assertRaises(PermissionDenied):
            views._library(self.store, Mock(), str(self.key), write=True)
        self.store.get_library.assert_not_called()

    def test_upload_only_validates_and_stages(self):
        file = SimpleUploadedFile('tests.csv', questions.csv_bytes([(self.question, 'old-block')]))
        request = self.request('post', {'library': str(self.key), 'action': 'preview', 'questions_file': file})
        response, _ = self.run_view(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.session[views.PREVIEW_KEY]['library'], str(self.key))
        self.store.create_child.assert_not_called()

    def test_invalid_upload_drops_old_preview_and_does_not_write(self):
        request = self.request('post', {'library': str(self.key), 'action': 'preview',
                                      'questions_file': SimpleUploadedFile('bad.csv', b'bad')})
        request.session[views.PREVIEW_KEY] = self.preview
        self.run_view(request)
        self.assertNotIn(views.PREVIEW_KEY, request.session)
        self.assertIn('error', request.session[views.FEEDBACK_KEY])
        self.store.create_child.assert_not_called()

    def test_import_cannot_change_preview_target_or_token(self):
        self.store.get_library.side_effect = lambda key, **kwargs: SimpleNamespace(
            location=key.make_usage_key('library', 'library'), display_name='Банк', children=[],
        )
        for token, library_id in [('wrong', str(self.key)), (self.preview['token'], 'library-v1:KazNU+OTHER')]:
            request = self.request('post', {'library': library_id, 'action': 'import', 'token': token})
            request.session[views.PREVIEW_KEY] = self.preview
            self.run_view(request)
            self.store.create_child.assert_not_called()

    def test_success_creates_new_block_and_consumes_preview(self):
        request = self.request('post', {'library': str(self.key), 'action': 'import', 'token': self.preview['token']})
        request.session[views.PREVIEW_KEY] = self.preview
        response, _ = self.run_view(request)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(views.PREVIEW_KEY, request.session)
        self.assertEqual(request.session[views.FEEDBACK_KEY]['results'][0]['status'], 'created')
        args, kwargs = self.store.create_child.call_args
        self.assertEqual(args, (1, self.library.location, 'problem'))
        self.assertEqual(kwargs['fields']['data'], questions.question_xml(self.question))
        self.store.update_item.assert_not_called()
        self.store.delete_item.assert_not_called()

    def test_retry_skips_a_block_created_from_same_batch(self):
        new_id = uuid.uuid5(uuid.UUID(self.preview['token']), str(self.question.row)).hex
        self.library.children = [self.key.make_usage_key('problem', new_id)]
        results = views._import(self.store, self.library, SimpleNamespace(id=1), self.preview)
        self.assertEqual(results[0]['status'], 'already_added')
        self.store.create_child.assert_not_called()

    def test_row_save_failure_is_reported_and_preview_retained(self):
        request = self.request('post', {'library': str(self.key), 'action': 'import', 'token': self.preview['token']})
        request.session[views.PREVIEW_KEY] = self.preview
        self.store.create_child.side_effect = RuntimeError('db failure')
        self.run_view(request)
        self.assertIn(views.PREVIEW_KEY, request.session)
        self.assertEqual(request.session[views.FEEDBACK_KEY]['results'][0]['status'], 'failed')

    def test_export_is_read_only_and_excludes_non_problem_components(self):
        key = self.key.make_usage_key('problem', 'p1')
        self.library.children = [key, self.key.make_usage_key('html', 'html')]
        self.store.get_item.return_value = SimpleNamespace(data=questions.question_xml(self.question), display_name='Тест')
        request = self.request(data={'library': str(self.key), 'action': 'export'})
        response, _ = self.run_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(excel.parse_xlsx(response.content).questions[0].question, self.question.question)
        self.store.create_child.assert_not_called()
        self.store.get_item.assert_called_once_with(key)

    def test_list_search_and_empty_library(self):
        response, render = self.run_view(self.request(data={'q': 'ST2026'}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(render.call_args.args[1]['library_total'], 1)
        self.assertEqual(render.call_args.args[1]['libraries'][0]['id'], str(self.key))

    def test_unsupported_problems_are_reported(self):
        key = self.key.make_usage_key('problem', 'complex')
        self.library.children = [key]
        self.store.get_item.return_value = SimpleNamespace(data='<problem><numericalresponse/></problem>', display_name='Complex')
        supported, unsupported = views._inventory(self.store, self.library)
        self.assertEqual(supported, [])
        self.assertIn('Complex', unsupported[0]['title'])

    def test_excel_upload_only_stages_new_questions(self):
        file = SimpleUploadedFile('bank.xlsx', excel.xlsx_bytes([(self.question, 'old-id')]))
        request = self.request('post', {'library': str(self.key), 'action': 'preview', 'questions_file': file})
        self.run_view(request)
        self.assertEqual(request.session[views.PREVIEW_KEY]['questions'][0]['question'], self.question.question)
        self.store.create_child.assert_not_called()

    def test_bad_excel_rows_are_reported_and_nothing_is_staged(self):
        invalid = questions.Question('Bad', 'Broken', 'dropdown', ['One', 'Two'], ['A', 'B'])
        file = SimpleUploadedFile('bank.xlsx', excel.xlsx_bytes([(self.question, ''), (invalid, '')]))
        request = self.request('post', {'library': str(self.key), 'action': 'preview', 'questions_file': file})
        self.run_view(request)
        self.assertNotIn(views.PREVIEW_KEY, request.session)
        self.assertEqual(request.session[views.FEEDBACK_KEY]['validation_errors'][0]['row'], 3)
        self.store.create_child.assert_not_called()

    def test_zero_supported_questions_still_downloads_excel_with_reasons(self):
        self.library.children = [self.key.make_usage_key('problem', 'unsupported')]
        self.store.get_item.return_value = SimpleNamespace(data='<problem><numericalresponse/></problem>', display_name='Numerical')
        response, _ = self.run_view(self.request(data={'library': str(self.key), 'action': 'export'}))
        self.assertEqual(response.status_code, 200)
        self.assertIn('.xlsx', response['Content-Disposition'])
        from zipfile import ZipFile  # pylint: disable=import-outside-toplevel
        import io  # pylint: disable=import-outside-toplevel
        with ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn('xl/worksheets/sheet2.xml', archive.namelist())
