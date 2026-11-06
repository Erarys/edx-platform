"""Excel export for the staff course analytics page."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from django.db.models import Count, Q
from django.utils import timezone

REPORT_PERIOD_LABELS = {
    "week": "Текущая неделя",
    "month": "Текущий месяц",
    "half_year": "Текущее полугодие",
}

REPORT_HEADERS = (
    "ID курса",
    "Название курса",
    "Период курса",
    "Активные обучающиеся",
    "Мужчины",
    "Женщины",
    "До 25 лет",
    "26–35 лет",
    "36–50 лет",
    "Старше 50 лет",
    "Завершили курс",
    "Период отчёта",
)


@dataclass(frozen=True)
class ReportPeriod:
    """Inclusive report start and current-time upper boundary."""

    key: str
    title: str
    start: datetime
    end: datetime

    @property
    def date_label(self):
        return f"{self.start:%d.%m.%Y} — {self.end:%d.%m.%Y}"

    @property
    def filename_label(self):
        return f"{self.start:%Y-%m-%d}_{self.end:%Y-%m-%d}"


def get_report_period(period_key, now=None):
    """Return the current calendar week, month, or half-year."""
    if period_key not in REPORT_PERIOD_LABELS:
        raise ValueError("Unsupported report period")

    now = timezone.localtime(now or timezone.now())
    if period_key == "week":
        start_date = now.date() - timedelta(days=now.weekday())
    elif period_key == "month":
        start_date = now.date().replace(day=1)
    else:
        start_date = now.date().replace(month=1 if now.month <= 6 else 7, day=1)

    start = now.replace(
        year=start_date.year,
        month=start_date.month,
        day=start_date.day,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return ReportPeriod(period_key, REPORT_PERIOD_LABELS[period_key], start, now)


def _format_course_date(value):
    if value is None:
        return ""
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.strftime("%d.%m.%Y")


def get_report_rows(period):
    """Build the course-level rows represented by the supplied SQL report."""
    # Import locally so the standalone workbook builder remains easy to test.
    from common.djangoapps.student.models import CourseEnrollment
    from lms.djangoapps.certificates.data import CertificateStatuses
    from lms.djangoapps.certificates.models import GeneratedCertificate
    from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

    courses = list(
        CourseOverview.objects.filter(
            start__gte=period.start, start__lte=period.end
        ).order_by("start", "display_name", "id")
    )
    if not courses:
        return []

    course_ids = [course.id for course in courses]
    current_year = period.end.year
    valid_birth_year = Q(
        user__profile__year_of_birth__gt=1900,
        user__profile__year_of_birth__lte=current_year,
    )
    enrollment_rows = (
        CourseEnrollment.objects.filter(
            course_id__in=course_ids,
            is_active=True,
            created__gte=period.start,
            created__lte=period.end,
        )
        .values("course_id")
        .annotate(
            active_users=Count("user_id", distinct=True),
            male_students=Count(
                "user_id", filter=Q(user__profile__gender="m"), distinct=True
            ),
            female_students=Count(
                "user_id", filter=Q(user__profile__gender="f"), distinct=True
            ),
            age_under_25=Count(
                "user_id",
                filter=valid_birth_year
                & Q(user__profile__year_of_birth__gt=current_year - 25),
                distinct=True,
            ),
            age_26_35=Count(
                "user_id",
                filter=valid_birth_year
                & Q(
                    user__profile__year_of_birth__gte=current_year - 35,
                    user__profile__year_of_birth__lte=current_year - 26,
                ),
                distinct=True,
            ),
            age_36_50=Count(
                "user_id",
                filter=valid_birth_year
                & Q(
                    user__profile__year_of_birth__gte=current_year - 50,
                    user__profile__year_of_birth__lte=current_year - 36,
                ),
                distinct=True,
            ),
            age_over_50=Count(
                "user_id",
                filter=valid_birth_year
                & Q(user__profile__year_of_birth__lt=current_year - 50),
                distinct=True,
            ),
        )
    )
    enrollment_by_course = {str(row["course_id"]): row for row in enrollment_rows}

    certificate_rows = (
        GeneratedCertificate.objects.filter(
            course_id__in=course_ids,
            status=CertificateStatuses.downloadable,
            created_date__gte=period.start,
            created_date__lte=period.end,
        )
        .values("course_id")
        .annotate(finished_students=Count("user_id", distinct=True))
    )
    certificates_by_course = {
        str(row["course_id"]): row["finished_students"] for row in certificate_rows
    }

    report_rows = []
    for course in courses:
        course_id = str(course.id)
        enrollment = enrollment_by_course.get(course_id, {})
        course_dates = " — ".join(
            value
            for value in (
                _format_course_date(course.start),
                _format_course_date(course.end),
            )
            if value
        )
        report_rows.append(
            {
                "course_id": course_id,
                "course_name": course.display_name or course_id,
                "course_period": course_dates,
                "active_users": enrollment.get("active_users", 0),
                "male_students": enrollment.get("male_students", 0),
                "female_students": enrollment.get("female_students", 0),
                "age_under_25": enrollment.get("age_under_25", 0),
                "age_26_35": enrollment.get("age_26_35", 0),
                "age_36_50": enrollment.get("age_36_50", 0),
                "age_over_50": enrollment.get("age_over_50", 0),
                "finished_students": certificates_by_course.get(course_id, 0),
                "report_period": period.date_label,
            }
        )

    return sorted(
        report_rows,
        key=lambda row: (
            -row["active_users"],
            row["course_name"].casefold(),
            row["course_id"],
        ),
    )


def _column_name(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xml_text(value, max_length=32767):
    """Escape text and remove characters forbidden by the XML 1.0 spec."""
    cleaned = "".join(
        character
        for character in str(value)[:max_length]
        if character in "\t\n\r"
        or 0x20 <= ord(character) <= 0xD7FF
        or 0xE000 <= ord(character) <= 0xFFFD
    )
    return escape(cleaned)


def _text_cell(reference, value, style=5):
    text = _xml_text(value)
    preserve = ' xml:space="preserve"' if text != text.strip() else ""
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t{preserve}>{text}</t></is></c>'


def _number_cell(reference, value, style=6):
    return f'<c r="{reference}" s="{style}"><v>{int(value)}</v></c>'


def _worksheet_one(rows, period):
    row_xml = [
        '<row r="1" ht="24" customHeight="1">'
        + _text_cell("A1", "Аналитика по курсам", 1)
        + "</row>",
        '<row r="2" ht="20" customHeight="1">'
        + _text_cell("A2", "Период отчёта", 2)
        + _text_cell("B2", f"{period.title}: {period.date_label}", 3)
        + "</row>",
        '<row r="4" ht="36" customHeight="1">'
        + "".join(
            _text_cell(f"{_column_name(index)}4", header, 4)
            for index, header in enumerate(REPORT_HEADERS, 1)
        )
        + "</row>",
    ]

    keys = (
        "course_id",
        "course_name",
        "course_period",
        "active_users",
        "male_students",
        "female_students",
        "age_under_25",
        "age_26_35",
        "age_36_50",
        "age_over_50",
        "finished_students",
        "report_period",
    )
    for row_index, row in enumerate(rows, 5):
        cells = []
        for column_index, key in enumerate(keys, 1):
            reference = f"{_column_name(column_index)}{row_index}"
            if 4 <= column_index <= 11:
                cells.append(_number_cell(reference, row[key]))
            else:
                cells.append(_text_cell(reference, row[key]))
        row_xml.append(
            f'<row r="{row_index}" ht="30" customHeight="1">{"".join(cells)}</row>'
        )

    last_row = len(rows) + 4
    merge_cells = ['<mergeCell ref="A1:L1"/>', '<mergeCell ref="B2:L2"/>']
    if not rows:
        last_row = 5
        row_xml.append(
            '<row r="5" ht="24" customHeight="1">'
            + _text_cell("A5", "За выбранный период данные отсутствуют.", 7)
            + "</row>"
        )
        merge_cells.append('<mergeCell ref="A5:L5"/>')

    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="A1:L{last_row}"/>
  <sheetViews><sheetView showGridLines="0" workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A5" sqref="A5"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>
    <col min="1" max="1" width="31" customWidth="1"/><col min="2" max="2" width="42" customWidth="1"/>
    <col min="3" max="3" width="24" customWidth="1"/><col min="4" max="11" width="16" customWidth="1"/>
    <col min="12" max="12" width="24" customWidth="1"/>
  </cols>
  <sheetData>{"".join(row_xml)}</sheetData>
  <autoFilter ref="A4:L{last_row}"/>
  <mergeCells count="{len(merge_cells)}">{"".join(merge_cells)}</mergeCells>
  <pageMargins left="0.35" right="0.35" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>
  <pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>
</worksheet>'''


def _worksheet_two(rows, period):
    note = (
        "Графики показывают до 25 курсов с наибольшим числом активных обучающихся. "
        "Полная таблица находится на листе «Отчёт»."
    )
    row_xml = [
        '<row r="1" ht="24" customHeight="1">'
        + _text_cell("A1", "Графики по курсам", 1)
        + "</row>",
        '<row r="2" ht="30" customHeight="1">'
        + _text_cell("A2", f"{period.date_label}. {note}", 3)
        + "</row>",
    ]
    merge_cells = ['<mergeCell ref="A1:N1"/>', '<mergeCell ref="A2:N2"/>']
    drawing = ""
    if rows:
        drawing = '<drawing r:id="rId1"/>'
    else:
        row_xml.append(
            '<row r="4" ht="24" customHeight="1">'
            + _text_cell("A4", "Нет данных для построения графиков.", 7)
            + "</row>"
        )
        merge_cells.append('<mergeCell ref="A4:N4"/>')

    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="A1:N60"/>
  <sheetViews><sheetView showGridLines="0" workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols><col min="1" max="14" width="12" customWidth="1"/></cols>
  <sheetData>{"".join(row_xml)}</sheetData>
  <mergeCells count="{len(merge_cells)}">{"".join(merge_cells)}</mergeCells>
  {drawing}
  <pageMargins left="0.35" right="0.35" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>
</worksheet>'''


def _chart_title_xml(title):
    return (
        '<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ru-RU" sz="1200" b="1"/>'
        f'<a:t>{_xml_text(title)}</a:t></a:r><a:endParaRPr lang="ru-RU"/></a:p></c:rich></c:tx>'
        '<c:layout/><c:overlay val="0"/></c:title>'
    )


def _string_cache(values):
    points = "".join(
        f'<c:pt idx="{index}"><c:v>{_xml_text(value)}</c:v></c:pt>'
        for index, value in enumerate(values)
    )
    return f'<c:strCache><c:ptCount val="{len(values)}"/>{points}</c:strCache>'


def _number_cache(values):
    points = "".join(
        f'<c:pt idx="{index}"><c:v>{int(value)}</c:v></c:pt>'
        for index, value in enumerate(values)
    )
    return f'<c:numCache><c:formatCode>0</c:formatCode><c:ptCount val="{len(values)}"/>{points}</c:numCache>'


def _series_xml(index, name, column, categories, values, color, last_row):
    category_formula = f"'Отчёт'!$B$5:$B${last_row}"
    value_formula = f"'Отчёт'!${column}$5:${column}${last_row}"
    return f'''<c:ser>
      <c:idx val="{index}"/><c:order val="{index}"/>
      <c:tx><c:v>{_xml_text(name)}</c:v></c:tx>
      <c:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:ln><a:noFill/></a:ln></c:spPr>
      <c:cat><c:strRef><c:f>{escape(category_formula)}</c:f>{_string_cache(categories)}</c:strRef></c:cat>
      <c:val><c:numRef><c:f>{escape(value_formula)}</c:f>{_number_cache(values)}</c:numRef></c:val>
    </c:ser>'''


def _chart_xml(title, rows, series, grouping, chart_number):
    categories = [row["course_name"] for row in rows]
    last_row = len(rows) + 4
    series_parts = []
    for index, (name, key, column, color) in enumerate(series):
        series_parts.append(
            _series_xml(
                index,
                name,
                column,
                categories,
                [row[key] for row in rows],
                color,
                last_row,
            )
        )
    overlap = '<c:overlap val="100"/>' if grouping == "stacked" else ""
    cat_axis_id = 100000 + chart_number * 10
    value_axis_id = cat_axis_id + 1
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <c:date1904 val="0"/><c:lang val="ru-RU"/><c:roundedCorners val="0"/>
  <c:chart>{_chart_title_xml(title)}<c:autoTitleDeleted val="0"/>
    <c:plotArea><c:layout/>
      <c:barChart><c:barDir val="bar"/><c:grouping val="{grouping}"/><c:varyColors val="0"/>
        {"".join(series_parts)}{overlap}<c:gapWidth val="65"/><c:axId val="{cat_axis_id}"/><c:axId val="{value_axis_id}"/>
      </c:barChart>
      <c:catAx><c:axId val="{cat_axis_id}"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="l"/>
        <c:tickLblPos val="nextTo"/><c:crossAx val="{value_axis_id}"/><c:crosses val="autoZero"/><c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/>
      </c:catAx>
      <c:valAx><c:axId val="{value_axis_id}"/><c:scaling><c:orientation val="minMax"/><c:min val="0"/></c:scaling><c:delete val="0"/><c:axPos val="b"/>
        <c:numFmt formatCode="0" sourceLinked="0"/><c:majorGridlines/><c:tickLblPos val="nextTo"/><c:crossAx val="{cat_axis_id}"/><c:crosses val="autoZero"/><c:crossBetween val="between"/>
      </c:valAx>
    </c:plotArea>
    <c:legend><c:legendPos val="t"/><c:layout/><c:overlay val="0"/></c:legend>
    <c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/><c:showDLblsOverMax val="0"/>
  </c:chart>
  <c:printSettings><c:headerFooter/><c:pageMargins b="0.75" l="0.7" r="0.7" t="0.75" header="0.3" footer="0.3"/><c:pageSetup/></c:printSettings>
</c:chartSpace>'''


def _drawing_xml():
    anchors = ((0, 2, 14, 20), (0, 21, 14, 39), (0, 40, 14, 58))
    parts = []
    for index, (from_col, from_row, to_col, to_row) in enumerate(anchors, 1):
        parts.append(f'''<xdr:twoCellAnchor>
  <xdr:from><xdr:col>{from_col}</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{from_row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
  <xdr:to><xdr:col>{to_col}</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{to_row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
  <xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="{index + 1}" name="Диаграмма {index}"/><xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>
    <xdr:xfrm/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rId{index}"/></a:graphicData></a:graphic>
  </xdr:graphicFrame><xdr:clientData/>
</xdr:twoCellAnchor>''')
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">{"".join(parts)}</xdr:wsDr>"""


