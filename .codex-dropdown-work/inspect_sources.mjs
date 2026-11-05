import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const input = "C:/Users/Erarys/Downloads/kaznu_cultural_b_3_questions.xlsx";
const previewDir = "C:/Users/Erarys/Desktop/edx-platform/.codex-dropdown-work/xlsx-preview";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(input));

const summary = await workbook.inspect({
  kind: "workbook,sheet,table,definedName",
  maxChars: 12000,
  tableMaxRows: 20,
  tableMaxCols: 16,
  tableMaxCellChars: 300,
});
console.log(summary.ndjson);

await fs.mkdir(previewDir, { recursive: true });
for (const sheet of workbook.worksheets.items) {
  const used = sheet.getUsedRange();
  const detail = await workbook.inspect({
    kind: "table,formula,computedStyle",
    sheetId: sheet.name,
    range: used.address,
    maxChars: 40000,
    tableMaxRows: 200,
    tableMaxCols: 20,
    tableMaxCellChars: 1000,
    options: { maxResults: 500 },
  });
  console.log(detail.ndjson);
  const preview = await workbook.render({ sheetName: sheet.name, autoCrop: "all", scale: 1.1, format: "png" });
  const safeName = sheet.name.replace(/[^A-Za-z0-9_-]+/g, "_") || "sheet";
  await fs.writeFile(`${previewDir}/${safeName}.png`, new Uint8Array(await preview.arrayBuffer()));
}
