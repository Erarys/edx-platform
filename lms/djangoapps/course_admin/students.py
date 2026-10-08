"""Account preparation and administrative enrollment for the private LMS page."""

import logging
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.models import CourseEnrollment, UserProfile
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

LOG = logging.getLogger(__name__)
ACTIONS = ('enroll_students', 'create_students', 'create_and_enroll_students')
DEFAULT_EMAIL_DOMAIN = 'open.edu.kz'
MAX_INPUT_LENGTH = 100000


def parse_students(raw):
    """Accept plain logins/emails and pasted numbered or Markdown email lists."""
    if not isinstance(raw, str) or len(raw) > MAX_INPUT_LENGTH:
        raise ValueError('Список студентов слишком большой.')
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    limit = getattr(settings, 'COURSE_ADMIN_MAX_STUDENT_BATCH_SIZE', 200)
    if not lines or len(lines) > limit:
        raise ValueError(f'Укажите от 1 до {limit} студентов, по одному на строку.')
    students, seen = [], set()
    for line in lines:
        value = re.sub(r'^\d+[.)]\s+', '', line).strip()
        markdown = re.fullmatch(r'\[([^\]]+)\]\(mailto:[^)]+\)', value)
        if markdown:
            value = markdown[1]
        value = value.lstrip('.')
        row = {'input': line, 'username': '', 'email': '', 'explicit_email': '@' in value}
        try:
            username = value.split('@', 1)[0] if row['explicit_email'] else value
            email = value if row['explicit_email'] else f'{username}@{DEFAULT_EMAIL_DOMAIN}'
            if not username or len(username) > get_user_model()._meta.get_field('username').max_length:
                raise ValidationError('Некорректная длина логина.')
            UnicodeUsernameValidator()(username)
            validate_email(email)
            if len(email) > get_user_model()._meta.get_field('email').max_length:
                raise ValidationError('Слишком длинный email.')
            row.update(username=username, email=email)
            identity = username.casefold()
            if identity in seen:
                row['error'] = 'Этот логин уже указан выше в списке.'
            else:
                seen.add(identity)
        except ValidationError as exc:
            row['error'] = '; '.join(exc.messages)
        students.append(row)
    return students


def _course_key(identifier):
    try:
        key = CourseKey.from_string(identifier.strip())
        if type(key) is not CourseLocator or key.deprecated or key.branch or key.version_guid:  # pylint: disable=unidiomatic-typecheck
            raise ValueError
    except (AttributeError, InvalidKeyError, ValueError) as exc:
        raise ValueError('Укажите корректный ID курса вида course-v1:ORG+COURSE+RUN.') from exc
    try:
        CourseOverview.get_from_id(key)
    except CourseOverview.DoesNotExist as exc:
        raise ValueError('Курс с указанным ID не найден.') from exc
    return key


def _resolve_user(student, create):
    """Never choose silently between identities with conflicting login/email."""
    User = get_user_model()
    username, email = student['username'], student['email']
    user = User.objects.select_for_update().filter(username=username).first()
    email_users = list(User.objects.select_for_update().filter(email__iexact=email)[:2])
    if len(email_users) > 1 or (user and email_users and email_users[0].pk != user.pk):
        raise ValueError('Логин и email указывают на разные учётки или email используется несколькими учётками.')

    if user is None:
        legacy = [
            candidate for candidate in User.objects.select_for_update().filter(
                username__startswith='.', username__endswith=username,
            ) if candidate.username.lstrip('.') == username
        ]
        if len(legacy) > 1:
            raise ValueError('Найдено несколько учёток с начальной точкой. Требуется выбрать одну вручную.')
        if legacy:
            user = legacy[0]
            if email_users and email_users[0].pk != user.pk:
                raise ValueError('Email принадлежит другой учётке.')
        elif email_users and student['explicit_email']:
            # Enrollment-only can use an existing email with a different login.
            # Creating a Univer identity must not bind that login to a different user.
            if create:
                raise ValueError('Email уже существует под другим логином. Используйте его фактический логин.')
            user = email_users[0]

    if user is not None and not user.is_active:
        raise ValueError('Учётка отключена. Запись на курс не изменена.')
    if user is None:
        if not create:
            raise ValueError('Учётка не найдена. Используйте «Создать и записать».')
        if email_users:
            raise ValueError('Email уже принадлежит другой учётке.')
        user = User.objects.create_user(username=username, email=email, password=None)
        return user, 'created'

    # Rename only when preparing accounts; enrollment-only preserves identity data.
    account_state = 'existing'
    if create and user.username.startswith('.'):
        user.username = username
        fields = ['username']
        if user.email.startswith('.') and user.email.lstrip('.').casefold() == email.casefold():
            user.email = email
            fields.append('email')
        user.save(update_fields=fields)
        account_state = 'renamed'
    return user, account_state


def process_students(actor, raw_students, identifier, action):
    """Apply each student independently, with a rollback on any per-row error."""
    if not actor.is_authenticated or not actor.is_active or not actor.is_staff:
        raise PermissionDenied()
    if action not in ACTIONS:
        raise ValueError('Неизвестное действие со студентами.')
    students = parse_students(raw_students)
    create = action != 'enroll_students'
    enroll = action != 'create_students'
    key = _course_key(identifier) if enroll else None
    results = []
    for student in students:
        result = {
            'input': student['input'], 'username': student['username'], 'email': student['email'],
            'course_id': str(key) if key else '', 'state': 'failed', 'message': '',
        }
        try:
            if student.get('error'):
                raise ValueError(student['error'])
            with transaction.atomic():
                user, account_state = _resolve_user(student, create)
                UserProfile.objects.get_or_create(user=user)
                enrollment_state = ''
                if enroll:
                    existing = CourseEnrollment.objects.select_for_update().filter(user=user, course_id=key).first()
                    if existing and existing.is_active:
                        enrollment_state = 'already_enrolled'
                    else:
                        enrollment = CourseEnrollment.enroll(
                            user, key, mode=existing.mode if existing else None, check_access=False,
                        )
                        if not enrollment or not enrollment.is_active:
                            raise ValueError('Не удалось активировать регистрацию на курс.')
                        enrollment_state = 'reactivated' if existing else 'enrolled'
                messages = {
                    'created': 'Учётка создана.', 'existing': 'Использована существующая учётка.',
                    'renamed': 'Убрана начальная точка в логине существующей учётки.',
                    'enrolled': 'Студент записан на курс.', 'reactivated': 'Регистрация восстановлена.',
                    'already_enrolled': 'Студент уже записан на курс.',
                }
                successful = {
                    'user_id': user.pk, 'username': user.username, 'email': user.email,
                    'state': 'succeeded', 'account_state': account_state,
                    'enrollment_state': enrollment_state,
                    'message': ' '.join(messages[state] for state in (account_state, enrollment_state) if state),
                }
            result.update(successful)
            LOG.info('Course admin student action=%s actor=%s learner=%s course=%s', action, actor.pk, user.pk, key)
        except (ValueError, ValidationError) as exc:
            result['message'] = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        except Exception:  # pylint: disable=broad-except
            LOG.exception('Course admin student failure action=%s actor=%s course=%s', action, actor.pk, key)
            result['message'] = 'Операция для студента не выполнена. Подробности записаны в журнал LMS.'
        results.append(result)
    return results
