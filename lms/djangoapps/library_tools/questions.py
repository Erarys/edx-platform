"""Plain-text choice questions exchanged as UTF-8 CSV, never executable OLX."""

import csv
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_QUESTIONS = 500
MAX_FIELD_LENGTH = 10000
LETTERS = 'ABCDEFGHIJ'
FIELDS = ('title', 'question', 'type', *(f'answer_{letter.lower()}' for letter in LETTERS), 'correct_answers', 'source_id')
HEADERS = ('Название', 'Вопрос', 'Тип', *(f'Вариант {letter}' for letter in LETTERS), 'Правильные варианты', 'Исходный ID (справочно)')
HEADER_MAP = {name.casefold(): field for name, field in zip(HEADERS, FIELDS)}
HEADER_MAP.update({field: field for field in FIELDS})
PLAIN_TAGS = {'p', 'div', 'span', 'br', 'b', 'strong', 'i', 'em', 'u'}
INVALID_XML_CHAR = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')


@dataclass(frozen=True)
class Question:
    """One new Problem component, with option letters as the answer key."""

    title: str
    question: str
    type: str
    answers: list
    correct_answers: list
    row: int = 0

    def to_dict(self):
        """JSON-safe preview stored in the authenticated session."""
        return asdict(self)


def _unescape_csv(value):
    # Our exports quote formula-looking text for Excel. Restore it as literal text.
    return value[1:] if len(value) > 1 and value[0] == "'" and value[1] in '=+-@' else value


