from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.table import Table


def iter_block_items(doc: Document):
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def run_info(run):
    color = run.font.color.rgb
    return {
        "text": run.text,
        "bold": run.bold,
        "italic": run.italic,
        "underline": bool(run.underline) if run.underline is not None else None,
        "color": str(color) if color else None,
        "highlight": str(run.font.highlight_color) if run.font.highlight_color else None,
        "style": run.style.name if run.style else None,
    }


def paragraph_info(p: Paragraph):
    ppr = p._p.pPr
    num = None
    if ppr is not None and ppr.numPr is not None:
        num_id = ppr.numPr.numId.val if ppr.numPr.numId is not None else None
        ilvl = ppr.numPr.ilvl.val if ppr.numPr.ilvl is not None else None
        num = {"numId": num_id, "ilvl": ilvl}
    return {
        "kind": "paragraph",
        "text": p.text,
        "style": p.style.name if p.style else None,
        "numbering": num,
        "runs": [run_info(r) for r in p.runs],
    }


def table_info(table: Table):
    rows = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            cells.append([paragraph_info(p) for p in cell.paragraphs])
        rows.append(cells)
    return {"kind": "table", "rows": rows}


def inspect(path: Path):
    doc = Document(path)
    blocks = []
    for block in iter_block_items(doc):
        blocks.append(paragraph_info(block) if isinstance(block, Paragraph) else table_info(block))
    return {
        "path": str(path),
        "paragraph_count": len(doc.paragraphs),
        "table_count": len(doc.tables),
        "blocks": blocks,
    }


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: inspect_docx.py output.json input.docx [input.docx ...]")
    output = Path(sys.argv[1])
    data = [inspect(Path(p)) for p in sys.argv[2:]]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{"path": d["path"], "paragraph_count": d["paragraph_count"], "table_count": d["table_count"], "blocks": len(d["blocks"])} for d in data], ensure_ascii=False))


if __name__ == "__main__":
    main()
