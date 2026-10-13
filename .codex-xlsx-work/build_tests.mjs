import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const templatePath = "C:/Users/Erarys/Downloads/questions-template.xlsx";
const sourcePath = "C:/Users/Erarys/Downloads/KazNU_ST2026_questions.xlsx";
const outputDir = "C:/Users/Erarys/Desktop/edx-platform/outputs/01a13534-4201-71a2-bb04-cf122a6fdf80";
const previewDir = "C:/Users/Erarys/Desktop/edx-platform/.codex-xlsx-work/final-previews";

const specs = [
  { code: "1.1", start: 0, end: 31 },
  { code: "1.2", start: 32, end: 59 },
  { code: "1.3", start: 60, end: 88 },
  { code: "2.1", start: 90, end: 116 },
  { code: "2.2", start: 117, end: 146 },
  { code: "3.1", start: 147, end: 176 },
  { code: "3.2", start: 177, end: 206 },
  { code: "3.3", start: 207, end: 236 },
  { code: "3.4", start: 237, end: 266 },
  { code: "4.1", start: 267, end: 296 },
  { code: "4.2", start: 297, end: 326 },
  { code: "5.1", start: 327, end: 356 },
  { code: "5.2", start: 357, end: 386 },
  { code: "5.3", start: 387, end: 416 },
  { code: "6.1", start: 417, end: 446 },
  { code: "ҚОРЫТЫНДЫ ТЕСТ", start: 446, end: 594 },
];

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const sourceBook = await SpreadsheetFile.importXlsx(await FileBlob.load(sourcePath));
const sourceSheet = sourceBook.worksheets.getItem("Questions");
const sourceRows = sourceSheet.getRange("A2:H596").values;
const header = sourceSheet.getRange("A1:H1").values[0];

if (sourceRows.length !== 595) throw new Error(`Expected 595 source questions, got ${sourceRows.length}`);

const manifest = [];

for (const spec of specs) {
  const rows = sourceRows.slice(spec.start, spec.end + 1);
  const expectedCount = spec.end - spec.start + 1;
  if (rows.length !== expectedCount) throw new Error(`${spec.code}: expected ${expectedCount} rows, got ${rows.length}`);

  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(templatePath));
  const sheet = workbook.worksheets.getItem("Questions");
  const table = sheet.tables.items[0];
  if (!table) throw new Error(`${spec.code}: template table not found`);

  const existing = table.getDataRows();
  const existingCount = existing.values.length;
  if (existingCount !== 6) throw new Error(`${spec.code}: expected 6 template rows, got ${existingCount}`);
  existing.values = rows.slice(0, existingCount);
  if (rows.length > existingCount) table.rows.add(null, rows.slice(existingCount));

  workbook.recalculate();
  const lastRow = rows.length + 1;
  const keyCheck = await workbook.inspect({
    kind: "table",
    sheetId: "Questions",
    range: `A1:H${lastRow}`,
    include: "values,formulas",
    tableMaxRows: 4,
    tableMaxCols: 8,
    tableMaxCellChars: 180,
    maxChars: 6000,
  });
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 50 },
    maxChars: 3000,
  });
  if (errors.records.some(r => r.kind === "match")) throw new Error(`${spec.code}: formula error found`);

  const preview = await workbook.render({
    sheetName: "Questions",
    range: `A1:H${Math.min(lastRow, 10)}`,
    scale: 0.7,
    format: "png",
  });
  const safeCode = spec.code.replace(/[^\p{L}\p{N}._-]+/gu, "_");
  const previewPath = path.join(previewDir, `Gaj${safeCode}.png`);
  await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

  const fileName = `Gaj${spec.code}.xlsx`;
  const outputPath = path.join(outputDir, fileName);
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);

  const saved = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  const savedSheet = saved.worksheets.getItem("Questions");
  const savedRows = savedSheet.getRange(`A2:H${lastRow}`).values;
  const savedHeader = savedSheet.getRange("A1:H1").values[0];
  const sameHeader = JSON.stringify(savedHeader) === JSON.stringify(header);
  const sameRows = JSON.stringify(savedRows) === JSON.stringify(rows);
  const savedTable = savedSheet.tables.items[0];
  const savedTableRows = savedTable?.getDataRows().values.length ?? 0;
  if (!sameHeader || !sameRows || savedTableRows !== rows.length) {
    throw new Error(`${spec.code}: saved workbook verification failed (header=${sameHeader}, rows=${sameRows}, tableRows=${savedTableRows})`);
  }

  manifest.push({
    code: spec.code,
    start: spec.start,
    end: spec.end,
    count: rows.length,
    outputPath,
    previewPath,
    firstQuestion: rows[0][0],
    lastQuestion: rows.at(-1)[0],
    inspect: keyCheck.records,
  });
}

await fs.writeFile(path.join(outputDir, "manifest.json"), JSON.stringify(manifest, null, 2), "utf8");
console.log(JSON.stringify(manifest.map(({inspect, ...rest}) => rest), null, 2));
