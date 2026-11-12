"""Extract ordered paragraphs, numbering, and red answers from a CN lab DOCX."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


def iter_blocks(document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield "paragraph", Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield "table", Table(child, document)


def run_payload(run):
    color = None
    theme = None
    color_node = run._r.find(qn("w:rPr") + "/" + qn("w:color"))
    if color_node is not None:
        color = color_node.get(qn("w:val"))
        theme = color_node.get(qn("w:themeColor"))
    return {
        "text": run.text,
        "color": color,
        "theme_color": theme,
        "bold": run.bold,
        "italic": run.italic,
    }


def paragraph_payload(paragraph, location):
    p_pr = paragraph._p.pPr
    num_id = None
    ilvl = None
    if p_pr is not None and p_pr.numPr is not None:
        if p_pr.numPr.numId is not None:
            num_id = p_pr.numPr.numId.val
        if p_pr.numPr.ilvl is not None:
            ilvl = p_pr.numPr.ilvl.val
    runs = [run_payload(run) for run in paragraph.runs if run.text]
    return {
        "location": location,
        "text": paragraph.text,
        "style": paragraph.style.name if paragraph.style else None,
        "num_id": num_id,
        "ilvl": ilvl,
        "runs": runs,
    }


def main():
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    document = Document(source)
    blocks = []
    colors = Counter()
    paragraphs = 0
    table_count = 0

    for block_index, (kind, block) in enumerate(iter_blocks(document)):
        if kind == "paragraph":
            payload = paragraph_payload(block, f"body:{block_index}")
            if payload["text"].strip():
                blocks.append(payload)
                paragraphs += 1
                for run in payload["runs"]:
                    colors[run["color"] or run["theme_color"] or "auto"] += len(run["text"])
        else:
            table_count += 1
            for row_index, row in enumerate(block.rows):
                for cell_index, cell in enumerate(row.cells):
                    for para_index, paragraph in enumerate(cell.paragraphs):
                        payload = paragraph_payload(
                            paragraph,
                            f"table:{table_count - 1}:row:{row_index}:cell:{cell_index}:p:{para_index}",
                        )
                        if payload["text"].strip():
                            blocks.append(payload)
                            paragraphs += 1
                            for run in payload["runs"]:
                                colors[run["color"] or run["theme_color"] or "auto"] += len(run["text"])

    result = {
        "source": str(source),
        "paragraph_count": paragraphs,
        "table_count": table_count,
        "run_color_character_counts": colors,
        "blocks": blocks,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    red_blocks = []
    for block in blocks:
        red = [run["text"] for run in block["runs"] if (run["color"] or "").upper() in {"FF0000", "C00000", "F00"}]
        if red:
            red_blocks.append({"text": block["text"], "red": red, "location": block["location"]})
    print(json.dumps({
        "paragraph_count": paragraphs,
        "table_count": table_count,
        "colors": colors,
        "red_block_count": len(red_blocks),
        "red_examples": red_blocks[:20],
        "output": str(target),
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
