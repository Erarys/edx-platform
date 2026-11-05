import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const outputPath = "C:/Users/Erarys/Desktop/edx-platform/outputs/01a13534-4201-71a2-bb04-cf122a6fdf80/kaznu_cultural_b_3_questions_merged_dropdown.xlsx";
const previewDir = "C:/Users/Erarys/Desktop/edx-platform/.codex-dropdown-work/compact-preview";

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
const sheet = workbook.worksheets.getItem("Questions");
const used = sheet.getUsedRange();
const values = used.values;

if (used.address !== "A1:L174") {
  throw new Error(`Unexpected used range: ${used.address}`);
}
if (values.length !== 174 || values.slice(1).some((row) => row[11] !== "dropdown")) {
  throw new Error("The merged workbook does not contain 173 dropdown questions");
}

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "saved workbook formula error scan",
  maxChars: 5000,
});
if (errors.records.some((record) => record.kind === "match")) {
  throw new Error("Formula errors found in the saved workbook");
}

await fs.mkdir(previewDir, { recursive: true });
const previews = [
  ["questions_start", "A127:C132"],
  ["metadata_start", "K127:L132"],
  ["questions_end", "A170:C174"],
  ["metadata_end", "K170:L174"],
];
for (const [name, range] of previews) {
  const image = await workbook.render({ sheetName: "Questions", range, scale: 0.7, format: "png" });
  await fs.writeFile(`${previewDir}/${name}.png`, new Uint8Array(await image.arrayBuffer()));
}

const appended = values.slice(128);
const distribution = appended.reduce((counts, row) => {
  counts[row[10]] = (counts[row[10]] ?? 0) + 1;
  return counts;
}, {});
console.log(JSON.stringify({
  usedRange: used.address,
  questions: values.length - 1,
  appendedQuestions: appended.length,
  correctAnswerPositionDistribution: distribution,
  previewDir,
}, null, 2));
