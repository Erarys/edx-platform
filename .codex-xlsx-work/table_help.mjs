import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load("C:/Users/Erarys/Downloads/questions-template.xlsx"));
console.log(wb.help("table collection access", { search: "tables|getItemAt|items|rows.add|getDataRows", include: "index,examples,notes", maxChars: 6000 }).ndjson);
const sheet = wb.worksheets.getItem("Questions");
console.log(JSON.stringify({ items: sheet.tables.items, count: Array.from(sheet.tables).length, names: Array.from(sheet.tables).map(t => t.name) }));
try {
  const t = sheet.tables.getItemAt(0);
  console.log(JSON.stringify({byIndexName:t.name, isNullObject:t.isNullObject, dataRows:t.getDataRows().values.length}));
} catch (e) {
  console.log(JSON.stringify({byIndexError:String(e)}));
}