def _styles_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="4">
    <font><sz val="10"/><color rgb="FF172033"/><name val="Arial"/><family val="2"/></font>
    <font><b/><sz val="14"/><color rgb="FF172033"/><name val="Arial"/><family val="2"/></font>
    <font><b/><sz val="10"/><color rgb="FF244B66"/><name val="Arial"/><family val="2"/></font>
    <font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Arial"/><family val="2"/></font>
  </fonts>
  <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF244B66"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left/><right/><top/><bottom style="thin"><color rgb="FFE7EDF4"/></bottom><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="8">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="3" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"><alignment horizontal="right" vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment horizontal="center" vertical="center"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Обычный" xfId="0" builtinId="0"/></cellStyles>
  <dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>"""


def _theme_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Analytics">
  <a:themeElements><a:clrScheme name="Analytics"><a:dk1><a:srgbClr val="172033"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="244B66"/></a:dk2><a:lt2><a:srgbClr val="F6F8FB"/></a:lt2><a:accent1><a:srgbClr val="2F6F95"/></a:accent1><a:accent2><a:srgbClr val="3B8067"/></a:accent2><a:accent3><a:srgbClr val="D98C4A"/></a:accent3><a:accent4><a:srgbClr val="7A68A6"/></a:accent4><a:accent5><a:srgbClr val="5C9EAD"/></a:accent5><a:accent6><a:srgbClr val="B56B84"/></a:accent6><a:hlink><a:srgbClr val="2F6F95"/></a:hlink><a:folHlink><a:srgbClr val="7A68A6"/></a:folHlink></a:clrScheme>
  <a:fontScheme name="Arial"><a:majorFont><a:latin typeface="Arial"/></a:majorFont><a:minorFont><a:latin typeface="Arial"/></a:minorFont></a:fontScheme>
  <a:fmtScheme name="Analytics"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements>
</a:theme>"""


