"""CSV/OLX round trips, malformed uploads, and unsafe/unsupported content."""

from types import SimpleNamespace
from unittest import TestCase
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

    def test_single_type_rejects_multiple_answers(self):
        with self.assertRaises(ValueError):
            questions.parse_csv(self.csv(type='single'))

    def test_limits_and_control_characters(self):
        content = b'question,answer_a,answer_b,correct_answers\n' + b'Q,one,two,A\n' * 501
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
        for item in [self.example(), self.example(type='single', correct_answers=['B'])]:
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
                        xml.replace('<checkboxgroup>', '<checkboxgroup shuffle="true">'),
                        xml.replace('</problem>', '<script>print(1)</script></problem>'),
                        xml.replace('correct="true"', 'correct="partial"'),
                        xml.replace('</choice>', '<choicehint>Hint</choicehint></choice>')]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                questions.extract_question(SimpleNamespace(data=content, display_name='Complex'))

    def test_xml_entities_and_invalid_xml_are_rejected(self):
        for content in ['<!DOCTYPE problem [<!ENTITY x "xx">]><problem>&x;</problem>', '<problem>']:
            with self.subTest(content=content), self.assertRaises(ValueError):
                questions.extract_question(SimpleNamespace(data=content, display_name='Unsafe'))
