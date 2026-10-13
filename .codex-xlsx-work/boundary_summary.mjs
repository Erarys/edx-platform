import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load("C:/Users/Erarys/Downloads/KazNU_ST2026_questions.xlsx"));
const s = wb.worksheets.getItem("Questions");
const data = s.getRange("A2:H596").values;
const boundaries = [0,31,32,59,60,88,89,90,116,117,146,147,176,177,206,207,236,237,266,267,296,297,326,327,356,357,386,387,416,417,446,447,448,594];
for (const idx of boundaries) {
  const r = data[idx];
  console.log(JSON.stringify({index:idx, excelRow:idx+2, question:r?.[0] ?? null, answer:r?.[6] ?? null, type:r?.[7] ?? null}));
}