def build_analytics_workbook(report_rows: Iterable[dict], period):
    """Return an XLSX workbook containing the report table and native charts."""
    rows = list(report_rows)
    chart_rows = rows[:25]
    output = BytesIO()
    created_at = timezone.now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    chart_parts = (
        "".join(
            f'<Override PartName="/xl/charts/chart{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
            for index in range(1, 4)
        )
        if chart_rows
        else ""
    )
    drawing_parts = (
        '<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
        if chart_rows
        else ""
    )

    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>{drawing_parts}{chart_parts}<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>""",
        )
        archive.writestr(
            "docProps/app.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Open edX</Application><DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Листы</vt:lpstr></vt:variant><vt:variant><vt:i4>2</vt:i4></vt:variant></vt:vector></HeadingPairs><TitlesOfParts><vt:vector size="2" baseType="lpstr"><vt:lpstr>Отчёт</vt:lpstr><vt:lpstr>Графики</vt:lpstr></vt:vector></TitlesOfParts><Company>КазНУ</Company><LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0300</AppVersion></Properties>""",
        )
        archive.writestr(
            "docProps/core.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>Open edX</dc:creator><cp:lastModifiedBy>Open edX</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{created_at}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{created_at}</dcterms:modified><dc:title>Аналитика по курсам</dc:title></cp:coreProperties>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="9303"/><workbookPr defaultThemeVersion="166925"/><bookViews><workbookView xWindow="0" yWindow="0" windowWidth="24000" windowHeight="12000" activeTab="0"/></bookViews><sheets><sheet name="Отчёт" sheetId="1" r:id="rId1"/><sheet name="Графики" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/></Relationships>""",
        )
        archive.writestr("xl/styles.xml", _styles_xml())
        archive.writestr("xl/theme/theme1.xml", _theme_xml())
        archive.writestr("xl/worksheets/sheet1.xml", _worksheet_one(rows, period))
        archive.writestr("xl/worksheets/sheet2.xml", _worksheet_two(rows, period))

        if chart_rows:
            archive.writestr(
                "xl/worksheets/_rels/sheet2.xml.rels",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/></Relationships>""",
            )
            archive.writestr("xl/drawings/drawing1.xml", _drawing_xml())
            archive.writestr(
                "xl/drawings/_rels/drawing1.xml.rels",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart3.xml"/></Relationships>""",
            )
            archive.writestr(
                "xl/charts/chart1.xml",
                _chart_xml(
                    "Активные и завершившие курс",
                    chart_rows,
                    (
                        ("Активные", "active_users", "D", "2F6F95"),
                        ("Завершили", "finished_students", "K", "3B8067"),
                    ),
                    "clustered",
                    1,
                ),
            )
            archive.writestr(
                "xl/charts/chart2.xml",
                _chart_xml(
                    "Активные обучающиеся по полу",
                    chart_rows,
                    (
                        ("Мужчины", "male_students", "E", "2F6F95"),
                        ("Женщины", "female_students", "F", "D98C4A"),
                    ),
                    "stacked",
                    2,
                ),
            )
            archive.writestr(
                "xl/charts/chart3.xml",
                _chart_xml(
                    "Активные обучающиеся по возрасту",
                    chart_rows,
                    (
                        ("До 25 лет", "age_under_25", "G", "5C9EAD"),
                        ("26–35 лет", "age_26_35", "H", "2F6F95"),
                        ("36–50 лет", "age_36_50", "I", "7A68A6"),
                        ("Старше 50 лет", "age_over_50", "J", "B56B84"),
                    ),
                    "stacked",
                    3,
                ),
            )

    return output.getvalue()
