"""Basic CAPA questions exchanged as literal text, including legacy CSV uploads."""

import csv
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from html.entities import html5

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_QUESTIONS = 2000
MAX_FIELD_LENGTH = 10000
LETTERS = 'ABCDEFGHIJ'
FIELDS = ('title', 'question', 'type', *(f'answer_{letter.lower()}' for letter in LETTERS), 'correct_answers', 'source_id')
HEADERS = ('Название', 'Вопрос', 'Тип', *(f'Вариант {letter}' for letter in LETTERS), 'Правильные варианты', 'Исходный ID (справочно)')
HEADER_MAP = {name.casefold(): field for name, field in zip(HEADERS, FIELDS)}
HEADER_MAP.update({field: field for field in FIELDS})
PLAIN_TAGS = {'p', 'div', 'span', 'br', 'b', 'strong', 'i', 'em', 'u', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
              'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'td', 'th', 'sub', 'sup', 'a', 'font',
              'blockquote', 'pre', 'code', 'hr', 'label', 'description'}
OMITTED_TAGS = {'choicehint', 'optionhint', 'demandhint', 'hint', 'solution', 'script', 'style',
                'stringequalhint', 'numericalhint', 'correcthint', 'incorrecthint'}
RESPONSE_TYPES = {'multiplechoiceresponse': 'single', 'choiceresponse': 'multi', 'optionresponse': 'dropdown'}
NUMBER_PATTERN = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
INVISIBLE_CHAR = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u00ad\u200b\u202a-\u202e\u2060\u2066-\u2069\ufeff\ufffe\uffff]')
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
                raise ValueError(f'За одну загрузку можно добавить не более {MAX_QUESTIONS} вопросов.')
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
            if kind not in ('single', 'multi', 'dropdown') or (kind == 'dropdown' and len(correct) != 1):
                raise ValueError(f'Строка {row_number}: dropdown требует один ответ, single/multi — один или несколько.')
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
    if question.type in ('text', 'text_case_sensitive'):
        attributes = {'answer': question.correct_answers[0]}
        if question.type == 'text':
            attributes['type'] = 'ci'
        response = ET.SubElement(root, 'stringresponse', attributes)
        ET.SubElement(response, 'label').text = question.question
        for answer in question.correct_answers[1:]:
            ET.SubElement(response, 'additional_answer', {'answer': answer})
        ET.SubElement(response, 'textline')
        return ET.tostring(root, encoding='unicode')
    if question.type == 'numeric':
        answer, tolerance = numeric_answer(question.correct_answers[0])
        response = ET.SubElement(root, 'numericalresponse', {'answer': answer})
        ET.SubElement(response, 'label').text = question.question
        ET.SubElement(response, 'responseparam', {'type': 'tolerance', 'default': tolerance})
        ET.SubElement(response, 'formulaequationinput')
        return ET.tostring(root, encoding='unicode')
    response = ET.SubElement(root, {value: key for key, value in RESPONSE_TYPES.items()}[question.type])
    ET.SubElement(response, 'label').text = question.question
    group = ET.SubElement(response, {'single': 'choicegroup', 'multi': 'checkboxgroup', 'dropdown': 'optioninput'}[question.type])
    for letter, answer in zip(LETTERS, question.answers):
        choice = ET.SubElement(group, 'option' if question.type == 'dropdown' else 'choice',
                               {'correct': 'true' if letter in question.correct_answers else 'false'})
        choice.text = answer
    return ET.tostring(root, encoding='unicode')


def clean_text(value):
    """Remove invisible editing artifacts while keeping accents and language joiners."""
    return unicodedata.normalize('NFC', INVISIBLE_CHAR.sub('', value).replace('\u00a0', ' ')).strip()


def numeric_answer(value):
    """Explicit numeric answer and optional absolute/percentage tolerance: 4 ± 5."""
    pieces = value.split('±')
    if len(pieces) > 2:
        raise ValueError('Числовой ответ: укажите число или число ± допуск, например 4 ± 0.1.')
    answer = pieces[0].strip()
    tolerance = pieces[1].strip() if len(pieces) == 2 else '0'
    if not re.fullmatch(NUMBER_PATTERN, answer) or len(answer) > 100:
        raise ValueError('Числовой ответ должен быть обычным числом, без выражений или переменных.')
    if not re.fullmatch(r'\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?', tolerance) or len(tolerance) > 100:
        raise ValueError('Допуск должен быть неотрицательным числом или процентом, например 0.1 или 5%.')
    return answer, tolerance


