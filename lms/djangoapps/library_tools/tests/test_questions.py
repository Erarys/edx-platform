"""CSV/OLX round trips, malformed uploads, and unsafe/unsupported content."""

from types import SimpleNamespace
from unittest import TestCase
from pathlib import Path
from textwrap import dedent
import xml.etree.ElementTree as ET

from lms.djangoapps.library_tools import questions


class QuestionCSVTests(TestCase):
    """Only fully validated literal-text questions become new Problem components."""

    def example(self, **changes):
        values = dict(title='Тест', question='Вопрос\nна второй строке?', type='multi',
                      answers=['Казахстан; да', 'Нет, спасибо', 'Третий'], correct_answers=['A', 'C'])
        return questions.Question(**{**values, **changes})

    def csv(self, **changes):
        return questions.csv_bytes([(self.example(**changes), 'old-id')])

    def test_csv_round_trip_multiline_unicode_and_answer_keys(self):
        actual = questions.parse_csv(self.csv())[0]
        self.assertEqual(actual.question, self.example().question)
        self.assertEqual(actual.answers, self.example().answers)
        self.assertEqual(actual.correct_answers, ['A', 'C'])
        self.assertNotIn('source_id', actual.to_dict())

    def test_formula_looking_cells_round_trip_as_literal_text(self):
        content = self.csv(title='=1+1', question='@вопрос', answers=['+ответ', '-ответ'], correct_answers=['A'])
        self.assertIn("'=1+1".encode(), content)
        actual = questions.parse_csv(content)[0]
        self.assertEqual((actual.title, actual.question, actual.answers), ('=1+1', '@вопрос', ['+ответ', '-ответ']))

    def test_english_headers_and_comma_delimiter(self):
        actual = questions.parse_csv(b'question,answer_a,answer_b,correct_answers\nQ,one,two,B\n')[0]
        self.assertEqual(actual.type, 'single')
        self.assertEqual(actual.correct_answers, ['B'])

    def test_tab_delimiter(self):
        actual = questions.parse_csv(b'question\tanswer_a\tanswer_b\tcorrect_answers\nQ\tone\ttwo\tB\n')[0]
        self.assertEqual(actual.answers, ['one', 'two'])

    def test_late_invalid_row_rejects_whole_file(self):
        content = b'question,answer_a,answer_b,correct_answers\nQ,one,two,A\nBad,one,two,J\n'
        with self.assertRaisesRegex(ValueError, 'Строка 3'):
            questions.parse_csv(content)

    def test_invalid_headers_duplicate_headers_and_encoding(self):
        for content in [b'', b'question,question\nq,q', b'foo,bar\nq,q', b'\xff\xfe']:
            with self.subTest(content=content), self.assertRaises(ValueError):
                questions.parse_csv(content)

    def test_empty_question_or_missing_option_rejected(self):
        for text in ['Q,,two,A', 'Q,one,,A', ',one,two,A', 'Q,one,two,Z']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                questions.parse_csv(('question,answer_a,answer_b,correct_answers\n'+text).encode())

    def test_dropdown_type_rejects_multiple_answers(self):
        with self.assertRaises(ValueError):
            questions.parse_csv(self.csv(type='dropdown'))

    def test_single_type_allows_alternative_correct_choices(self):
        parsed = questions.parse_csv(self.csv(type='single'))[0]
        result = questions.extract_question(SimpleNamespace(data=questions.question_xml(parsed), display_name=parsed.title))
        self.assertEqual(result.correct_answers, ['A', 'C'])
        self.assertEqual(result.type, 'single')

    def test_limits_and_control_characters(self):
        content = b'question,answer_a,answer_b,correct_answers\n' + b'Q,one,two,A\n' * (questions.MAX_QUESTIONS + 1)
        for value in [content, b'x'*(questions.MAX_UPLOAD_BYTES+1), self.csv(question='bad\x00text')]:
            with self.subTest(size=len(value)), self.assertRaises(ValueError):
                questions.parse_csv(value)

    def test_literal_html_is_escaped_in_generated_problem(self):
        item = self.example(question='<script>print(1)</script>', answers=['<img src=x>', 'A&B'])
        xml = questions.question_xml(item)
        root = ET.fromstring(xml)
        self.assertIsNone(root.find('.//script'))
        self.assertIsNone(root.find('.//img'))
        self.assertEqual(root.find('.//label').text, item.question)

    def test_single_and_multi_xml_round_trip(self):
        for item in [self.example(), self.example(type='single', correct_answers=['B']),
                     self.example(type='dropdown', correct_answers=['B'])]:
            with self.subTest(type=item.type):
                block = SimpleNamespace(data=questions.question_xml(item), display_name=item.title)
                exported = questions.extract_question(block)
                self.assertEqual(exported, item)

    def test_legacy_paragraph_prompt(self):
        block = SimpleNamespace(display_name='Legacy', data='<problem><p>Вопрос?</p><multiplechoiceresponse><choicegroup type="MultipleChoice"><choice correct="true">Да</choice><choice correct="false">Нет</choice></choicegroup></multiplechoiceresponse></problem>')
        self.assertEqual(questions.extract_question(block).question, 'Вопрос?')

    def test_complex_content_is_not_silently_flattened(self):
        xml = questions.question_xml(self.example())
        for content in [xml.replace('<label>', '<label><img src="a"/>'),
                        xml.replace('</problem>', '<script>print(1)</script></problem>'),
                        xml.replace('correct="true"', 'correct="partial"')]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                questions.extract_question(SimpleNamespace(data=content, display_name='Complex'))

    def test_standard_studio_templates_including_hints(self):
        directory = Path(__file__).resolve().parents[4] / 'xmodule/templates/problem'
        for filename, kind in [('multiplechoice.yaml', 'single'), ('multiplechoice_hint.yaml', 'single'),
                               ('checkboxes_response.yaml', 'multi'), ('checkboxes_response_hint.yaml', 'multi'),
                               ('optionresponse.yaml', 'dropdown'), ('optionresponse_hint.yaml', 'dropdown')]:
            source = (directory / filename).read_text(encoding='utf-8')
            xml = dedent(source.split('\ndata:', 1)[1].split('\n', 1)[1])
            with self.subTest(filename=filename):
                result = questions.extract_question(SimpleNamespace(data=xml, display_name='Template'))
                self.assertEqual(result.type, kind)
                self.assertEqual(len(result.answers), 4 if kind == 'multi' else 3)
                self.assertNotIn('feedback', '\n'.join(result.answers))

    def test_styled_html_entities_and_shuffle_are_plain_text(self):
        xml = '<problem><div class="wrapper"><p style="color:red"><strong>Вопрос&nbsp;?</strong></p><multiplechoiceresponse><choicegroup type="MultipleChoice" shuffle="true"><choice correct="true"><span style="font-size:12px">Да</span><choicehint>Скрытая подсказка</choicehint></choice><choice correct="false">Нет</choice></choicegroup></multiplechoiceresponse></div><solution>Решение</solution></problem>'
        result = questions.extract_question(SimpleNamespace(data=xml, display_name='Styled'))
        self.assertEqual(result.question, 'Вопрос ?')
        self.assertEqual(result.answers, ['Да', 'Нет'])

    def test_multiple_questions_are_separate_rows_and_errors_are_individual(self):
        xml = questions.question_xml(self.example(type='single', correct_answers=['A']))
        response = xml[len('<problem>'):-len('</problem>')]
        items, errors = questions.extract_questions(SimpleNamespace(
            data=f'<problem>{response}<numericalresponse answer="3"/>{response}</problem>', display_name='Bank'))
        self.assertEqual(len(items), 2)
        self.assertEqual(errors[0]['number'], 2)
        self.assertTrue(items[1].title.endswith('3'))

    def test_numeric_default_tolerance_matches_capa(self):
        block = SimpleNamespace(display_name='Numeric', data='<problem><p>Сколько?</p><numericalresponse answer="4"><textline/></numericalresponse></problem>')
        self.assertEqual(questions.extract_question(block).correct_answers, ['4 ± 0.001%'])

    def test_xml_entities_and_invalid_xml_are_rejected(self):
        for content in ['<!DOCTYPE problem [<!ENTITY x "xx">]><problem>&x;</problem>', '<problem>']:
            with self.subTest(content=content), self.assertRaises(ValueError):
                questions.extract_question(SimpleNamespace(data=content, display_name='Unsafe'))
