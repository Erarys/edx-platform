"""Excel export for the staff course analytics page."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from django.db.models import Count
from django.utils import timezone

REPORT_PERIOD_LABELS = {
    "week": "Текущая неделя",
    "month": "Текущий месяц",
    "half_year": "Текущее полугодие",
}

REPORT_HEADERS = (
    "Код курса",
    "Название курса",
    "Период курса",
    "Зарегистрировано",
    "Завершили курс",
    "Данные за период",
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

    @property
    def spreadsheet_label(self):
        return f"{self.start:%Y.%m.%d}-{self.end:%Y.%m.%d}"


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
    """Build course rows using the selected activity period and year-start cutoff."""
    # Import locally so the standalone workbook builder remains easy to test.
    from common.djangoapps.student.models import CourseEnrollment
    from lms.djangoapps.certificates.data import CertificateStatuses
    from lms.djangoapps.certificates.models import GeneratedCertificate
    from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

    course_year_start = period.end.replace(
        month=1, day=1, hour=0, minute=0, second=0, microsecond=0
    )
    courses = list(
        CourseOverview.objects.filter(start__gte=course_year_start).order_by("id")
    )
    if not courses:
        return []

    course_ids = [course.id for course in courses]
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
                "finished_students": certificates_by_course.get(course_id, 0),
                "report_period": period.spreadsheet_label,
            }
        )

    return sorted(report_rows, key=lambda row: row["course_id"].casefold())


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
        + "".join(
            _text_cell(f"{_column_name(index)}1", header, 1)
            for index, header in enumerate(REPORT_HEADERS, 1)
        )
        + "</row>"
    ]

    keys = (
        "course_id",
        "course_name",
        "course_period",
        "active_users",
        "finished_students",
        "report_period",
    )
    for row_index, row in enumerate(rows, 2):
        cells = []
        for column_index, key in enumerate(keys, 1):
            reference = f"{_column_name(column_index)}{row_index}"
            if 4 <= column_index <= 5:
                cells.append(_number_cell(reference, row[key], 3))
            else:
                cells.append(_text_cell(reference, row[key], 2))
        row_xml.append(
            f'<row r="{row_index}" ht="21" customHeight="1">{"".join(cells)}</row>'
        )

    last_row = len(rows) + 1
    merge_cells = []
    if not rows:
        last_row = 2
        row_xml.append(
            '<row r="2" ht="24" customHeight="1">'
            + _text_cell("A2", "За выбранный период данные отсутствуют.", 7)
            + "</row>"
        )
        merge_cells.append('<mergeCell ref="A2:F2"/>')

    merges = (
        f'<mergeCells count="{len(merge_cells)}">{"".join(merge_cells)}</mergeCells>'
        if merge_cells
        else ""
    )

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="A1:F{last_row}"/>
  <sheetViews><sheetView showGridLines="1" workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="20"/>
  <cols>
    <col min="1" max="1" width="41" customWidth="1"/><col min="2" max="2" width="83" customWidth="1"/>
    <col min="3" max="3" width="24" customWidth="1"/><col min="4" max="4" width="20" customWidth="1"/>
    <col min="5" max="5" width="18" customWidth="1"/><col min="6" max="6" width="22" customWidth="1"/>
  </cols>
  <sheetData>{"".join(row_xml)}</sheetData>
  <autoFilter ref="A1:F{last_row}"/>
  {merges}
  <pageMargins left="0.35" right="0.35" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>
  <pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>
</worksheet>"""


def _worksheet_two(rows, period):
    row_xml = [
        '<row r="1" ht="24" customHeight="1">'
        + _text_cell("A1", "Название курса", 4)
        + _text_cell("B1", "Зарегистрировано", 4)
        + _text_cell("C1", "Завершили курс", 4)
        + "</row>"
    ]
    for row_index, row in enumerate(rows, 2):
        row_xml.append(
            f'<row r="{row_index}" ht="21" customHeight="1">'
            + _text_cell(f"A{row_index}", row["course_name"], 5)
            + _number_cell(f"B{row_index}", row["active_users"], 6)
            + _number_cell(f"C{row_index}", row["finished_students"], 6)
            + "</row>"
        )

    merge_cells = []
    drawing = ""
    if rows:
        drawing = '<drawing r:id="rId1"/>'
    else:
        row_xml.append(
            '<row r="2" ht="24" customHeight="1">'
            + _text_cell("A2", "Нет данных для построения графика.", 7)
            + "</row>"
        )
        merge_cells.append('<mergeCell ref="A2:C2"/>')

    merges = (
        f'<mergeCells count="{len(merge_cells)}">{"".join(merge_cells)}</mergeCells>'
        if merge_cells
        else ""
    )

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="A1:O17"/>
  <sheetViews><sheetView showGridLines="1" workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="20"/>
  <cols><col min="1" max="1" width="79" customWidth="1"/><col min="2" max="2" width="18" customWidth="1"/><col min="3" max="3" width="16" customWidth="1"/><col min="4" max="15" width="12" customWidth="1"/></cols>
  <sheetData>{"".join(row_xml)}</sheetData>
  {merges}
  {drawing}
  <pageMargins left="0.35" right="0.35" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>
