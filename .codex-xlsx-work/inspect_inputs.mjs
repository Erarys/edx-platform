import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const files = [
  "C:/Users/Erarys/Downloads/questions-template.xlsx",
  "C:/Users/Erarys/Downloads/KazNU_ST2026_questions.xlsx",
];
const outputDir = "C:/Users/Erarys/Desktop/edx-platform/.codex-xlsx-work/previews";
await fs.mkdir(outputDir, { recursive: true });

for (const file of files) {
  const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
  const base = file.split("/").pop().replace(/\.xlsx$/i, "");
  const summary = await wb.inspect({
    kind: "workbook,sheet",
    maxChars: 6000,
  });
  console.log(`\n===== ${base} =====\n${summary.ndjson}`);
  const sheets = await wb.inspect({ kind: "sheet", include: "id,name", maxChars: 3000 });
  console.log(`\nSHEETS ${base}\n${sheets.ndjson}`);
  for (const sheet of wb.worksheets.items) {
    const used = sheet.getUsedRange();
    console.log(JSON.stringify({ file: base, sheet: sheet.name, usedAddress: used?.address ?? null }));
    if (used) {
      const previewRange = base === "questions-template"
        ? "A1:I10"
        : sheet.name === "Questions" ? "A1:I40" : used.address;
      const preview = await wb.render({ sheetName: sheet.name, range: previewRange, scale: 1, format: "png" });
      await fs.writeFile(`${outputDir}/${base}-${sheet.name.replace(/[^a-z0-9_-]/gi, "_")}.png`, new Uint8Array(await preview.arrayBuffer()));
    }
  }
  if (base === "KazNU_ST2026_questions") {
    for (const sheet of wb.worksheets.items) {
      console.log(`\n--- ${sheet.name} targeted rows ---`);
      if (sheet.name === "Questions") {
        const spans = [[1,40],[28,38],[55,66],[84,96],[108,123],[140,153],[170,183],[200,213],[230,243],[260,273],[290,303],[320,333],[350,363],[380,393],[410,423],[440,460],[585,596]];
        for (const [start,end] of spans) {
          const vals = sheet.getRange(`A${start}:I${end}`).values;
          console.log(JSON.stringify({range:`A${start}:I${end}`, rows:vals.map((row,i)=>[start+i,...row])}));
        }
      } else {
        const vals = used ? used.values : [];
        console.log(JSON.stringify({range:used?.address ?? null, rows:vals.map((row,i)=>[i+1,...row])}));
      }
    }
  }
}
