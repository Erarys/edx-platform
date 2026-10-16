import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "C:/Users/Erarys/Desktop/edx-platform/outputs/01a13534-4201-71a2-bb04-cf122a6fdf80/Sociology_оценки_16.10.2026.xlsx";
const previewPath = "C:/Users/Erarys/Desktop/edx-platform/.codex-xlsx-work/Sociology_existing.png";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const summary = await workbook.inspect({
  kind: "workbook,sheet,table",
  maxChars: 5000,
  tableMaxRows: 4,
  tableMaxCols: 4,
  tableMaxCellChars: 100,
});
console.log(summary.ndjson);
const preview = await workbook.render({ sheetName: "Sociology", range: "A1:D18", scale: 1.1, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