</worksheet>"""


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
    category_formula = f"'Графика'!$A$2:$A${last_row}"
    value_formula = f"'Графика'!${column}$2:${column}${last_row}"
    return f'''<c:ser>
      <c:idx val="{index}"/><c:order val="{index}"/>
      <c:tx><c:v>{_xml_text(name)}</c:v></c:tx>
      <c:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:ln><a:noFill/></a:ln></c:spPr>
      <c:cat><c:strRef><c:f>{escape(category_formula)}</c:f>{_string_cache(categories)}</c:strRef></c:cat>
      <c:val><c:numRef><c:f>{escape(value_formula)}</c:f>{_number_cache(values)}</c:numRef></c:val>
    </c:ser>'''


def _chart_xml(title, rows):
    categories = [row["course_name"] for row in rows]
    last_row = len(rows) + 1
    series_parts = []
    series = (
        ("Завершили курс", "finished_students", "C", "ED7D31"),
        ("Зарегистрировано", "active_users", "B", "2F75B5"),
    )
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
    cat_axis_id = 100010
    value_axis_id = 100011
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <c:date1904 val="0"/><c:lang val="ru-RU"/><c:roundedCorners val="0"/>
  <c:chart>{_chart_title_xml(title)}<c:autoTitleDeleted val="0"/>
    <c:plotArea><c:layout/>
      <c:barChart><c:barDir val="bar"/><c:grouping val="clustered"/><c:varyColors val="0"/>
        {"".join(series_parts)}<c:gapWidth val="70"/><c:axId val="{cat_axis_id}"/><c:axId val="{value_axis_id}"/>
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
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><xdr:twoCellAnchor>
  <xdr:from><xdr:col>3</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>0</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
  <xdr:to><xdr:col>15</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>16</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
  <xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="2" name="Диаграмма 1"/><xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>
    <xdr:xfrm/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rId1"/></a:graphicData></a:graphic>
  </xdr:graphicFrame><xdr:clientData/>
</xdr:twoCellAnchor></xdr:wsDr>"""


def _styles_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="3">
    <font><sz val="11"/><color rgb="FF000000"/><name val="Aptos Narrow"/><family val="2"/></font>
    <font><b/><sz val="11"/><color rgb="FF000000"/><name val="Aptos Narrow"/><family val="2"/></font>
    <font><i/><sz val="11"/><color rgb="FF667085"/><name val="Aptos Narrow"/><family val="2"/></font>
  </fonts>
  <fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFC6E0B4"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE2F0D9"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FF70AD47"/></left><right style="thin"><color rgb="FF70AD47"/></right><top style="thin"><color rgb="FF70AD47"/></top><bottom style="thin"><color rgb="FF70AD47"/></bottom><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="8">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1"><alignment vertical="center"/></xf>
    <xf numFmtId="3" fontId="0" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" applyBorder="1"><alignment horizontal="right" vertical="center"/></xf>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment vertical="center"/></xf>
    <xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment horizontal="right" vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment horizontal="center" vertical="center"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Обычный" xfId="0" builtinId="0"/></cellStyles>
  <dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>"""


def _theme_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Course report">
  <a:themeElements><a:clrScheme name="Course report"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2><a:accent1><a:srgbClr val="2F75B5"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>
  <a:fontScheme name="Aptos Narrow"><a:majorFont><a:latin typeface="Aptos Narrow"/></a:majorFont><a:minorFont><a:latin typeface="Aptos Narrow"/></a:minorFont></a:fontScheme>
  <a:fmtScheme name="Analytics"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements>
</a:theme>"""


def build_analytics_workbook(report_rows: Iterable[dict], period):
    """Return an XLSX workbook containing the report table and native charts."""
    rows = list(report_rows)
    chart_rows = sorted(
        (row for row in rows if row["active_users"] or row["finished_students"]),
        key=lambda row: (
            -row["active_users"],
            -row["finished_students"],
            row["course_name"].casefold(),
        ),
    )[:10]
    output = BytesIO()
    created_at = timezone.now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    chart_parts = (
        '<Override PartName="/xl/charts/chart1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
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
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Open edX</Application><DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Листы</vt:lpstr></vt:variant><vt:variant><vt:i4>2</vt:i4></vt:variant></vt:vector></HeadingPairs><TitlesOfParts><vt:vector size="2" baseType="lpstr"><vt:lpstr>Отчет</vt:lpstr><vt:lpstr>Графика</vt:lpstr></vt:vector></TitlesOfParts><Company>КазНУ</Company><LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0300</AppVersion></Properties>""",
        )
        archive.writestr(
            "docProps/core.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>Open edX</dc:creator><cp:lastModifiedBy>Open edX</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{created_at}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{created_at}</dcterms:modified><dc:title>Аналитика по курсам</dc:title></cp:coreProperties>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="9303"/><workbookPr defaultThemeVersion="166925"/><bookViews><workbookView xWindow="0" yWindow="0" windowWidth="24000" windowHeight="12000" activeTab="0"/></bookViews><sheets><sheet name="Отчет" sheetId="1" r:id="rId1"/><sheet name="Графика" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/></Relationships>""",
        )
        archive.writestr("xl/styles.xml", _styles_xml())
        archive.writestr("xl/theme/theme1.xml", _theme_xml())
        archive.writestr("xl/worksheets/sheet1.xml", _worksheet_one(rows, period))
        archive.writestr("xl/worksheets/sheet2.xml", _worksheet_two(chart_rows, period))

        if chart_rows:
            archive.writestr(
                "xl/worksheets/_rels/sheet2.xml.rels",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/></Relationships>""",
            )
            archive.writestr("xl/drawings/drawing1.xml", _drawing_xml())
            archive.writestr(
                "xl/drawings/_rels/drawing1.xml.rels",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/></Relationships>""",
            )
            archive.writestr(
                "xl/charts/chart1.xml",
                _chart_xml("Зарегистрировано и завершили курс", chart_rows),
            )

    return output.getvalue()
