import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "C:/Users/Erarys/Downloads/kaznu_cultural_b_3_questions.xlsx";
const outputPath = "C:/Users/Erarys/Desktop/edx-platform/.codex-dropdown-work/xlsx_content.json";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheet = workbook.worksheets.getItem("Questions");
const used = sheet.getUsedRange();
await fs.writeFile(outputPath, JSON.stringify({
  sheetName: sheet.name,
  address: used.address,
  values: used.values,
  formulas: used.formulas,
}, null, 2), "utf8");
console.log(JSON.stringify({ sheetName: sheet.name, address: used.address, rows: used.values.length, cols: used.values[0]?.length ?? 0 }));
