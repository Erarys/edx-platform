import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "C:/Users/Erarys/Desktop/edx-platform/outputs/01a13ddf-b501-7153-aec4-eb00624f4fcd/Аналитика_курсов_2026.xlsx";
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const sheets = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 3000 });
const totals = await workbook.inspect({
  kind: "table",
  range: "Курсы!A87:E89",
  include: "values,formulas",
  tableMaxRows: 5,
  tableMaxCols: 5,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
});
const preview = await workbook.render({
  sheetName: "Курсы",
  range: "A85:E89",
  scale: 1,
  format: "png",
});
await fs.writeFile(
  "C:/Users/Erarys/Desktop/edx-platform/.codex-temp/artifact-work/previews/courses_bottom.png",
  new Uint8Array(await preview.arrayBuffer()),
);
console.log(JSON.stringify({ sheets: sheets.ndjson, totals: totals.ndjson, errors: errors.ndjson }, null, 2));
