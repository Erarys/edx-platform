import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const projectDir = "C:/Users/Erarys/Desktop/edx-platform";
const dataPath = path.join(projectDir, ".codex-temp/course_report_data.json");
const outputDir = path.join(projectDir, "outputs/01a13ddf-b501-7153-aec4-eb00624f4fcd");
const outputPath = path.join(outputDir, "Аналитика_курсов_2026.xlsx");
const previewDir = path.join(projectDir, ".codex-temp/artifact-work/previews");
const data = JSON.parse(await fs.readFile(dataPath, "utf8"));

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const workbook = Workbook.create();
const coursesSheet = workbook.worksheets.add("Курсы");
const directionsSheet = workbook.worksheets.add("Направления");
const fontFamily = "Arial";
const headerFill = "#1F4E78";
const headerText = "#FFFFFF";
const bodyText = "#1F2937";
const noteText = "#5B6573";
const totalFill = "#D9EAF7";
const thinBorder = "#B8C7D9";

function styleBase(sheet, usedRange) {
  sheet.showGridLines = false;
  usedRange.format.font = { name: fontFamily, size: 10, color: bodyText };
  usedRange.format.verticalAlignment = "center";
}

function styleTitle(sheet, address) {
  const range = sheet.getRange(address);
  range.format.font = { name: fontFamily, size: 14, bold: true, color: "#15324B" };
  range.format.rowHeight = 24;
}

function styleNote(sheet, address) {
  const range = sheet.getRange(address);
  range.format.font = { name: fontFamily, size: 10, italic: true, color: noteText };
  range.format.wrapText = true;
  range.format.rowHeight = 34;
}

function styleHeader(sheet, address) {
  const range = sheet.getRange(address);
  range.format.fill = headerFill;
  range.format.font = { name: fontFamily, size: 10, bold: true, color: headerText };
  range.format.horizontalAlignment = "center";
  range.format.verticalAlignment = "center";
  range.format.wrapText = true;
  range.format.rowHeight = 32;
  range.format.borders = { preset: "outside", style: "thin", color: headerFill };
}

// Sheet 1: course summary.
coursesSheet.getRange("A2").values = [["Курсы со стартом с 01.01 по 14.10.2026"]];
coursesSheet.getRange("A3").values = [[
  `${data.course_runs} запусков: start 01.01–14.10.2026. Регистрации — все добавленные слушатели без учета is_active; отсутствие регистраций = 0. Сертификаты — downloadable. Дубликаты объединены.`
]];

const courseHeaders = [[
  "Название курса",
  "Зарегистрировано студентов",
  "Получили сертификат",
  "Направление",
  "Факультет",
]];
coursesSheet.getRange("A5:E5").values = courseHeaders;
const courseRows = data.courses.map((row) => [
  row.course_name,
  Number(row.enrolled_students),
  Number(row.certified_students),
  row.directions,
  row.faculties,
]);
if (courseRows.length) {
  coursesSheet.getRangeByIndexes(5, 0, courseRows.length, 5).values = courseRows;
}
const courseDataEndRow = 5 + courseRows.length;
const courseTotalRow = courseDataEndRow + 1;
coursesSheet.getRange(`A${courseTotalRow}:E${courseTotalRow}`).values = [[
  "Итого",
  courseRows.reduce((sum, row) => sum + row[1], 0),
  courseRows.reduce((sum, row) => sum + row[2], 0),
  "",
  "",
]];
const courseTable = coursesSheet.tables.add(`A5:E${courseDataEndRow}`, true, "CourseSummaryTable");
courseTable.style = "TableStyleMedium2";
styleBase(coursesSheet, coursesSheet.getRange(`A2:E${courseTotalRow}`));
styleTitle(coursesSheet, "A2:E2");
styleNote(coursesSheet, "A3:E3");
coursesSheet.getRange("A3:E3").format.rowHeight = 52;
styleHeader(coursesSheet, "A5:E5");
coursesSheet.getRange(`A6:A${courseDataEndRow}`).format.wrapText = true;
coursesSheet.getRange(`D6:E${courseDataEndRow}`).format.wrapText = true;
coursesSheet.getRange(`B6:C${courseTotalRow}`).format.numberFormat = "#,##0";
coursesSheet.getRange(`B6:C${courseTotalRow}`).format.horizontalAlignment = "right";
coursesSheet.getRange(`A${courseTotalRow}:E${courseTotalRow}`).format.fill = totalFill;
coursesSheet.getRange(`A${courseTotalRow}:E${courseTotalRow}`).format.font = { name: fontFamily, size: 10, bold: true, color: "#15324B" };
coursesSheet.getRange(`A${courseTotalRow}:E${courseTotalRow}`).format.borders = {
  top: { style: "thin", color: thinBorder },
  bottom: { style: "double", color: headerFill },
};
coursesSheet.getRange("A:A").format.columnWidth = 52;
coursesSheet.getRange("B:C").format.columnWidth = 20;
coursesSheet.getRange("D:E").format.columnWidth = 43;
coursesSheet.getRange(`A6:E${courseDataEndRow}`).format.autofitRows();
coursesSheet.freezePanes.freezeRows(5);
coursesSheet.tabColor = headerFill;

