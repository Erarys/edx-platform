import json
import sys
from pathlib import Path

from docx import Document


sys.stdout.reconfigure(encoding="utf-8")
input_path = Path(r"C:\Users\Erarys\Desktop\Итоговый экзамен_сопоставление.docx")
output_path = Path(r"C:\Users\Erarys\Desktop\edx-platform\.codex-dropdown-work\docx_content.json")

document = Document(input_path)
content = {
    "paragraphs": [paragraph.text for paragraph in document.paragraphs],
    "tables": [
        [[cell.text for cell in row.cells] for row in table.rows]
        for table in document.tables
    ],
}
output_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

nonempty = [(index, text) for index, text in enumerate(content["paragraphs"]) if text.strip()]
print(json.dumps({
    "paragraph_count": len(content["paragraphs"]),
    "nonempty_paragraph_count": len(nonempty),
    "table_count": len(content["tables"]),
    "separator_count": sum(text.strip().startswith("----------------") for text in content["paragraphs"]),
    "first_nonempty": nonempty[:40],
}, ensure_ascii=False, indent=2))
