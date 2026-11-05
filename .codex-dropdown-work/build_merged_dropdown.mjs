import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "C:/Users/Erarys/Downloads/kaznu_cultural_b_3_questions.xlsx";
const docxJsonPath = "C:/Users/Erarys/Desktop/edx-platform/.codex-dropdown-work/docx_content.json";
const outputDir = "C:/Users/Erarys/Desktop/edx-platform/outputs/01a13534-4201-71a2-bb04-cf122a6fdf80";
const outputPath = `${outputDir}/kaznu_cultural_b_3_questions_merged_dropdown.xlsx`;
const previewDir = "C:/Users/Erarys/Desktop/edx-platform/.codex-dropdown-work/final-preview";

function parseBlocks(paragraphs) {
  const blocks = [];
  let current = [];
  let started = false;

  for (const paragraph of paragraphs) {
    const text = String(paragraph ?? "").trim();
    if (text.startsWith("----------------")) {
      if (current.length > 0) blocks.push(current);
      current = [];
      started = true;
    } else if (text) {
      if (started || text.startsWith("Установите соответствие")) {
        started = true;
        current.push(text);
      }
    }
  }
  if (current.length > 0) blocks.push(current);
  return blocks;
}

function seededRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6D2B79F5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffleAnswers(answers, random) {
  const shuffled = answers.map((text, index) => ({ text, isCorrect: index === 0 }));
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const target = Math.floor(random() * (index + 1));
    [shuffled[index], shuffled[target]] = [shuffled[target], shuffled[index]];
  }
  return {
    answers: shuffled.map((item) => item.text),
    correctAnswer: String(shuffled.findIndex((item) => item.isCorrect) + 1),
  };
}

const docx = JSON.parse(await fs.readFile(docxJsonPath, "utf8"));
const blocks = parseBlocks(docx.paragraphs);
if (blocks.length !== 46) throw new Error(`Expected 46 Word blocks, got ${blocks.length}`);
if (blocks.some((block) => block.length !== 7)) {
  throw new Error("Each Word block must contain instruction, term, and five answers");
}

const random = seededRandom(20261103);
const newRows = blocks.map((block) => {
  const [instruction, term, correct, ...incorrect] = block;
  const originalAnswers = [correct, ...incorrect];
  if (new Set(originalAnswers).size !== originalAnswers.length) {
    throw new Error(`Duplicate answer in Word block: ${term}`);
  }
  const shuffled = shuffleAnswers(originalAnswers, random);
  return [
    `${instruction}\n${term}`,
    ...shuffled.answers,
    null,
    null,
    null,
    null,
    shuffled.correctAnswer,
    "dropdown",
  ];
});

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheet = workbook.worksheets.getItem("Questions");
const originalValues = sheet.getUsedRange().values;
const expectedHeaders = [
  "question", "variant1", "variant2", "variant3", "variant4", "variant5",
  "variant6", "variant7", "variant8", "variant9", "correct_answer", "type",
];
if (JSON.stringify(originalValues[0]) !== JSON.stringify(expectedHeaders)) {
  throw new Error("Unexpected Excel template headers");
}
if (originalValues.length !== 128) {
  throw new Error(`Expected 127 existing questions, got ${originalValues.length - 1}`);
}
if (originalValues.slice(1).some((row) => row[11] !== "dropdown")) {
  throw new Error("The source Excel contains a non-dropdown question");
}

const firstNewRow = originalValues.length + 1;
const lastNewRow = originalValues.length + newRows.length;
const appendedRange = sheet.getRange(`A${firstNewRow}:L${lastNewRow}`);
appendedRange.values = newRows;
appendedRange.format.font = { name: "Arial", size: 11, color: "#243447" };
appendedRange.format.verticalAlignment = "top";
appendedRange.format.wrapText = true;
appendedRange.setNumberFormat("@");
for (let row = firstNewRow; row <= lastNewRow; row += 1) {
  sheet.getRange(`A${row}:L${row}`).format.fill = row % 2 === 0 ? "#F3F6FA" : "#FFFFFF";
}
appendedRange.format.autofitRows();
sheet.getRange(`L${firstNewRow}:L${lastNewRow}`).dataValidation = {
  rule: { type: "list", values: ["single", "multi", "dropdown", "text", "text_case_sensitive", "numeric"] },
};

workbook.recalculate();

const check = await workbook.inspect({
  kind: "table",
  range: `Questions!A${firstNewRow - 2}:L${lastNewRow}`,
  include: "values,formulas",
  tableMaxRows: newRows.length + 2,
  tableMaxCols: 12,
  tableMaxCellChars: 400,
  maxChars: 50000,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
  maxChars: 5000,
});
if (errors.records.some((record) => record.kind === "match")) {
  throw new Error("Formula errors found in merged workbook");
}

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

const saved = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
const savedSheet = saved.worksheets.getItem("Questions");
const savedValues = savedSheet.getUsedRange().values;
if (savedValues.length !== 174) {
  throw new Error(`Saved workbook has ${savedValues.length - 1} questions instead of 173`);
}
const savedNewRows = savedValues.slice(firstNewRow - 1);
for (let index = 0; index < savedNewRows.length; index += 1) {
  const row = savedNewRows[index];
  const block = blocks[index];
  const correctIndex = Number(row[10]);
  if (row[11] !== "dropdown" || correctIndex < 1 || correctIndex > 5) {
    throw new Error(`Invalid dropdown metadata in appended row ${firstNewRow + index}`);
  }
  if (row[correctIndex] !== block[2]) {
    throw new Error(`Correct answer mismatch in appended row ${firstNewRow + index}`);
  }
  const outputAnswers = row.slice(1, 6).filter((value) => value != null);
  const sourceAnswers = block.slice(2);
  if (JSON.stringify([...outputAnswers].sort()) !== JSON.stringify([...sourceAnswers].sort())) {
    throw new Error(`Answer set mismatch in appended row ${firstNewRow + index}`);
  }
}

const previewRanges = [
  ["start", `A${firstNewRow - 2}:L${firstNewRow + 7}`],
  ["end", `A${lastNewRow - 7}:L${lastNewRow}`],
];
for (const [name, range] of previewRanges) {
  const preview = await saved.render({ sheetName: "Questions", range, scale: 1.1, format: "png" });
  await fs.writeFile(`${previewDir}/${name}.png`, new Uint8Array(await preview.arrayBuffer()));
}

const distribution = savedNewRows.reduce((counts, row) => {
  counts[row[10]] = (counts[row[10]] ?? 0) + 1;
  return counts;
}, {});
console.log(JSON.stringify({
  outputPath,
  existingQuestions: originalValues.length - 1,
  addedQuestions: newRows.length,
  totalQuestions: savedValues.length - 1,
  correctAnswerPositionDistribution: distribution,
  inspectedRecords: check.recordCount,
}, null, 2));
