// Build CNlab1.xlsx through CNlab15.xlsx in the library-tools import format.
import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const rootDir = path.resolve(process.cwd(), "..", "..");
const inputPath = path.join(rootDir, "tmp", "cnlab_questions.json");
const outputDir = path.join(rootDir, "outputs", "cnlab-20261111");
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));

await fs.mkdir(outputDir, { recursive: true });

function columnName(number) {
  let result = "";
  for (let value = number; value > 0; value = Math.floor((value - 1) / 26)) {
    result = String.fromCharCode(65 + ((value - 1) % 26)) + result;
  }
  return result;
}

const summaries = [];
for (let lab = 1; lab <= 15; lab += 1) {
  const questions = data[String(lab)];
  if (!Array.isArray(questions) || questions.length !== 10) {
    throw new Error(`CNlab${lab}: expected 10 questions, got ${questions?.length ?? 0}`);
  }

  const optionCount = Math.max(2, ...questions.map((item) => item.variants.length));
  const headers = [
    "question",
    ...Array.from({ length: optionCount }, (_, index) => `variant${index + 1}`),
    "correct_answer",
    "type",
  ];
  const rows = questions.map((item) => [
    item.question,
    ...item.variants,
    ...Array(optionCount - item.variants.length).fill(""),
    item.correct_answer,
    item.type,
  ]);

  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("Questions");
  sheet.showGridLines = false;
  sheet.tabColor = "#1F4E78";
  sheet.freezePanes.freezeRows(1);

  const lastColumn = columnName(headers.length);
  const lastRow = rows.length + 1;
  sheet.getRange(`A1:${lastColumn}${lastRow}`).values = [headers, ...rows];

  const used = sheet.getRange(`A1:${lastColumn}${lastRow}`);
  used.format.font = { name: "Arial", size: 10, color: "#1F2937" };
  used.format.verticalAlignment = "center";
  used.format.wrapText = true;

  const header = sheet.getRange(`A1:${lastColumn}1`);
  header.format = {
    fill: "#1F4E78",
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#FFFFFF" },
  };
  header.format.rowHeight = 30;

  const body = sheet.getRange(`A2:${lastColumn}${lastRow}`);
  body.format.borders = {
    insideHorizontal: { style: "thin", color: "#D9E2F3" },
    bottom: { style: "thin", color: "#D9E2F3" },
    left: { style: "thin", color: "#D9E2F3" },
    right: { style: "thin", color: "#D9E2F3" },
  };
  body.format.autofitRows();

  sheet.getRange(`A1:A${lastRow}`).format.columnWidth = 68;
  for (let index = 0; index < optionCount; index += 1) {
    const letter = columnName(2 + index);
    sheet.getRange(`${letter}1:${letter}${lastRow}`).format.columnWidth = 25;
  }
  const correctColumn = columnName(optionCount + 2);
  const typeColumn = columnName(optionCount + 3);
  sheet.getRange(`${correctColumn}1:${correctColumn}${lastRow}`).format.columnWidth = 24;
  sheet.getRange(`${typeColumn}1:${typeColumn}${lastRow}`).format.columnWidth = 16;
  sheet.getRange(`${correctColumn}2:${correctColumn}${lastRow}`).format.font = {
    name: "Arial",
    size: 10,
    bold: true,
    color: "#C00000",
  };
  sheet.getRange(`${correctColumn}2:${typeColumn}${lastRow}`).format.horizontalAlignment = "center";

  workbook.recalculate();
  const inspection = await workbook.inspect({
    kind: "table",
    range: `Questions!A1:${lastColumn}${lastRow}`,
    include: "values,formulas",
    tableMaxRows: 12,
    tableMaxCols: 13,
    maxChars: 12000,
  });
  const formulaErrors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 50 },
    summary: `CNlab${lab} formula error scan`,
  });
  if (!inspection.ndjson.includes(`Лаб${lab}-1.`) || !inspection.ndjson.includes(`Лаб${lab}-10.`)) {
    throw new Error(`CNlab${lab}: inspection did not contain first and last question`);
  }
  if (/"match"\s*:\s*true/.test(formulaErrors.ndjson)) {
    throw new Error(`CNlab${lab}: formula error detected`);
  }

  const outputPath = path.join(outputDir, `CNlab${lab}.xlsx`);
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);

  const imported = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  const importedSheet = imported.worksheets.getItem("Questions");
  const importedValues = importedSheet.getRange(`A1:${lastColumn}${lastRow}`).values;
  if (importedValues.length !== 11 || importedValues[0][0] !== "question") {
    throw new Error(`CNlab${lab}: XLSX round-trip failed`);
  }
  summaries.push({
    lab,
    questions: questions.length,
    optionColumns: optionCount,
    types: Object.fromEntries(
      [...new Set(questions.map((item) => item.type))].map((type) => [
        type,
        questions.filter((item) => item.type === type).length,
      ]),
    ),
    outputPath,
  });
}

console.log(JSON.stringify({ outputDir, files: summaries }, null, 2));
