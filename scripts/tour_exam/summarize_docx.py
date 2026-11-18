from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path


def flat_paragraphs(doc):
    for i, block in enumerate(doc["blocks"]):
        if block["kind"] == "paragraph":
            yield i, block
        else:
            for r, row in enumerate(block["rows"]):
                for c, cell in enumerate(row):
                    for p in cell:
                        yield f"{i}/t{r},{c}", p


def special_runs(p):
    out = []
    for r in p["runs"]:
        if not r["text"].strip():
            continue
        marks = []
        if r["bold"]:
            marks.append("B")
        if r["italic"]:
            marks.append("I")
        if r["underline"]:
            marks.append("U")
        if r["color"]:
            marks.append("C=" + r["color"])
        if r["highlight"]:
            marks.append("H=" + r["highlight"])
        if marks:
            out.append(f"<{','.join(marks)}:{r['text']}>")
    return " ".join(out)


def main():
    docs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 160
    for doc in docs:
        print("\n===", Path(doc["path"]).name, "===")
        styles = Counter()
        colors = Counter()
        highlights = Counter()
        numbered = 0
        items = []
        for idx, p in flat_paragraphs(doc):
            styles[p["style"]] += 1
            if p["numbering"]:
                numbered += 1
            for r in p["runs"]:
                if r["color"]:
                    colors[r["color"]] += 1
                if r["highlight"]:
                    highlights[r["highlight"]] += 1
            if p["text"].strip():
                items.append((idx, p))
        print("nonempty", len(items), "numbered", numbered)
        print("styles", styles.most_common(12))
        print("colors", colors.most_common(12), "highlights", highlights.most_common(12))
        for idx, p in items[:limit]:
            text = re.sub(r"\s+", " ", p["text"]).strip()
            marker = special_runs(p)
            print(f"[{idx}] style={p['style']!r} num={p['numbering']} :: {text}")
            if marker:
                print("    RUNS", marker)


if __name__ == "__main__":
    main()