// Sheet 2: direction analysis.
directionsSheet.getRange("A2").values = [["Слушатели по направлениям"]];
directionsSheet.getRange("A3").values = [[
  "Сумма всех регистраций по курсам со стартом с 01.01.2026 до конца 14.10.2026. is_active не учитывается."
]];
directionsSheet.getRange("A5:B5").values = [["Направление", "Сумма слушателей"]];
const directionRows = data.directions.map((row) => [row.direction_name, Number(row.enrolled_students)]);
if (directionRows.length) {
  directionsSheet.getRangeByIndexes(5, 0, directionRows.length, 2).values = directionRows;
}
const directionDataEndRow = 5 + directionRows.length;
const directionTotalRow = directionDataEndRow + 1;
directionsSheet.getRange(`A${directionTotalRow}:B${directionTotalRow}`).values = [[
  "Итого",
  directionRows.reduce((sum, row) => sum + row[1], 0),
]];
const directionTable = directionsSheet.tables.add(`A5:B${directionDataEndRow}`, true, "DirectionSummaryTable");
directionTable.style = "TableStyleMedium2";
styleBase(directionsSheet, directionsSheet.getRange(`A2:B${directionTotalRow}`));
styleTitle(directionsSheet, "A2:B2");
styleNote(directionsSheet, "A3:B3");
styleHeader(directionsSheet, "A5:B5");
directionsSheet.getRange(`B6:B${directionTotalRow}`).format.numberFormat = "#,##0";
directionsSheet.getRange(`B6:B${directionTotalRow}`).format.horizontalAlignment = "right";
directionsSheet.getRange(`A${directionTotalRow}:B${directionTotalRow}`).format.fill = totalFill;
directionsSheet.getRange(`A${directionTotalRow}:B${directionTotalRow}`).format.font = { name: fontFamily, size: 10, bold: true, color: "#15324B" };
directionsSheet.getRange(`A${directionTotalRow}:B${directionTotalRow}`).format.borders = {
  top: { style: "thin", color: thinBorder },
  bottom: { style: "double", color: headerFill },
};
directionsSheet.getRange("A:A").format.columnWidth = 55;
directionsSheet.getRange("B:B").format.columnWidth = 22;
directionsSheet.getRange(`A6:B${directionDataEndRow}`).format.autofitRows();
directionsSheet.freezePanes.freezeRows(5);
directionsSheet.tabColor = "#5B9BD5";

workbook.recalculate();

const coursesInspect = await workbook.inspect({
  kind: "table",
  range: `Курсы!A2:E${Math.min(courseTotalRow, 12)}`,
  include: "values,formulas",
  tableMaxRows: 12,
  tableMaxCols: 5,
});
const directionsInspect = await workbook.inspect({
  kind: "table",
  range: `Направления!A2:B${directionTotalRow}`,
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 2,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});

const coursesPreview = await workbook.render({
  sheetName: "Курсы",
  range: `A1:E${Math.min(courseTotalRow, 18)}`,
  scale: 1,
  format: "png",
});
await fs.writeFile(path.join(previewDir, "courses.png"), new Uint8Array(await coursesPreview.arrayBuffer()));

const directionsPreview = await workbook.render({
  sheetName: "Направления",
  range: `A1:B${directionTotalRow}`,
  scale: 1.5,
  format: "png",
});
await fs.writeFile(path.join(previewDir, "directions.png"), new Uint8Array(await directionsPreview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

console.log(JSON.stringify({
  outputPath,
  courseRows: courseRows.length,
  directionRows: directionRows.length,
  enrolledTotal: courseRows.reduce((sum, row) => sum + row[1], 0),
  certifiedTotal: courseRows.reduce((sum, row) => sum + row[2], 0),
  coursesInspect: coursesInspect.ndjson,
  directionsInspect: directionsInspect.ndjson,
  errors: errors.ndjson,
}, null, 2));