def _plain(element):
    """Flatten visible HTML. Styling is discarded; answer feedback is excluded."""
    def flatten(node):
        if node.tag in OMITTED_TAGS:
            return ''
        if node.tag not in PLAIN_TAGS and node is not element:
            raise ValueError(f'Текст содержит неподдерживаемый элемент <{node.tag}> (например изображение или формулу).')
        result = node.text or ''
        for child in node:
            result += flatten(child)
            if child.tag in ('p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'hr'):
                result += '\n'
            elif child.tag in ('td', 'th'):
                result += '\t'
            result += child.tail or ''
        return result
    return clean_text(flatten(element))


def extract_question(block):
    """Compatibility helper for a component containing exactly one supported question."""
    items, errors = extract_questions(block)
    if errors:
        raise ValueError(errors[0]['reason'])
    if len(items) != 1:
        raise ValueError('Компонент содержит несколько вопросов.')
    return items[0]


def extract_questions(block):
    """Extract basic responses, even inside HTML wrappers, reporting failures individually."""
    # Studio often stores HTML entities such as &nbsp;, which are not XML entities.
    def entity(match):
        name = match.group(1)
        if name in ('amp', 'lt', 'gt', 'quot', 'apos') or name + ';' not in html5:
            return match.group(0)
        return ''.join(f'&#{ord(char)};' for char in html5[name + ';'])
    try:
        data = re.sub(r'&([a-zA-Z][a-zA-Z0-9]+);', entity, block.data)
        root = fromstring(data, forbid_dtd=True)
    except (ET.ParseError, DefusedXmlException, TypeError) as error:
        raise ValueError('Некорректный или неподдерживаемый XML.') from error
    if root.tag != 'problem':
        raise ValueError('Не является компонентом Problem.')
    responses = [node for node in root.iter() if node.tag.endswith('response')]
    if not responses:
        raise ValueError('Не найден стандартный блок ответа Open edX.')
    if root.find('.//script') is not None:
        raise ValueError('Вопрос содержит скрипт; автоматически определять правильные ответы небезопасно.')
    media = next((node.tag for node in root.iter() if node.tag in
                  ('img', 'math', 'svg', 'iframe', 'audio', 'video', 'object', 'embed', 'canvas')), None)
    if media:
        raise ValueError(f'Компонент содержит <{media}>. Excel-таблица поддерживает только текст.')
    parents = {child: node for node in root.iter() for child in node}
    items, errors = [], []
    for index, response in enumerate(responses, 1):
        try:
            items.append(_extract_response(response, root, parents, block.display_name, index, len(responses)))
        except ValueError as error:
            errors.append({'number': index, 'reason': str(error)})
    return items, errors


def _extract_response(response, root, parents, title, index, total):
    kind = RESPONSE_TYPES.get(response.tag)
    free_answer = response.tag in ('stringresponse', 'numericalresponse')
    if kind is None and not free_answer:
        raise ValueError(f'Тип <{response.tag}> не поддерживается: доступны выбор, текстовый и числовой ответ.')
    prompt = _response_prompt(response, root, parents, total)
    name = title or prompt[:255]
    if total > 1:
        name = f'{name[:240]} — {index}'
    if not prompt:
        raise ValueError('Не найден текст вопроса.')
    if free_answer:
        if response.get('cfn'):
            raise ValueError('Ответ вычисляется пользовательским кодом.')
        answer = clean_text(response.get('answer', ''))
        if not answer:
            raise ValueError('Не найден правильный ответ.')
        if response.tag == 'numericalresponse':
            if response.findall('additional_answer'):
                raise ValueError('Несколько допустимых числовых ответов не поддерживаются этим форматом.')
            params = response.findall('responseparam')
            if any(param.get('type') != 'tolerance' for param in params) or len(params) > 1:
                raise ValueError('Числовой вопрос содержит дополнительные параметры.')
            # Match CAPA's default when no explicit responseparam is present.
            tolerance = params[0].get('default', '0.001%') if params else '0.001%'
            try:
                answer, tolerance = numeric_answer(f'{answer} ± {tolerance}')
            except ValueError as error:
                raise ValueError(f'{error} Исходный ответ: {answer!r}.') from error
            key = answer if tolerance == '0' else f'{answer} ± {tolerance}'
            return Question(name, prompt, 'numeric', [], [key])
        match_type = response.get('type', '').strip().lower()
        if match_type not in ('', 'ci', 'cs'):
            raise ValueError('Текстовый ответ с регулярным выражением не поддерживается.')
        alternatives = [clean_text(node.get('answer', '')) for node in response.findall('additional_answer')]
        if any(not value for value in alternatives):
            raise ValueError('Пустой дополнительный текстовый ответ.')
        if any(' || ' in value for value in [answer, *alternatives]):
            raise ValueError('Текст ответа содержит разделитель альтернатив " || "; требуется ручная проверка.')
        return Question(name, prompt, 'text' if match_type == 'ci' else 'text_case_sensitive', [], [answer, *alternatives])
    group_tag = {'single': 'choicegroup', 'multi': 'checkboxgroup', 'dropdown': 'optioninput'}[kind]
    groups = response.findall('.//' + group_tag)
    if len(groups) != 1:
        raise ValueError('Нужна одна группа вариантов ответа на вопрос.')
    group = groups[0]
    choice_tag = 'option' if kind == 'dropdown' else 'choice'
    choices = [node for node in group if node.tag == choice_tag]
    if not 2 <= len(choices) <= len(LETTERS):
        raise ValueError('Нужны от 2 до 10 вариантов ответа.')
    # Ignore hint/settings elements, but do not silently discard a second answer input.
    if any(node.tag.endswith('input') and node is not group for node in response.iter()):
        raise ValueError('Вопрос содержит дополнительное поле ответа.')
    answers, correct = [], []
    for letter, choice in zip(LETTERS, choices):
        value = choice.get('correct', '').strip().lower()
        if value not in ('true', 'false'):
            raise ValueError('Правильность ответа должна быть true/false; частичные или вычисляемые ответы не поддерживаются.')
        answers.append(_plain(choice))
        if value == 'true':
            correct.append(letter)
    if any(not answer for answer in answers) or not correct or (kind == 'dropdown' and len(correct) != 1):
        raise ValueError('Пустые варианты или неподдерживаемые правильные ответы.')
    return Question(name, prompt, kind, answers, correct)


def _response_prompt(response, root, parents, total):
    labels = response.findall('.//label')
    if len(labels) > 1:
        raise ValueError('Найдено несколько текстов вопроса.')
    if labels and _plain(labels[0]):
        prompt = _plain(labels[0])
    else:
        prompt = _prompt_without_groups(response)
        if not prompt:
            # Legacy OLX puts the question in paragraphs immediately before the response.
            parent = parents.get(response, root)
            preceding = []
            for child in parent:
                if child is response:
                    break
                if any(node.tag.endswith('response') for node in child.iter()):
                    preceding = []
                elif child.tag in PLAIN_TAGS:
                    preceding.append(_plain(child))
            prompt = '\n'.join(filter(None, preceding))
            if not prompt and total == 1:
                prompt = clean_text(parent.text or '')
    return prompt


def _prompt_without_groups(response):
    """Gather visible prompt text while excluding answer inputs and feedback."""
    def visible(node):
        if node.tag in OMITTED_TAGS or node.tag in ('choicegroup', 'checkboxgroup', 'optioninput', 'textline',
                                                  'formulaequationinput', 'responseparam', 'additional_answer'):
            return ''
        if node is not response and node.tag not in PLAIN_TAGS:
            raise ValueError(f'Неподдерживаемый элемент текста <{node.tag}>.')
        value = node.text or ''
        for child in node:
            value += visible(child) + (child.tail or '')
            if child.tag in ('p', 'div', 'br', 'label', 'description'):
                value += '\n'
        return value
    return clean_text(visible(response))


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
