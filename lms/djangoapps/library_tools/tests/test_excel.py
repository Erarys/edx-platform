"""Excel exchange: real OOXML cell types, row diagnostics, and archive boundaries."""

import io
import xml.etree.ElementTree as ET
from unittest import TestCase
from zipfile import ZIP_DEFLATED, ZipFile

from lms.djangoapps.library_tools import excel, questions


class QuestionExcelTests(TestCase):
    def example(self, **changes):
        values = dict(title='Example', question='Вопрос\nна двух строках?', type='single',
                      answers=['Да', 'Нет', 'Возможно', 'Не знаю'], correct_answers=['B'])
        return questions.Question(**{**values, **changes})

    def workbook(self, **changes):
        return excel.xlsx_bytes([(self.example(**changes), 'original-id')])

    def modify(self, content, cell_address, value=None, formula=None):
        output = io.BytesIO()
        with ZipFile(io.BytesIO(content)) as source, ZipFile(output, 'w', ZIP_DEFLATED) as target:
            for member in source.infolist():
                data = source.read(member.filename)
                if member.filename == 'xl/worksheets/sheet1.xml':
                    sheet = ET.fromstring(data)
                    cell = next(cell for cell in sheet.iter(excel._tag('c')) if cell.get('r') == cell_address)
                    if value is not None:
                        cell.find('.//' + excel._tag('t')).text = value
                    if formula is not None:
                        ET.SubElement(cell, excel._tag('f')).text = formula
                    data = ET.tostring(sheet)
                target.writestr(member.filename, data)
        return output.getvalue()

    def test_round_trip_all_types_and_multiline_text(self):
        for kind, correct in [('single', ['B']), ('multi', ['A', 'C']), ('dropdown', ['D'])]:
            result = excel.parse_xlsx(self.workbook(type=kind, correct_answers=correct))
            self.assertFalse(result.errors)
            self.assertEqual(result.questions[0].question, self.example().question)
            self.assertEqual(result.questions[0].answers, self.example().answers)
            self.assertEqual(result.questions[0].correct_answers, correct)
            self.assertEqual(result.questions[0].type, kind)

    def test_file_has_only_requested_columns_and_excel_controls(self):
        with ZipFile(io.BytesIO(self.workbook())) as archive:
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            header = [node.text for node in sheet.find(excel._tag('sheetData'))[0].iter(excel._tag('t'))]
            self.assertEqual(header, ['question', 'variant1', 'variant2', 'variant3', 'variant4', 'correct_answer', 'type'])
            self.assertIsNotNone(sheet.find(excel._tag('autoFilter')))
            self.assertIsNotNone(sheet.find(excel._tag('dataValidations')))
            self.assertIsNotNone(sheet.find('.//' + excel._tag('pane')))

    def test_602_question_bank_can_be_reimported(self):
        result = excel.parse_xlsx(excel.xlsx_bytes([(self.example(), '')] * 602))
        self.assertEqual(len(result.questions), 602)
        self.assertFalse(result.errors)

    def test_up_to_ten_options(self):
        result = excel.parse_xlsx(self.workbook(answers=[str(i) for i in range(10)], correct_answers=['J']))
        self.assertEqual(result.questions[0].correct_answers, ['J'])

    def test_formula_looking_text_is_literal(self):
        result = excel.parse_xlsx(self.workbook(question='=SUM(A1:A2)', answers=['+1', '-1'], correct_answers=['A']))
        self.assertEqual(result.questions[0].question, '=SUM(A1:A2)')
        self.assertEqual(result.questions[0].answers, ['+1', '-1'])

    def test_invisible_characters_are_cleaned_and_reported(self):
        content = self.modify(self.workbook(), 'A2', '  Во\u200bпрос\ufeff\u00a0?  ')
        result = excel.parse_xlsx(content)
        self.assertEqual(result.questions[0].question, 'Вопрос ?')
        self.assertEqual(result.warnings[0]['row'], 2)

    def test_empty_after_cleaning_is_error(self):
        result = excel.parse_xlsx(self.modify(self.workbook(), 'A2', '\u200b\ufeff'))
        self.assertEqual(result.errors[0]['row'], 2)
        self.assertFalse(result.questions)

    def test_errors_in_different_rows_are_all_reported(self):
        content = excel.xlsx_bytes([(self.example(), ''), (self.example(), '')])
        content = self.modify(content, 'F2', '9')
        content = self.modify(content, 'G3', 'unknown')
        result = excel.parse_xlsx(content)
        self.assertEqual([item['row'] for item in result.errors], [2, 3])

    def test_formula_cells_are_errors(self):
        result = excel.parse_xlsx(self.modify(self.workbook(), 'B2', formula='1+1'))
        self.assertIn('формулы', result.errors[0]['detail'])

    def test_headers_and_type_aliases(self):
        content = self.modify(self.workbook(), 'B1', 'varian1')
        content = self.modify(content, 'G1', 'type_задачи')
        content = self.modify(content, 'G2', 'single select')
        self.assertFalse(excel.parse_xlsx(content).errors)

    def test_invalid_headers_rejected(self):
        for address, value in [('A1', 'other'), ('B1', 'question')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                excel.parse_xlsx(self.modify(self.workbook(), address, value))

    def test_unsupported_report_does_not_enter_import(self):
        content = excel.xlsx_bytes([(self.example(), '')], [{'title': 'Broken', 'id': 'old', 'reason': 'Invalid XML'}])
        result = excel.parse_xlsx(content)
        self.assertEqual(len(result.questions), 1)

    def test_broken_and_oversized_files_rejected(self):
        for content in [b'not an excel', b'x' * (excel.MAX_UPLOAD_BYTES + 1)]:
            with self.subTest(size=len(content)), self.assertRaises(ValueError):
                excel.parse_xlsx(content)

    def test_text_numeric_and_tolerance_round_trip(self):
        for kind, correct in [('text', ['Алматы', 'Almaty']), ('text_case_sensitive', ['ABC']),
                              ('numeric', ['4 ± 5']), ('numeric', ['3.14 ± 0.01']), ('numeric', ['5 ± 10%'])]:
            question = self.example(type=kind, answers=[], correct_answers=correct)
            content = excel.xlsx_bytes([(question, '')])
            result = excel.parse_xlsx(content)
            self.assertFalse(result.errors)
            self.assertEqual(result.questions[0].correct_answers, correct)
            block = type('Block', (), {'data': questions.question_xml(result.questions[0]), 'display_name': question.title})()
            extracted = questions.extract_question(block)
            self.assertEqual(extracted.type, kind)
            self.assertEqual(extracted.correct_answers, correct)

    def test_invalid_numeric_answers_are_diagnosed(self):
        for value in ['nan', 'inf', '$answer', '600-900', '0,4', '3 ± -1', '3 ± wrong']:
            content = self.workbook(type='numeric', answers=[], correct_answers=[value])
            with self.subTest(value=value):
                self.assertEqual(excel.parse_xlsx(content).errors[0]['row'], 2)

    def test_free_answer_cannot_have_choice_variants(self):
        content = self.workbook(type='text', correct_answers=['answer'])
        self.assertIn('оставьте', excel.parse_xlsx(content).errors[0]['detail'])