def parse_csv(content):
    """Validate the WHOLE upload before allowing any writes to the library."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError('Файл превышает 2 МБ.')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ValueError('Сохраните файл как CSV UTF-8 в Excel.') from error
    if not text.strip():
        raise ValueError('Файл пуст.')
    try:
        delimiter = csv.Sniffer().sniff(text[:8192], delimiters=',;\t').delimiter
    except csv.Error:
        delimiter = ','
    # Sniffer can mistake Excel's formula-escaping apostrophe for a quote character.
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, quotechar='"', strict=True)
    try:
        raw_headers = next(reader)
        headers = [HEADER_MAP.get(name.strip().casefold()) for name in raw_headers]
        if None in headers or len(headers) != len(set(headers)):
            raise ValueError('Неизвестные или повторяющиеся столбцы. Используйте шаблон CSV.')
        required = {'question', 'answer_a', 'answer_b', 'correct_answers'}
        if not required.issubset(headers):
            raise ValueError('Нужны столбцы: Вопрос, Вариант A, Вариант B, Правильные варианты.')
        questions = []
        for cells in reader:
            row_number = reader.line_num
            if not any(value.strip() for value in cells):
                continue
            if len(questions) >= MAX_QUESTIONS:
                raise ValueError('За одну загрузку можно добавить не более 500 вопросов.')
            if len(cells) != len(headers):
                raise ValueError(f'Строка {row_number}: число ячеек не совпадает с заголовком.')
            row = {key: _unescape_csv(value.strip()) for key, value in zip(headers, cells)}
            if any(len(value) > MAX_FIELD_LENGTH or INVALID_XML_CHAR.search(value) for value in row.values()):
                raise ValueError(f'Строка {row_number}: слишком длинный текст или недопустимые символы.')
            answers = [row.get(f'answer_{letter.lower()}', '') for letter in LETTERS]
            while answers and not answers[-1]:
                answers.pop()
            if not row['question'] or len(answers) < 2 or any(not value for value in answers):
                raise ValueError(f'Строка {row_number}: нужны вопрос и минимум два варианта без пропусков.')
            correct = list(dict.fromkeys(re.split(r'[,;\s]+', row['correct_answers'].upper())))
            if not correct or any(letter not in LETTERS[:len(answers)] for letter in correct):
                raise ValueError(f'Строка {row_number}: укажите правильные варианты буквами A–J, например A,C.')
            kind = row.get('type', '') or ('multi' if len(correct) > 1 else 'single')
            kind = kind.lower()
            if kind not in ('single', 'multi') or (kind == 'single' and len(correct) != 1):
                raise ValueError(f'Строка {row_number}: тип single требует один ответ, multi — один или несколько.')
            title = row.get('title') or row['question'][:255]
            if len(title) > 255:
                raise ValueError(f'Строка {row_number}: название превышает 255 символов.')
            # source_id is deliberately ignored: imports ONLY create new blocks.
            questions.append(Question(title, row['question'], kind, answers, correct, row_number))
    except csv.Error as error:
        raise ValueError(f'Некорректный CSV: {error}') from error
    if not questions:
        raise ValueError('В файле нет вопросов.')
    return questions


def question_xml(question):
    """Generate safe CAPA XML: every uploaded question and option is literal text."""
    root = ET.Element('problem')
    response = ET.SubElement(root, 'multiplechoiceresponse' if question.type == 'single' else 'choiceresponse')
    ET.SubElement(response, 'label').text = question.question
    group = ET.SubElement(response, 'choicegroup', {'type': 'MultipleChoice'}) if question.type == 'single' else ET.SubElement(response, 'checkboxgroup')
    for letter, answer in zip(LETTERS, question.answers):
        choice = ET.SubElement(group, 'choice', {'correct': 'true' if letter in question.correct_answers else 'false', 'name': letter.lower()})
        choice.text = answer
    return ET.tostring(root, encoding='unicode')


def _plain(element):
    """Accept text/basic formatting; reject images, math, scripts, and embedded tasks."""
    if any(child.tag not in PLAIN_TAGS or child.attrib for child in element.iter() if child is not element):
        raise ValueError('Вопрос содержит сложную разметку, изображения или настройки.')
    def flatten(node):
        result = node.text or ''
        for child in node:
            result += flatten(child)
            if child.tag in ('p', 'div', 'br'):
                result += '\n'
            result += child.tail or ''
        return result
    return flatten(element).strip()


def extract_question(block):
    """Extract one simple single/multi question; reject anything we cannot round-trip."""
    try:
        root = fromstring(block.data, forbid_dtd=True)
    except (ET.ParseError, DefusedXmlException, TypeError) as error:
        raise ValueError('Некорректный или неподдерживаемый XML.') from error
    if root.tag != 'problem':
        raise ValueError('Не является компонентом Problem.')
    responses = [child for child in root if child.tag in ('multiplechoiceresponse', 'choiceresponse')]
    if len(responses) != 1:
        raise ValueError('Поддерживается один вопрос с выбором ответа в компоненте.')
    response = responses[0]
    if response.attrib or any(child is not response and child.tag not in PLAIN_TAGS for child in root):
        raise ValueError('Подсказки, решения, скрипты и частичные баллы не поддерживаются.')
    kind = 'single' if response.tag == 'multiplechoiceresponse' else 'multi'
    group_tag = 'choicegroup' if kind == 'single' else 'checkboxgroup'
    groups = response.findall(group_tag)
    labels = response.findall('label')
    if len(groups) != 1 or len(labels) > 1:
        raise ValueError('Неподдерживаемая структура вариантов.')
    group = groups[0]
    if any(key != 'type' or value != 'MultipleChoice' for key, value in group.attrib.items()):
        raise ValueError('Перемешивание и специальные настройки вариантов не поддерживаются.')
    choices = list(group)
    if not 2 <= len(choices) <= len(LETTERS) or any(choice.tag != 'choice' for choice in choices):
        raise ValueError('Нужны от 2 до 10 вариантов ответа.')
    allowed = PLAIN_TAGS | {group_tag, 'label'}
    if any(child.tag not in allowed for child in response):
        raise ValueError('Описание или дополнительные настройки вопроса не поддерживаются.')
    if labels:
        prompt = _plain(labels[0])
        # A separate label plus other visible prompt text cannot be flattened reliably.
        if (root.text or '').strip() or any(child is not response for child in root) or (response.text or '').strip() or any(child.tag not in (group_tag, 'label') for child in response):
            raise ValueError('Вопрос содержит дополнительные текстовые блоки.')
    else:
        prompt_nodes = [child for child in root if child is not response] + [child for child in response if child is not group]
        prompt = '\n'.join(filter(None, [(root.text or '').strip(), (response.text or '').strip(), *(_plain(child) for child in prompt_nodes)]))
    answers, correct = [], []
    for letter, choice in zip(LETTERS, choices):
        if any(key not in ('correct', 'name') for key in choice.attrib) or choice.get('correct') not in ('true', 'false'):
            raise ValueError('Частичные баллы или специальные настройки ответа не поддерживаются.')
        answers.append(_plain(choice))
        if choice.get('correct') == 'true':
            correct.append(letter)
    if not prompt or any(not answer for answer in answers) or not correct or (kind == 'single' and len(correct) != 1):
        raise ValueError('Пустой вопрос, варианты или неподдерживаемые правильные ответы.')
    return Question(block.display_name or prompt[:255], prompt, kind, answers, correct)


def csv_bytes(rows):
    """Excel-friendly BOM, semicolon separators, multiline quoting, formula escaping."""
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter=';')
    writer.writerow(HEADERS)
    for question, source_id in rows:
        values = [question.title, question.question, question.type, *question.answers,
                  *([''] * (len(LETTERS) - len(question.answers))), ','.join(question.correct_answers), source_id]
        writer.writerow(["'" + value if value and value[0] in '=+-@' else value for value in values])
    return stream.getvalue().encode('utf-8-sig')
