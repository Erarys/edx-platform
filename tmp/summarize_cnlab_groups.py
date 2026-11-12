from __future__ import annotations

import json
import re
import sys
from pathlib import Path


MARKER = re.compile(r"^\s*Лаб(\d+)-(\d+)\s*(.*)$", re.IGNORECASE)


def main():
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    payload = json.loads(source.read_text(encoding="utf-8"))
    groups = []
    current = None
    for block in payload["blocks"]:
        match = MARKER.match(block["text"].strip())
        if match:
            current = {
                "lab": int(match.group(1)),
                "number": int(match.group(2)),
                "label": block["text"].strip(),
                "note": match.group(3).strip(),
                "blocks": [],
            }
            groups.append(current)
        elif current is not None and not re.match(r"^\s*Лабораторная\s+\d+\s*$", block["text"], re.IGNORECASE):
            copied = dict(block)
            copied["red_text"] = "".join(
                run["text"] for run in block["runs"]
                if (run.get("color") or "").upper() in {"EE0000", "FF0000", "C00000", "F00"}
            )
            copied["is_red"] = bool(copied["red_text"])
            current["blocks"].append(copied)

    lines = []
    for group in groups:
        lines.append(f"### {group['label']}")
        for block in group["blocks"]:
            flags = [f"style={block['style']}"]
            if block["num_id"] is not None:
                flags.append(f"num={block['num_id']}:{block['ilvl']}")
            if block["is_red"]:
                flags.append(f"RED={block['red_text']!r}")
            lines.append(f"[{', '.join(flags)}] {block['text']}")
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    labs = sorted({group["lab"] for group in groups})
    print(json.dumps({
        "groups": len(groups),
        "labs": labs,
        "per_lab": {str(lab): sum(group["lab"] == lab for group in groups) for lab in labs},
        "groups_without_red": [group["label"] for group in groups if not any(block["is_red"] for block in group["blocks"])],
        "output": str(target),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
