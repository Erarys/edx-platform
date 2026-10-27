"""Tests for the Coursera OLX post-processor."""

import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from openedx.core.coursera_olx import prepare_coursera_olx


class CourseraOlxTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.course_dir = Path(self.temp_directory.name) / 'course'
        for directory in ('course', 'vertical', 'static', 'policies'):
            (self.course_dir / directory).mkdir(parents=True, exist_ok=True)
        (self.course_dir / 'course.xml').write_text(
            '<course url_name="run" org="Test" course="PDF"/>', encoding='utf-8'
        )
        (self.course_dir / 'course' / 'run.xml').write_text(
            '<course advanced_modules="[&quot;pdf&quot;, &quot;other&quot;]"/>', encoding='utf-8'
        )
        (self.course_dir / 'vertical' / 'unit.xml').write_text(
            '<vertical><pdf url_name="pdf-block" display_name="Lecture notes" '
            'href="https://example.test/asset-v1:Test+PDF+run+type@asset+block@lecture.pdf"/>'
            '</vertical>',
            encoding='utf-8',
        )
        (self.course_dir / 'static' / 'Lecture notes.pdf').write_bytes(b'%PDF-1.4\n')
        (self.course_dir / 'policies' / 'assets.json').write_text(
            json.dumps({'lecture.pdf': {'displayname': 'Lecture notes.pdf', 'import_path': None}}),
            encoding='utf-8',
        )


    def test_pdf_block_becomes_reading_with_bundled_asset(self):
        report = prepare_coursera_olx(self.course_dir)

        vertical = ElementTree.parse(self.course_dir / 'vertical' / 'unit.xml').getroot()
        self.assertEqual([(child.tag, child.get('url_name')) for child in vertical], [('html', 'pdf-block')])
        descriptor = ElementTree.parse(self.course_dir / 'html' / 'pdf-block.xml').getroot()
        self.assertEqual(descriptor.attrib, {'filename': 'pdf-block', 'display_name': 'Lecture notes'})
        reading = (self.course_dir / 'html' / 'pdf-block.html').read_text(encoding='utf-8')
        self.assertIn('/static/Lecture%20notes.pdf', reading)
        self.assertIn('Lecture notes (PDF)', reading)
        self.assertEqual(report.to_dict(), {
            'converted_pdf_blocks': 1,
            'local_pdf_assets': 1,
            'external_pdf_links': 0,
            'removed_pdf_advanced_modules': 1,
        })
        course = ElementTree.parse(self.course_dir / 'course' / 'run.xml').getroot()
        self.assertEqual(json.loads(course.get('advanced_modules')), ['other'])


    def test_conversion_is_idempotent(self):
        prepare_coursera_olx(self.course_dir)
        second_report = prepare_coursera_olx(self.course_dir)
        self.assertEqual(second_report.converted_pdf_blocks, 0)
        self.assertEqual(second_report.local_pdf_assets, 0)
        self.assertEqual(second_report.removed_pdf_advanced_modules, 0)


if __name__ == '__main__':
    unittest.main()
