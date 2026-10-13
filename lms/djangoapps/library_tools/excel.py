"""Small, bounded OOXML exchange for question banks without extra LMS dependencies."""

import io
import posixpath
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException

from . import questions

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
TYPE_ALIASES = {
    'single': 'single', 'single_select': 'single', 'single_choice': 'single',
    'multiple_choice': 'single', 'multiplechoiceresponse': 'single', 'radio': 'single',
    'multi': 'multi', 'multiple_select': 'multi', 'checkbox': 'multi', 'checkboxes': 'multi',
    'choiceresponse': 'multi', 'dropdown': 'dropdown', 'select': 'dropdown', 'optionresponse': 'dropdown',
    'text': 'text', 'string': 'text', 'stringresponse': 'text', 'text_case_sensitive': 'text_case_sensitive',
    'numeric': 'numeric', 'number': 'numeric', 'numericalresponse': 'numeric',
}
CELL_REF = re.compile(r'([A-Z]{1,2})([1-9][0-9]*)\Z')


@dataclass
class Upload:
    """Validation is complete before any new components can be written."""
    questions: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _tag(name):
    return f'{{{NS}}}{name}'


def _column(number):
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xml(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _sheet(name, rows, widths, question_sheet=False):
    root = ET.Element('worksheet', xmlns=NS)
    views = ET.SubElement(root, 'sheetViews')
    view = ET.SubElement(views, 'sheetView', workbookViewId='0', showGridLines='0')
    ET.SubElement(view, 'pane', ySplit='1', topLeftCell='A2', activePane='bottomLeft', state='frozen')
    columns = ET.SubElement(root, 'cols')
    for index, width in enumerate(widths, 1):
        ET.SubElement(columns, 'col', min=str(index), max=str(index), width=str(width), customWidth='1')
    data = ET.SubElement(root, 'sheetData')
    for number, values in enumerate(rows, 1):
        # Wrap long questions instead of clipping them. Excel can resize these rows further.
        lines = max((sum(max(1, (len(line) + int(widths[i]) - 1) // int(widths[i]))
                     for line in str(value).split('\n')) for i, value in enumerate(values)), default=1)
        row = ET.SubElement(data, 'row', r=str(number), ht=str(32 if number == 1 else min(409, max(40, lines * 16 + 12))), customHeight='1')
        for index, value in enumerate(values, 1):
            cell = ET.SubElement(row, 'c', r=f'{_column(index)}{number}', t='inlineStr', s='1' if number == 1 else ('2' if number % 2 == 0 else '3'))
            inline = ET.SubElement(cell, 'is')
            ET.SubElement(inline, 't', {'xml:space': 'preserve'}).text = questions.clean_text(str(value))
    last_column = _column(len(widths))
    ET.SubElement(root, 'autoFilter', ref=f'A1:{last_column}{max(1, len(rows))}')
    if question_sheet:
        validations = ET.SubElement(root, 'dataValidations', count='1')
        validation = ET.SubElement(validations, 'dataValidation', type='list', allowBlank='0', showErrorMessage='1',
                                   errorTitle='Тип вопроса', error='Выберите тип вопроса из списка.',
                                   sqref=f'{last_column}2:{last_column}{max(len(rows), questions.MAX_QUESTIONS + 1)}')
        ET.SubElement(validation, 'formula1').text = '"single,multi,dropdown,text,text_case_sensitive,numeric"'
    return name, _xml(root)


def xlsx_bytes(rows, unsupported=()):
    """One row per question. All cells are literal strings, including '=...' answers."""
    rows = list(rows)
    option_count = max(4, max((len(question.answers) for question, _ in rows), default=4))
    headers = ['question', *(f'variant{i}' for i in range(1, option_count + 1)), 'correct_answer', 'type']
    values = [headers]
    for question, _ in rows:
        if question.type == 'numeric':
            correct = question.correct_answers[0]
        elif question.type in ('text', 'text_case_sensitive'):
            correct = ' || '.join(question.correct_answers)
        else:
            correct = ','.join(str(questions.LETTERS.index(letter) + 1) for letter in question.correct_answers)
        values.append([question.question, *question.answers, *([''] * (option_count - len(question.answers))), correct, question.type])
    sheets = [_sheet('Questions', values, [60, *([28] * option_count), 22, 18], question_sheet=True)]
    if unsupported:
        sheets.append(_sheet('Не выгружены', [['Название', 'ID', 'Причина'],
                                               *[[item['title'], item['id'], item['reason']] for item in unsupported]], [45, 55, 80]))
    workbook = ET.Element('workbook', xmlns=NS, attrib={'xmlns:r': REL_NS})
    sheet_list = ET.SubElement(workbook, 'sheets')
    relationships = ET.Element('Relationships', xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
    content = ET.Element('Types', xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
    ET.SubElement(content, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    ET.SubElement(content, 'Default', Extension='xml', ContentType='application/xml')
    ET.SubElement(content, 'Override', PartName='/xl/workbook.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml')
    ET.SubElement(content, 'Override', PartName='/xl/styles.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml')
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        for index, (name, sheet_data) in enumerate(sheets, 1):
            ET.SubElement(sheet_list, 'sheet', name=name, sheetId=str(index), attrib={'r:id': f'rId{index}'})
            ET.SubElement(relationships, 'Relationship', Id=f'rId{index}', Type=REL_NS + '/worksheet', Target=f'worksheets/sheet{index}.xml')
            ET.SubElement(content, 'Override', PartName=f'/xl/worksheets/sheet{index}.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
            archive.writestr(f'xl/worksheets/sheet{index}.xml', sheet_data)
        ET.SubElement(relationships, 'Relationship', Id='styles', Type=REL_NS + '/styles', Target='styles.xml')
        archive.writestr('xl/workbook.xml', _xml(workbook))
        archive.writestr('xl/_rels/workbook.xml.rels', _xml(relationships))
        archive.writestr('[Content_Types].xml', _xml(content))
        archive.writestr('_rels/.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{REL_NS}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/styles.xml', STYLES)
    return buffer.getvalue()


def _read_xml(archive, name):
    return fromstring(archive.read(name), forbid_dtd=True)


def _cell_value(cell, strings):
    if cell.find(_tag('f')) is not None:
        raise ValueError('формулы не допускаются; вставьте значение как обычный текст')
    kind = cell.get('t', 'n')
    if kind == 'inlineStr':
        return ''.join(node.text or '' for node in cell.findall('.//' + _tag('t')))
    value = cell.findtext(_tag('v'), '')
    if kind == 's':
        try:
            index = int(value)
            if index < 0:
                raise ValueError()
            return strings[index]
        except (ValueError, IndexError) as error:
            raise ValueError('повреждён текст ячейки') from error
    if kind not in ('n', 'str'):
        raise ValueError('нужен текст или число, а не ошибка, дата или логическое значение Excel')
    return value


def _header(value):
    key = questions.clean_text(value).lower().replace(' ', '_')
    aliases = {'вопрос': 'question', 'type_задачи': 'type', 'тип': 'type', 'тип_задачи': 'type',
               'правильный_ответ': 'correct_answer', 'correct_answers': 'correct_answer',
               'правильные_варианты': 'correct_answer'}
    key = aliases.get(key, key)
    match = re.fullmatch(r'(?:variant|varian|вариант)_?([1-9]|10)', key)
    return f'variant{match[1]}' if match else key


def _question(row, row_number):
    prompt = row['question']
    if not prompt:
        raise ValueError('не заполнен текст вопроса')
    if any(len(value) > questions.MAX_FIELD_LENGTH or questions.INVALID_XML_CHAR.search(value) for value in row.values()):
        raise ValueError('текст превышает 10000 символов или содержит недопустимые символы')
    kind_key = row['type'].casefold().replace('-', '_').replace(' ', '_')
    kind = TYPE_ALIASES.get(kind_key)
    if not kind:
        raise ValueError('type: укажите single, multi, dropdown, text, text_case_sensitive или numeric')
    if kind in ('text', 'text_case_sensitive', 'numeric'):
        if any(row.get(f'variant{i}', '') for i in range(1, 11)):
            raise ValueError('для текстового/числового ответа оставьте столбцы variant пустыми')
        value = row['correct_answer']
        if not value:
            raise ValueError('не заполнен правильный ответ')
        if kind == 'numeric':
            questions.numeric_answer(value)
            correct = [value]
        else:
            correct = [part.strip() for part in value.split(' || ')]
            if any(not answer for answer in correct):
                raise ValueError('пустой допустимый текстовый ответ')
        return questions.Question(prompt[:255], prompt, kind, [], correct, row_number)
    answers = [row.get(f'variant{i}', '') for i in range(1, 11)]
    while answers and not answers[-1]:
        answers.pop()
    if not prompt or len(answers) < 2 or any(not answer for answer in answers):
        raise ValueError('нужны вопрос и минимум два варианта подряд без пустых ячеек между ними')
    answer_key = row['correct_answer'].upper()
    tokens = re.split(r'[,;\s]+', answer_key)
    correct = []
    for token in tokens:
        if token.isascii() and token.isdigit() and 1 <= int(token) <= len(answers):
            letter = questions.LETTERS[int(token) - 1]
        elif token in questions.LETTERS[:len(answers)] and len(token) == 1:
            letter = token
        else:
            raise ValueError('correct_answer: укажите номер варианта (2) или несколько номеров (1,3)')
        if letter not in correct:
            correct.append(letter)
    if kind == 'dropdown' and len(correct) != 1:
        raise ValueError('dropdown требует ровно один правильный ответ')
    return questions.Question(prompt[:255], prompt, kind, answers, correct, row_number)


def parse_xlsx(content):
    """Read ordinary Excel files (inline/shared strings), collect all row errors, reject formulas."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError('Файл Excel превышает 5 МБ.')
    result = Upload()
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 512 or sum(item.file_size for item in members) > MAX_EXPANDED_BYTES:
                raise ValueError('Распакованный Excel слишком большой. Разделите файл на части.')
            if any(item.flag_bits & 1 for item in members):
                raise ValueError('Файлы Excel с паролем не поддерживаются.')
            workbook = _read_xml(archive, 'xl/workbook.xml')
            sheets = workbook.findall(_tag('sheets') + '/' + _tag('sheet'))
            if not sheets:
                raise ValueError('В файле нет листов.')
            selected = next((sheet for sheet in sheets if sheet.get('name') == 'Questions'), sheets[0])
            relationship = selected.get(f'{{{REL_NS}}}id')
            rels = _read_xml(archive, 'xl/_rels/workbook.xml.rels')
            target = next((rel for rel in rels if rel.get('Id') == relationship), None)
            if target is None or target.get('TargetMode') == 'External':
                raise ValueError('Повреждена ссылка на лист Excel.')
            path = posixpath.normpath(posixpath.join('xl', target.get('Target', ''))) if not target.get('Target', '').startswith('/') else target.get('Target').lstrip('/')
            if not path.startswith('xl/worksheets/'):
                raise ValueError('Некорректный путь листа Excel.')
            strings = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                strings = [''.join(node.text or '' for node in item.iter(_tag('t')))
                           for item in _read_xml(archive, 'xl/sharedStrings.xml')]
            sheet = _read_xml(archive, path)
            if sheet.find(_tag('mergeCells')) is not None:
                raise ValueError('Уберите объединённые ячейки: одна строка должна содержать один вопрос.')
            raw_rows = sheet.findall(_tag('sheetData') + '/' + _tag('row'))
            headers = None
            seen_rows = set()
            for raw_row in raw_rows:
                row_number = int(raw_row.get('r', '0'))
                if row_number < 1 or row_number in seen_rows:
                    raise ValueError('Повреждены номера строк Excel.')
                seen_rows.add(row_number)
                cells = {}
                errors = []
                for cell in raw_row.findall(_tag('c')):
                    address = cell.get('r', '')
                    match = CELL_REF.fullmatch(address)
                    if match is None or int(match[2]) != row_number:
                        raise ValueError('Повреждены координаты ячеек Excel.')
                    column = 0
                    for char in match[1]:
                        column = column * 26 + ord(char) - 64
                    if column > 32 or column in cells:
                        raise ValueError('Слишком много или повторяющиеся столбцы в Excel.')
                    try:
                        value = _cell_value(cell, strings)
                        normalized = questions.clean_text(value)
                        if value != normalized:
                            result.warnings.append({'row': row_number, 'detail': f'{address}: убраны невидимые символы, лишние пробелы или нормализован текст.'})
                        cells[column] = normalized
                    except ValueError as error:
                        cells[column] = ''
                        errors.append(f'{address}: {error}')
                if not any(cells.values()) and not errors:
                    continue
                if headers is None:
                    if errors:
                        raise ValueError('; '.join(errors))
                    headers = {column: _header(value) for column, value in cells.items() if value}
                    allowed = {'question', 'correct_answer', 'type', *(f'variant{i}' for i in range(1, 11))}
                    if len(set(headers.values())) != len(headers) or not set(headers.values()) <= allowed:
                        raise ValueError('Неизвестные или повторные заголовки. Скачайте шаблон Excel.')
                    if not {'question', 'variant1', 'variant2', 'correct_answer', 'type'} <= set(headers.values()):
                        raise ValueError('Нужны столбцы question, variant1, variant2, correct_answer, type.')
                    continue
                if len(result.questions) + len(result.errors) >= questions.MAX_QUESTIONS:
                    raise ValueError(f'За одну загрузку можно добавить до {questions.MAX_QUESTIONS} вопросов.')
                if any(value and column not in headers for column, value in cells.items()):
                    errors.append('данные находятся в столбце без заголовка')
                try:
                    if errors:
                        raise ValueError('; '.join(errors))
                    result.questions.append(_question({name: cells.get(column, '') for column, name in headers.items()}, row_number))
                except ValueError as error:
                    result.errors.append({'row': row_number, 'detail': str(error)})
    except (BadZipFile, KeyError, ET.ParseError, DefusedXmlException, RuntimeError, OSError) as error:
        raise ValueError('Повреждённый файл или неподдерживаемый формат. Нужен обычный .xlsx без пароля.') from error
    if not result.questions and not result.errors:
        raise ValueError('В файле нет вопросов.')
    return result


STYLES = '''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><color rgb="FF243447"/><name val="Arial"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Arial"/></font></fonts>
<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF245D8C"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF3F6FA"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="49" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="49" fontId="0" fillId="3" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
