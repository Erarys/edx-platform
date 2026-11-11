"""Admin-only LMS access to legacy libraries using the Studio modulestore APIs."""

import logging
import math
import secrets
import string
import uuid
from functools import wraps
from itertools import zip_longest
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator
from organizations.api import ensure_organization

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.auth import has_studio_read_access, has_studio_write_access
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, GlobalStaff
from xmodule.matching_block import MAX_PAIRS, MAX_TEXT_LENGTH, normalize_pairs
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import DuplicateCourseError

from . import excel, questions

LOG = logging.getLogger(__name__)
PREVIEW_KEY = 'library_tools.preview.v1'
NEW_LIBRARY_PREVIEW_KEY = 'library_tools.new_library_preview.v1'
FEEDBACK_KEY = 'library_tools.feedback.v1'
NEW_LIBRARY_ORG = 'KazNU'
LIBRARY_CODE_LENGTH = 6
MAX_LIBRARY_FILES = 20
MAX_LIBRARY_QUESTIONS = questions.MAX_QUESTIONS
MAX_LIBRARY_NAME_LENGTH = 255


def admin_required(view):
    """Answer keys and library writes are restricted to active platform admins."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_active and GlobalStaff().has_user(request.user)):
            return HttpResponseForbidden('Доступ разрешён только администраторам платформы.')
        return view(request, *args, **kwargs)
    return wrapped


def _library(store, user, value, write=False):
    try:
        key = CourseKey.from_string(value)
    except (InvalidKeyError, TypeError) as error:
        raise Http404('Некорректный ID библиотеки.') from error
    if not isinstance(key, LibraryLocator):
        raise Http404('Поддерживаются только библиотеки V1.')
    key = key.for_branch(None).for_version(None)
    if not has_studio_read_access(user, key) or (write and not has_studio_write_access(user, key, service_variant='cms')):
        raise PermissionDenied()
    # LMS defaults to published-only courses. A library lives on the library branch.
    library = store.get_library(key.for_branch('library'), remove_branch=False)
    if library is None:
        raise Http404('Библиотека не найдена.')
    return library


def _inventory(store, library):
    supported, unsupported = [], []
    with store.bulk_operations(library.location.library_key):
        for key in library.children:
            if key.block_type != 'problem':
                continue
            block = store.get_item(key)
            try:
                extracted, issues = questions.extract_questions(block)
                supported.extend((question, str(key)) for question in extracted)
                unsupported.extend({'title': f'{block.display_name or str(key)} — вопрос {issue["number"]}',
                                    'id': str(key), 'reason': issue['reason']} for issue in issues)
            except ValueError as error:
                unsupported.append({'title': block.display_name or str(key), 'id': str(key), 'reason': str(error)})
    return supported, unsupported


def _redirect(library_id=''):
    url = reverse('library_tools')
    return HttpResponseRedirect(url + ('?' + urlencode({'library': library_id}) if library_id else ''))


def _parse_upload(upload, allow_csv=False):
    """Read and validate one bounded question-bank upload."""
    if upload is None:
        raise ValueError('Выберите файл Excel .xlsx.')
    if upload.size > excel.MAX_UPLOAD_BYTES:
        raise ValueError('Файл превышает 5 МБ.')
    content = upload.read(excel.MAX_UPLOAD_BYTES + 1)
    filename = str(upload.name or '')
    if filename.lower().endswith('.xlsx'):
        return excel.parse_xlsx(content)
    if allow_csv and filename.lower().endswith('.csv'):
        return excel.Upload(questions=questions.parse_csv(content))
    if allow_csv:
        raise ValueError('Поддерживается Excel .xlsx или прежний CSV UTF-8. Формат .xls не поддерживается.')
    raise ValueError('Для создания библиотеки нужен файл Excel .xlsx. Формат .xls не поддерживается.')


def _library_name(upload):
    """Derive a safe display name from an uploaded .xlsx filename."""
    filename = str(upload.name or '').replace('\\', '/').rsplit('/', 1)[-1]
    if not filename.lower().endswith('.xlsx'):
        raise ValueError('Для создания библиотеки нужен файл Excel .xlsx.')
    display_name = questions.clean_text(filename[:-5])
    if not display_name:
        raise ValueError('Название Excel-файла не может быть пустым.')
    if len(display_name) > MAX_LIBRARY_NAME_LENGTH:
        raise ValueError(f'Название файла без расширения должно быть не длиннее {MAX_LIBRARY_NAME_LENGTH} символов.')
    return filename, display_name


def _library_code(reserved):
    """Return a short random code that is unused in KazNU and in this batch."""
    for _attempt in range(100):
        code = ''.join(secrets.choice(string.ascii_lowercase) for _ in range(LIBRARY_CODE_LENGTH))
        if code.casefold() not in reserved:
            reserved.add(code.casefold())
            return code
    raise RuntimeError('Не удалось подобрать свободный код библиотеки. Повторите загрузку.')


def _new_library_preview(store, uploads):
    """Validate all files before allowing the first library write."""
    if not uploads:
        raise ValueError('Выберите хотя бы один файл Excel .xlsx.')
    if len(uploads) > MAX_LIBRARY_FILES:
        raise ValueError(f'За одну загрузку можно создать не более {MAX_LIBRARY_FILES} библиотек.')

    reserved = {
        item.location.library_key.library.casefold()
        for item in store.get_libraries()
        if item.location.library_key.org.casefold() == NEW_LIBRARY_ORG.casefold()
    }
    items = []
    validation_errors = []
    total_questions = 0
    for upload in uploads:
        try:
            filename, display_name = _library_name(upload)
            parsed = _parse_upload(upload)
            validation_errors.extend({
                'filename': filename,
                'row': error.get('row'),
                'detail': error['detail'],
            } for error in parsed.errors)
            total_questions += len(parsed.questions)
            items.append({
                'filename': filename,
                'display_name': display_name,
                'org': NEW_LIBRARY_ORG,
                'code': _library_code(reserved),
                'questions': [question.to_dict() for question in parsed.questions],
                'warnings': parsed.warnings,
                'created': False,
            })
        except ValueError as error:
            filename = str(upload.name or 'Excel-файл').replace('\\', '/').rsplit('/', 1)[-1]
            validation_errors.append({'filename': filename, 'row': None, 'detail': str(error)})

    if total_questions > MAX_LIBRARY_QUESTIONS:
        validation_errors.append({
            'filename': 'Все файлы',
            'row': None,
            'detail': f'За одну операцию можно добавить не более {MAX_LIBRARY_QUESTIONS} вопросов.',
        })
    if validation_errors:
        return None, validation_errors
    return {'token': str(uuid.uuid4()), 'libraries': items}, []


def _grant_library_access(library_key, user):
    """Make the uploader an explicit Studio owner as well as a global admin."""
    CourseInstructorRole(library_key).add_users(user)
    CourseStaffRole(library_key).add_users(user)


def _create_libraries(store, user, preview):
    """Create the staged libraries and append their questions idempotently."""
    results = []
    batch_id = uuid.UUID(preview['token'])
    ensure_organization(NEW_LIBRARY_ORG)
    for item in preview['libraries']:
        key = LibraryLocator(org=item['org'], library=item['code'])
        result = {
            'filename': item['filename'],
            'display_name': item['display_name'],
            'id': str(key),
            'status': '',
            'detail': '',
            'questions': [],
            'exists': False,
        }
        try:
            library = store.get_library(key.for_branch('library'), remove_branch=False)
            if library is None:
                try:
                    with store.default_store(ModuleStoreEnum.Type.split):
                        library = store.create_library(
                            org=item['org'],
                            library=item['code'],
                            user_id=user.id,
                            fields={'display_name': item['display_name']},
                        )
                    item['created'] = True
                    created = True
                except DuplicateCourseError:
                    library = store.get_library(key.for_branch('library'), remove_branch=False)
                    if library is None:
                        raise
                    created = False
            else:
                created = False

            if not created and not item.get('created'):
                raise ValueError('Случайный код уже занят другой библиотекой. Загрузите файл заново.')
            if questions.clean_text(library.display_name or '') != item['display_name']:
                raise ValueError('Библиотека с этим кодом имеет другое название. Загрузите файл заново.')

            result['exists'] = True
            _grant_library_access(library.location.library_key, user)
            question_preview = {
                'token': str(uuid.uuid5(batch_id, item['code'])),
                'questions': item['questions'],
            }
            result['questions'] = _import(store, library, user, question_preview)
            failed = sum(row['status'] == 'failed' for row in result['questions'])
            if failed:
                result.update(
                    status='failed',
                    detail=f'Библиотека создана, но не удалось добавить вопросов: {failed}. Повторите подтверждение.',
                )
            else:
                result.update(
                    status='created' if created else 'already_created',
                    detail='Библиотека и вопросы созданы.' if created else 'Повторная операция завершена без дубликатов.',
                )
        except (ValueError, RuntimeError) as error:
            result.update(status='failed', detail=str(error))
        except Exception:  # pylint: disable=broad-except
            LOG.exception('Could not create library from Excel: org=%s code=%s', item['org'], item['code'])
            result.update(status='failed', detail='Не удалось создать библиотеку. Проверьте журнал LMS.')
        results.append(result)
    return results


def _matching_form_values(data):
    """Return the submitted matching form in a session-safe representation."""
    left_items = data.getlist('matching_left') if hasattr(data, 'getlist') else data.get('matching_left', [])
    right_items = data.getlist('matching_right') if hasattr(data, 'getlist') else data.get('matching_right', [])
    return {
        'title': data.get('matching_title', ''),
        'question': data.get('matching_question', ''),
        'pairs': [
            {'left': left, 'right': right}
            for left, right in zip_longest(left_items, right_items, fillvalue='')
        ],
        'shuffle': data.get('matching_shuffle', '') in ('1', 'true', 'on', 'yes'),
        'max_attempts': data.get('matching_max_attempts', '0'),
        'weight': data.get('matching_weight', '1'),
    }


def _matching_fields(data):
    """Validate a direct Matching form and return fields for ``create_child``."""
    values = _matching_form_values(data)
    title = questions.clean_text(values['title'])
    prompt = questions.clean_text(values['question'])
    if not title or not prompt:
        raise ValueError('Заполните название и текст задания на сопоставление.')
    if len(title) > MAX_TEXT_LENGTH or len(prompt) > MAX_TEXT_LENGTH:
        raise ValueError(f'Название и текст задания должны быть не длиннее {MAX_TEXT_LENGTH} символов.')

    raw_pairs = values['pairs']
    if not 2 <= len(raw_pairs) <= MAX_PAIRS:
        raise ValueError(f'Добавьте от 2 до {MAX_PAIRS} пар.')
    if any(
        len(questions.clean_text(pair['left'])) > MAX_TEXT_LENGTH
        or len(questions.clean_text(pair['right'])) > MAX_TEXT_LENGTH
        for pair in raw_pairs
    ):
        raise ValueError(f'Каждая часть пары должна быть не длиннее {MAX_TEXT_LENGTH} символов.')
    pairs = normalize_pairs([
        {
            'id': f'pair-{index}-{uuid.uuid4().hex[:8]}',
            'right_id': f'answer-{index}-{uuid.uuid4().hex[:8]}',
            'left': questions.clean_text(pair['left']),
            'right': questions.clean_text(pair['right']),
        }
        for index, pair in enumerate(raw_pairs, 1)
    ])
    if len(pairs) != len(raw_pairs):
        raise ValueError('Заполните левую и правую части каждой пары.')
    left_values = [pair['left'].casefold() for pair in pairs]
    right_values = [pair['right'].casefold() for pair in pairs]
    if len(set(left_values)) != len(left_values) or len(set(right_values)) != len(right_values):
        raise ValueError('Значения внутри каждого столбца не должны повторяться.')

    try:
        max_attempts = int(values['max_attempts'])
        weight = float(str(values['weight']).replace(',', '.'))
    except (TypeError, ValueError) as error:
        raise ValueError('Количество попыток и балл должны быть числами.') from error
    if max_attempts < 0:
        raise ValueError('Количество попыток не может быть отрицательным.')
    if not math.isfinite(weight) or weight < 0:
        raise ValueError('Балл должен быть неотрицательным конечным числом.')

    return {
        'display_name': title,
        'question': prompt,
        'pairs': pairs,
        'shuffle_answers': values['shuffle'],
        'max_attempts': max_attempts,
        'weight': weight,
    }


def _import(store, library, user, preview):
    """Append only. Batch-derived IDs make retrying the same preview idempotent."""
    results = []
    batch_id = uuid.UUID(preview['token'])
    present = {key.block_id for key in library.children}
    with store.bulk_operations(library.location.library_key):
        for row in preview['questions']:
            question = questions.Question(**row)
            block_id = uuid.uuid5(batch_id, str(question.row)).hex
            result = {'row': question.row, 'title': question.title, 'status': '', 'detail': ''}
            if block_id in present:
                result.update(status='already_added', detail='Этот вопрос уже добавлен из данной проверки файла.')
            else:
                try:
                    store.create_child(user.id, library.location, 'problem', block_id=block_id,
                                       fields={'display_name': question.title, 'data': questions.question_xml(question)})
                    result.update(status='created', detail='Создан новый вопрос.')
                    present.add(block_id)
                except Exception:  # pylint: disable=broad-except
                    LOG.exception('Library question import failed: library=%s row=%s', library.location.library_key, question.row)
                    result.update(status='failed', detail='Не удалось сохранить вопрос. Проверьте журнал LMS.')
            results.append(result)
    return results


@admin_required
@never_cache
@csrf_protect
@require_http_methods(['GET', 'POST'])
def library_tools(request):
    """Library list, Excel export, full-file validation, and confirmed append-only import."""
    store = modulestore()
    action = request.POST.get('action', '') if request.method == 'POST' else request.GET.get('action', '')
    selected_id = request.POST.get('library', '') if request.method == 'POST' else request.GET.get('library', '')
    library = _library(store, request.user, selected_id, write=request.method == 'POST') if selected_id else None
    if library:
        selected_id = str(library.location.library_key.for_branch(None).for_version(None))
    if request.method == 'POST':
        if action == 'preview_libraries':
            request.session.pop(NEW_LIBRARY_PREVIEW_KEY, None)
            try:
                staged, validation_errors = _new_library_preview(
                    store,
                    request.FILES.getlist('library_files'),
                )
                if validation_errors:
                    request.session[FEEDBACK_KEY] = {
                        'error': 'В Excel-файлах есть ошибки. Ни одна библиотека не создана.',
                        'library_validation_errors': validation_errors,
                    }
                else:
                    request.session[NEW_LIBRARY_PREVIEW_KEY] = staged
            except (RuntimeError, ValueError) as error:
                request.session[FEEDBACK_KEY] = {'error': str(error)}
            return _redirect(selected_id)
        if action == 'create_libraries':
            preview = request.session.get(NEW_LIBRARY_PREVIEW_KEY)
            if not preview or preview['token'] != request.POST.get('token'):
                request.session[FEEDBACK_KEY] = {'error': 'Проверка файлов устарела. Загрузите Excel-файлы заново.'}
                return _redirect(selected_id)
            try:
                results = _create_libraries(store, request.user, preview)
            except Exception:  # pylint: disable=broad-except
                LOG.exception('Could not start Excel library import for user=%s', request.user.id)
                request.session[FEEDBACK_KEY] = {
                    'error': 'Не удалось начать создание библиотек. Ни одна новая запись этой попыткой не подтверждена.',
                }
                return _redirect(selected_id)
            request.session[FEEDBACK_KEY] = {'library_results': results}
            request.session[NEW_LIBRARY_PREVIEW_KEY] = preview
            if not any(result['status'] == 'failed' for result in results):
                request.session.pop(NEW_LIBRARY_PREVIEW_KEY, None)
            first_library = next((result['id'] for result in results if result.get('exists')), selected_id)
            LOG.info(
                'Excel library import user=%s created=%s failed=%s',
                request.user.id,
                sum(result['status'] == 'created' for result in results),
                sum(result['status'] == 'failed' for result in results),
            )
            return _redirect(first_library)
        if action == 'cancel_libraries':
            request.session.pop(NEW_LIBRARY_PREVIEW_KEY, None)
            return _redirect(selected_id)
        if not library:
            request.session[FEEDBACK_KEY] = {'error': 'Сначала выберите библиотеку.'}
            return _redirect()
        if action == 'preview':
            request.session.pop(PREVIEW_KEY, None)
            try:
                parsed = _parse_upload(request.FILES.get('questions_file'), allow_csv=True)
                if parsed.errors:
                    request.session[FEEDBACK_KEY] = {
                        'error': 'В файле есть ошибки. Исправьте указанные строки и загрузите файл заново. Вопросы не добавлены.',
                        'validation_errors': parsed.errors, 'warnings': parsed.warnings,
                    }
                else:
                    request.session[PREVIEW_KEY] = {'library': selected_id, 'token': str(uuid.uuid4()),
                                                    'questions': [question.to_dict() for question in parsed.questions],
                                                    'warnings': parsed.warnings}
            except ValueError as error:
                request.session[FEEDBACK_KEY] = {'error': str(error)}
        elif action == 'import':
            preview = request.session.get(PREVIEW_KEY)
            if not preview or preview['library'] != selected_id or preview['token'] != request.POST.get('token'):
                request.session[FEEDBACK_KEY] = {'error': 'Проверка файла устарела. Загрузите файл заново.'}
            else:
                try:
                    results = _import(store, library, request.user, preview)
                    LOG.info('Library question import user=%s library=%s created=%s failed=%s',
                             request.user.id, selected_id, sum(row['status'] == 'created' for row in results),
                             sum(row['status'] == 'failed' for row in results))
                    request.session[FEEDBACK_KEY] = {'results': results}
                    if not any(result['status'] == 'failed' for result in results):
                        request.session.pop(PREVIEW_KEY, None)
                except Exception:  # pylint: disable=broad-except
                    LOG.exception('Could not finish library question import: %s', selected_id)
                    request.session[FEEDBACK_KEY] = {'error': 'Не удалось завершить загрузку. Часть вопросов могла сохраниться. Повторите добавление из этой проверки файла.'}
        elif action == 'add_matching':
            form_values = _matching_form_values(request.POST)
            try:
                fields = _matching_fields(request.POST)
                block = store.create_child(
                    request.user.id,
                    library.location,
                    'matching',
                    block_id=uuid.uuid4().hex,
                    fields=fields,
                )
                LOG.info(
                    'Matching library question created user=%s library=%s block=%s',
                    request.user.id,
                    selected_id,
                    block.location,
                )
                request.session[FEEDBACK_KEY] = {
                    'matching_created': {
                        'title': fields['display_name'],
                        'id': str(block.location),
                    },
                }
            except ValueError as error:
                request.session[FEEDBACK_KEY] = {'error': str(error), 'matching_form': form_values}
            except Exception:  # pylint: disable=broad-except
                LOG.exception('Could not create Matching question: %s', selected_id)
                request.session[FEEDBACK_KEY] = {
                    'error': 'Не удалось создать вопрос Matching. Проверьте журнал LMS.',
                    'matching_form': form_values,
                }
        elif action == 'cancel':
            request.session.pop(PREVIEW_KEY, None)
        else:
            request.session[FEEDBACK_KEY] = {'error': 'Неизвестная команда.'}
        return _redirect(selected_id)

    if action == 'template':
        example = questions.Question('Пример вопроса', 'Сколько будет 2 + 2?', 'single', ['3', '4', '5'], ['B'])
        examples = [(example, ''), (questions.Question('Выберите простые числа', 'Выберите простые числа.', 'multi',
                                                     ['2', '4', '5', '6'], ['A', 'C']), ''),
                    (questions.Question('Пример списка', 'Выберите столицу Казахстана.', 'dropdown',
                                        ['Астана', 'Алматы', 'Шымкент'], ['A']), ''),
                    (questions.Question('Пример текста', 'Введите столицу Казахстана.', 'text', [], ['Астана']), ''),
                    (questions.Question('Пример числа', 'Сколько будет 2 + 2?', 'numeric', [], ['4']), '')]
        return _xlsx_response(excel.xlsx_bytes(examples), 'questions-template.xlsx')
    supported, unsupported = _inventory(store, library) if library else ([], [])
    if action == 'export':
        if library is None:
            raise Http404('Выберите библиотеку.')
        filename = f'{library.location.library_key.org}_{library.location.library_key.library}_questions'
        return _xlsx_response(excel.xlsx_bytes(supported, unsupported), filename + '.xlsx')

    search = request.GET.get('q', '').strip()[:255]
    libraries = []
    for item in store.get_libraries():
        key = item.location.library_key.for_branch(None).for_version(None)
        name = item.display_name or str(key)
        if has_studio_read_access(request.user, key) and search.casefold() in f'{name} {key}'.casefold():
            libraries.append({'id': str(key), 'name': name, 'components': len(item.children),
                              'url': reverse('library_tools') + '?' + urlencode({'library': str(key)})})
    libraries.sort(key=lambda item: (item['name'].casefold(), item['id']))
    page = Paginator(libraries, 25).get_page(request.GET.get('page'))
    preview = request.session.get(PREVIEW_KEY)
    if preview and preview['library'] != selected_id:
        preview = None
    feedback = request.session.pop(FEEDBACK_KEY, {})
    new_library_preview = request.session.get(NEW_LIBRARY_PREVIEW_KEY)
    matching_form = feedback.get('matching_form') or {
        'title': 'Сопоставление',
        'question': 'Соедините каждый элемент слева с подходящим элементом справа.',
        'pairs': [{'left': '', 'right': ''}, {'left': '', 'right': ''}],
        'shuffle': True,
        'max_attempts': '0',
        'weight': '1',
    }
    return render_to_response('library-tools.html', {
        'libraries': page, 'library_total': len(libraries), 'search': search,
        'selected_library': library, 'selected_id': selected_id,
        'supported_count': len(supported), 'unsupported': unsupported,
        'preview': preview, 'new_library_preview': new_library_preview,
        'feedback': feedback, 'csrf_token': get_token(request),
        'matching_count': sum(key.block_type == 'matching' for key in library.children) if library else 0,
        'matching_form': matching_form,
        'tools_url': reverse('library_tools'), 'course_admin_url': reverse('course_admin'),
        'template_url': reverse('library_tools') + '?action=template',
        'export_url': reverse('library_tools') + '?' + urlencode({'library': selected_id, 'action': 'export'}),
    }, request=request)


def _xlsx_response(content, filename):
    response = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    # Library identifiers do not contain line breaks, and quotes are escaped for the header.
    filename = filename.replace('"', '').replace('\r', '').replace('\n', '')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
