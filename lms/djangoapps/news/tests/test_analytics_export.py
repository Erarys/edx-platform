"""Tests for the analytics Excel export."""

from datetime import datetime
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, override_settings

from lms.djangoapps.news.analytics_export import (
    build_analytics_workbook,
    get_report_period,
)


@override_settings(USE_TZ=True, TIME_ZONE="Asia/Qyzylorda")
class AnalyticsExportTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 6, 15, 30, tzinfo=ZoneInfo("Asia/Qyzylorda"))

    def test_calendar_period_boundaries(self):
        week = get_report_period("week", self.now)
        month = get_report_period("month", self.now)
        half_year = get_report_period("half_year", self.now)

        self.assertEqual(week.start, datetime(2026, 11, 2, tzinfo=self.now.tzinfo))
        self.assertEqual(month.start, datetime(2026, 11, 1, tzinfo=self.now.tzinfo))
        self.assertEqual(half_year.start, datetime(2026, 7, 1, tzinfo=self.now.tzinfo))
        self.assertEqual(week.end, self.now)

    def test_unknown_period_is_rejected(self):
        with self.assertRaises(ValueError):
            get_report_period("year", self.now)

    def test_workbook_matches_reference_table_and_contains_native_chart(self):
        period = get_report_period("week", self.now)
        rows = [
            {
                "course_id": "course-v1:KazNU+TEST+2026",
                "course_name": "Тестовый курс",
                "course_period": "01.11.2026 — 30.11.2026",
                "active_users": 25,
                "finished_students": 12,
                "report_period": period.spreadsheet_label,
            }
        ]

        workbook = build_analytics_workbook(rows, period)
        with ZipFile(BytesIO(workbook)) as archive:
            names = set(archive.namelist())
            self.assertIn("xl/worksheets/sheet1.xml", names)
            self.assertIn("xl/worksheets/sheet2.xml", names)
            self.assertIn("xl/charts/chart1.xml", names)
            self.assertNotIn("xl/charts/chart2.xml", names)
            for name in names:
                if name.endswith((".xml", ".rels")):
                    ElementTree.fromstring(archive.read(name))

            report_xml = archive.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("Тестовый курс", report_xml)
            self.assertIn("<v>25</v>", report_xml)
            self.assertIn("Зарегистрировано", report_xml)
            self.assertNotIn("Мужчины", report_xml)

    def test_empty_workbook_explains_that_no_data_is_available(self):
        period = get_report_period("month", self.now)
        workbook = build_analytics_workbook([], period)

        with ZipFile(BytesIO(workbook)) as archive:
            self.assertNotIn("xl/drawings/drawing1.xml", archive.namelist())
            report_xml = archive.read("xl/worksheets/sheet1.xml").decode()
            charts_xml = archive.read("xl/worksheets/sheet2.xml").decode()
            self.assertIn("За выбранный период данные отсутствуют.", report_xml)
            self.assertIn("Нет данных для построения графика.", charts_xml)
