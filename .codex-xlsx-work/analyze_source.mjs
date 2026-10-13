import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const file = "C:/Users/Erarys/Downloads/KazNU_ST2026_questions.xlsx";
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
console.log(JSON.stringify({sheets: wb.worksheets.items.map(s => ({name:s.name, used:s.getUsedRange()?.address ?? null}))}));

for (const sheet of wb.worksheets.items) {
  const used = sheet.getUsedRange();
  if (!used) continue;
  const values = used.values;
  const interesting = [];
  for (let i = 0; i < values.length; i++) {
    const row = values[i];
    const present = row.filter(v => v !== null && v !== "");
    const first = row[0];
    const shortFirst = typeof first === "string" && first.length <= 45;
    const hasRange = typeof first === "string" && /\d+\s*[-–—]+\s*\d+/.test(first);
    const hasTest = typeof first === "string" && /(тест|дәріс|қорытынды|[1-6][.,][1-4])/i.test(first);
    if (present.length <= 2 || shortFirst || hasRange || hasTest) {
      interesting.push({excelRow:i+1, presentCount:present.length, row});
    }
  }
  console.log(`\n=== ${sheet.name} interesting (${interesting.length}) ===`);
  console.log(JSON.stringify(interesting));
}
