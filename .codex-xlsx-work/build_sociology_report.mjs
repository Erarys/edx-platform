import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputPath = "C:/Users/Erarys/Desktop/edx-platform/.codex-xlsx-work/sociology_grades_after_august_20261019.tsv";
const outputDir = "C:/Users/Erarys/Desktop/edx-platform/outputs/01a13534-4201-71a2-bb04-cf122a6fdf80";
const outputPath = `${outputDir}/Sociology_оценки_19.10.2026.xlsx`;
const previewPath = "C:/Users/Erarys/Desktop/edx-platform/.codex-xlsx-work/Sociology_оценки_19.10.2026.png";

const text = await fs.readFile(inputPath, "utf8");
const lines = text.replace(/^\uFEFF/, "").trimEnd().split(/\r?\n/);
const sourceHeaders = lines.shift().split("\t");
if (JSON.stringify(sourceHeaders) !== JSON.stringify(["N", "Login", "FullName", "Grade"])) {
  throw new Error(`Unexpected TSV headers: ${sourceHeaders.join(", ")}`);
}

const rows = lines.map((line, index) => {
  const cells = line.split("\t");
  if (cells.length !== 4) throw new Error(`Row ${index + 2}: expected 4 columns, got ${cells.length}`);
  const n = Number(cells[0]);
  const grade = Number(cells[3]);
  if (!Number.isInteger(n) || !Number.isFinite(grade) || grade < 0 || grade > 1) {
    throw new Error(`Row ${index + 2}: invalid number or grade`);
  }
  return [n, cells[1], cells[2], grade];
});

if (rows.length === 0) throw new Error("No learners found");
if (new Set(rows.map(row => row[1])).size !== rows.length) throw new Error("Duplicate logins found");

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Sociology");
sheet.showGridLines = false;
sheet.tabColor = "#17365D";

const matrix = [
  ["N", "Логин", "ФИО", "Оценка курса (19.10)"],
  ...rows,
];
const lastRow = matrix.length;
const used = sheet.getRange(`A1:D${lastRow}`);
used.values = matrix;
used.format.font = { name: "Arial", size: 10, color: "#1F2937" };
used.format.verticalAlignment = "center";

const header = sheet.getRange("A1:D1");
header.format = {
  fill: "#17365D",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#FFFFFF" },
};
header.format.rowHeight = 28;

const body = sheet.getRange(`A2:D${lastRow}`);
body.format.horizontalAlignment = "center";
body.format.verticalAlignment = "center";
body.format.wrapText = true;
body.format.borders = { preset: "all", style: "thin", color: "#808080" };
sheet.getRange(`D2:D${lastRow}`).format.numberFormat = "0.0%";

sheet.getRange(`A1:A${lastRow}`).format.columnWidth = 8;
sheet.getRange(`B1:B${lastRow}`).format.columnWidth = 29;
sheet.getRange(`C1:C${lastRow}`).format.columnWidth = 34;
sheet.getRange(`D1:D${lastRow}`).format.columnWidth = 25;
body.format.autofitRows();

const table = sheet.tables.add(`A1:D${lastRow}`, true, "SociologyGradesTable");
table.style = "TableStyleMedium2";
table.showFilterButton = true;
table.showBandedColumns = false;

sheet.freezePanes.freezeRows(1);
sheet.freezePanes.freezeColumns(1);

workbook.recalculate();

const topCheck = await workbook.inspect({
  kind: "table",
  range: "Sociology!A1:D8",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 4,
  tableMaxCellChars: 120,
  maxChars: 5000,
});
const bottomCheck = await workbook.inspect({
  kind: "table",
  range: `Sociology!A${lastRow - 4}:D${lastRow}`,
  include: "values,formulas",
  tableMaxRows: 5,
  tableMaxCols: 4,
  tableMaxCellChars: 120,
  maxChars: 4000,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  maxChars: 3000,
});
if (errors.records.some(record => record.kind === "match")) throw new Error("Formula errors found");

const preview = await workbook.render({
  sheetName: "Sociology",
  range: "A1:D22",
  scale: 1.2,
  format: "png",
});
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

const saved = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
const savedSheet = saved.worksheets.getItem("Sociology");
const savedValues = savedSheet.getRange(`A1:D${lastRow}`).values;
const savedGrades = savedSheet.getRange(`D2:D${lastRow}`).values.flat();
const nonzero = savedGrades.filter(value => typeof value === "number" && value > 0).length;
const expectedNonzero = rows.filter(row => row[3] > 0).length;
if (savedValues.length !== rows.length + 1 || nonzero !== expectedNonzero) {
  throw new Error(`Saved verification failed: rows=${savedValues.length}, nonzero=${nonzero}`);
}

console.log(JSON.stringify({
  outputPath,
  previewPath,
  learners: rows.length,
  nonzeroGrades: nonzero,
  zeroGrades: rows.length - nonzero,
  minGrade: Math.min(...rows.map(row => row[3])),
  maxGrade: Math.max(...rows.map(row => row[3])),
  averageGrade: rows.reduce((sum, row) => sum + row[3], 0) / rows.length,
  topRecords: topCheck.recordCount,
  bottomRecords: bottomCheck.recordCount,
}, null, 2));
