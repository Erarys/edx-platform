"""
Grade book view for instructor and pagination work (for grade book)
which is currently use by ccx and instructor apps.
"""


import io
import math
import re
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.db import transaction
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.instructor.views.api import require_course_permission
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order

from .. import permissions

# Grade book: max students per page
MAX_STUDENTS_PER_PAGE_GRADE_BOOK = 20

XLSX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
XLSX_NAMESPACE = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
XLSX_RELATIONSHIP_NAMESPACE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
INVALID_XML_CHARACTERS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
INVALID_SHEET_NAME_CHARACTERS = re.compile(r'[\[\]:*?/\\]')
GRADEBOOK_COLUMN_WIDTHS = (8, 29, 34, 25)
GRADEBOOK_XLSX_STYLES = b'''<?xml version="1.0" encoding="utf-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1"><numFmt numFmtId="164" formatCode="0.0%"/></numFmts>
  <fonts count="2">
    <font><sz val="10"/><color rgb="FF1F2937"/><name val="Arial"/></font>
    <font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Arial"/></font>
  </fonts>
  <fills count="4">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill>
      <patternFill patternType="solid"><fgColor rgb="FF17365D"/><bgColor indexed="64"/></patternFill>
    </fill>
    <fill>
      <patternFill patternType="solid"><fgColor rgb="FFC6E6F5"/><bgColor indexed="64"/></patternFill>
    </fill>
  </fills>
  <borders count="3">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FFFFFFFF"/></left>
      <right style="thin"><color rgb="FFFFFFFF"/></right>
      <top style="thin"><color rgb="FFFFFFFF"/></top>
      <bottom style="thin"><color rgb="FFFFFFFF"/></bottom>
      <diagonal/>
    </border>
    <border>
      <left style="thin"><color rgb="FF808080"/></left>
      <right style="thin"><color rgb="FF808080"/></right>
      <top style="thin"><color rgb="FF808080"/></top>
      <bottom style="thin"><color rgb="FF808080"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="6">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0"
        applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">
      <alignment horizontal="center" vertical="center" wrapText="1"/>
    </xf>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="2" xfId="0"
        applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">
      <alignment horizontal="center" vertical="center" wrapText="1"/>
    </xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="2" xfId="0"
        applyFont="1" applyBorder="1" applyAlignment="1">
      <alignment horizontal="center" vertical="center" wrapText="1"/>
    </xf>
    <xf numFmtId="164" fontId="0" fillId="3" borderId="2" xfId="0"
        applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">
      <alignment horizontal="center" vertical="center"/>
    </xf>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="2" xfId="0"
        applyNumberFormat="1" applyFont="1" applyBorder="1" applyAlignment="1">
      <alignment horizontal="center" vertical="center"/>
    </xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def _xlsx_column(number):
    """Return an Excel column label for a one-based column number."""
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xlsx_text(value):
    """Return text that is safe to include in an OOXML inline string."""
    return INVALID_XML_CHARACTERS.sub('', str(value or ''))


def _xlsx_xml(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _xlsx_sheet_name(value):
    """Return a valid Excel worksheet name."""
    name = INVALID_SHEET_NAME_CHARACTERS.sub('_', _xlsx_text(value)).strip("'")
    return (name or 'Grades')[:31]


def _gradebook_sheet(headers, rows):
    """Build the worksheet XML used by the gradebook export."""
    worksheet = ET.Element('worksheet', xmlns=XLSX_NAMESPACE)
    ET.SubElement(worksheet, 'dimension', ref=f'A1:D{max(1, len(rows) + 1)}')
    sheet_views = ET.SubElement(worksheet, 'sheetViews')
    sheet_view = ET.SubElement(sheet_views, 'sheetView', workbookViewId='0', showGridLines='0')
    ET.SubElement(
        sheet_view,
        'pane',
        xSplit='1',
        ySplit='1',
        topLeftCell='B2',
        activePane='bottomRight',
        state='frozen',
    )
    columns = ET.SubElement(worksheet, 'cols')
    for index, width in enumerate(GRADEBOOK_COLUMN_WIDTHS, 1):
        ET.SubElement(columns, 'col', min=str(index), max=str(index), width=str(width), customWidth='1')

    sheet_data = ET.SubElement(worksheet, 'sheetData')
    all_rows = [headers, *rows]
    for row_number, values in enumerate(all_rows, 1):
        line_count = max(
            (max(1, (len(_xlsx_text(value)) + width - 1) // width)
             for value, width in zip(values, GRADEBOOK_COLUMN_WIDTHS)),
            default=1,
        )
        height = 28 if row_number == 1 else max(20, min(60, line_count * 15))
        row = ET.SubElement(sheet_data, 'row', r=str(row_number), ht=str(height), customHeight='1')
        for column_number, value in enumerate(values, 1):
            address = f'{_xlsx_column(column_number)}{row_number}'
            if row_number == 1:
                style = '1'
            elif column_number == 4:
                style = '4' if row_number % 2 == 0 else '5'
            else:
                style = '2' if row_number % 2 == 0 else '3'

            if isinstance(value, (int, float)):
                cell = ET.SubElement(row, 'c', r=address, s=style)
                ET.SubElement(cell, 'v').text = str(value)
            else:
                cell = ET.SubElement(row, 'c', r=address, s=style, t='inlineStr')
                inline = ET.SubElement(cell, 'is')
                ET.SubElement(inline, 't', {'xml:space': 'preserve'}).text = _xlsx_text(value)

    ET.SubElement(worksheet, 'autoFilter', ref=f'A1:D{max(1, len(all_rows))}')
    return _xlsx_xml(worksheet)


def _gradebook_xlsx(headers, rows, sheet_name='Grades'):
    """Create a compact XLSX workbook containing the supplied gradebook rows."""
    workbook = ET.Element('workbook', xmlns=XLSX_NAMESPACE, attrib={'xmlns:r': XLSX_RELATIONSHIP_NAMESPACE})
    sheets = ET.SubElement(workbook, 'sheets')
    ET.SubElement(sheets, 'sheet', name=_xlsx_sheet_name(sheet_name), sheetId='1', attrib={'r:id': 'rId1'})

    relationships = ET.Element(
        'Relationships',
        xmlns='http://schemas.openxmlformats.org/package/2006/relationships',
    )
    ET.SubElement(
        relationships,
        'Relationship',
        Id='rId1',
        Type=XLSX_RELATIONSHIP_NAMESPACE + '/worksheet',
        Target='worksheets/sheet1.xml',
    )
    ET.SubElement(
        relationships,
        'Relationship',
        Id='styles',
        Type=XLSX_RELATIONSHIP_NAMESPACE + '/styles',
        Target='styles.xml',
    )

    content_types = ET.Element(
        'Types',
        xmlns='http://schemas.openxmlformats.org/package/2006/content-types',
    )
    ET.SubElement(
        content_types,
        'Default',
        Extension='rels',
        ContentType='application/vnd.openxmlformats-package.relationships+xml',
    )
    ET.SubElement(content_types, 'Default', Extension='xml', ContentType='application/xml')
    ET.SubElement(
        content_types,
        'Override',
        PartName='/xl/workbook.xml',
        ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml',
    )
    ET.SubElement(
        content_types,
        'Override',
        PartName='/xl/styles.xml',
        ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml',
    )
    ET.SubElement(
        content_types,
        'Override',
        PartName='/xl/worksheets/sheet1.xml',
        ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml',
    )

    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('xl/worksheets/sheet1.xml', _gradebook_sheet(headers, rows))
        archive.writestr('xl/workbook.xml', _xlsx_xml(workbook))
        archive.writestr('xl/_rels/workbook.xml.rels', _xlsx_xml(relationships))
        archive.writestr('[Content_Types].xml', _xlsx_xml(content_types))
        archive.writestr(
            '_rels/.rels',
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{XLSX_RELATIONSHIP_NAMESPACE}/officeDocument" '
            f'Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr('xl/styles.xml', GRADEBOOK_XLSX_STYLES)
    return output.getvalue()


def calculate_page_info(offset, total_students):
    """
    Takes care of sanitizing the offset of current page also calculates offsets for next and previous page
    and information like total number of pages and current page number.

    :param offset: offset for database query
    :return: tuple consist of page number, query offset for next and previous pages and valid offset
    """

    # validate offset.
    if not (isinstance(offset, int) or offset.isdigit()) or int(offset) < 0 or int(offset) >= total_students:
        offset = 0
    else:
        offset = int(offset)

    # calculate offsets for next and previous pages.
    next_offset = offset + MAX_STUDENTS_PER_PAGE_GRADE_BOOK
    previous_offset = offset - MAX_STUDENTS_PER_PAGE_GRADE_BOOK

    # calculate current page number.
    page_num = ((offset / MAX_STUDENTS_PER_PAGE_GRADE_BOOK) + 1)

    # calculate total number of pages.
    total_pages = int(math.ceil(float(total_students) / MAX_STUDENTS_PER_PAGE_GRADE_BOOK)) or 1

    if previous_offset < 0 or offset == 0:
        # We are at first page, so there's no previous page.
        previous_offset = None

    if next_offset >= total_students:
        # We've reached the last page, so there's no next page.
        next_offset = None

    return {
        "previous_offset": previous_offset,
        "next_offset": next_offset,
        "page_num": page_num,
        "offset": offset,
        "total_pages": total_pages
    }


def get_grade_book_page(request, course, course_key):
    """
    Get student records per page along with page information i.e current page, total pages and
    offset information.
    """
    # Unsanitized offset
    current_offset = request.GET.get('offset', 0)
    enrolled_students = User.objects.filter(
        courseenrollment__course_id=course_key,
        courseenrollment__is_active=1
    ).order_by('username').select_related("profile")

    total_students = enrolled_students.count()
    page = calculate_page_info(current_offset, total_students)
    offset = page["offset"]
    total_pages = page["total_pages"]

    if total_pages > 1:
        # Apply limit on queryset only if total number of students are greater then MAX_STUDENTS_PER_PAGE_GRADE_BOOK.
        enrolled_students = enrolled_students[offset: offset + MAX_STUDENTS_PER_PAGE_GRADE_BOOK]

    with modulestore().bulk_operations(course.location.course_key):
        student_info = [
            {
                'username': student.username,
                'id': student.id,
                'email': student.email,
                'grade_summary': CourseGradeFactory().read(student, course).summary
            }
            for student in enrolled_students
        ]
    return student_info, page


@transaction.non_atomic_requests
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.OVERRIDE_GRADES)
def spoc_gradebook(request, course_id):
    """
    Show the gradebook for this course:
    - Only shown for courses with enrollment < settings.FEATURES.get("MAX_ENROLLMENT_INSTR_BUTTONS")
    - Only displayed to course staff
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'staff', course_key, depth=None)
    student_info, page = get_grade_book_page(request, course, course_key)

    return render_to_response('courseware/gradebook.html', {
        'page': page,
        'page_url': reverse('spoc_gradebook', kwargs={'course_id': str(course_key)}),
        'students': student_info,
        'course': course,
        'course_id': course_key,
        # Checked above
        'staff_access': True,
        'ordered_grades': sorted(list(course.grade_cutoffs.items()), key=lambda i: i[1], reverse=True),
    })


@transaction.non_atomic_requests
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.OVERRIDE_GRADES)
def spoc_gradebook_excel(request, course_id):
    """Download the active learners and their current course grades as an XLSX workbook."""
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'staff', course_key, depth=None)
    enrollments = CourseEnrollment.objects.filter(
        course_id=course_key,
        is_active=True,
    ).order_by('id').select_related('user', 'user__profile')

    report_date = timezone.localdate()
    headers = [
        _('N'),
        _('Username'),
        _('Full name'),
        f"{_('Course grade')} ({report_date:%d.%m})",
    ]
    rows = []
    course_grade_factory = CourseGradeFactory()
    with modulestore().bulk_operations(course.location.course_key):
        for number, enrollment in enumerate(enrollments, 1):
            learner = enrollment.user
            profile_name = getattr(getattr(learner, 'profile', None), 'name', '') or ''
            full_name = profile_name.strip() or learner.get_full_name().strip() or learner.username
            course_grade = course_grade_factory.read(learner, course)
            rows.append([number, learner.username, full_name, float(course_grade.percent or 0)])

    course_name = str(getattr(course, 'display_name', course_key.course))
    filename_part = re.sub(r'[^A-Za-z0-9._-]+', '_', course_name).strip('_') or 'course'
    filename = f'{filename_part}_grades_{report_date:%Y-%m-%d}.xlsx'
    response = HttpResponse(_gradebook_xlsx(headers, rows, course_name), content_type=XLSX_CONTENT_TYPE)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
