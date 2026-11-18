from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path


QUESTION_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*\(([^()]*)\)\s*$", re.S)


def paragraphs(doc):
    for block in doc["blocks"]:
        if block["kind"] == "paragraph":
            yield block["text"].strip()
        else:
            for row in block["rows"]:
                for cell in row:
                    for p in cell:
                        yield p["text"].strip()


def main():
    docs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    doc = docs[-1]
    lines = [x for x in paragraphs(doc) if x]
    counts = Counter()
    headings = []
    questions = []
    for i, line in enumerate(lines):
        m = QUESTION_RE.match(line)
        if m:
            counts[m.group(3).strip().casefold()] += 1
            questions.append((i, int(m.group(1)), m.group(2), m.group(3).strip()))
        elif "санат" in line.casefold() or re.match(r"^[A-ZА-Я]\d+:\s*\d+\s", line):
            headings.append((i, line))
    print("lines", len(lines), "questions", len(questions))
    print("types", dict(counts))
    print("headings")
    for item in headings:
        print(item)
    print("number sequence / block counts")
    last = None
    for i, n, text, kind in questions:
        if last is None or n <= last:
            print(f"block start line={i} n={n} type={kind} text={text[:80]}")
        last = n
    print("option marker counts", Counter(line[:1] for line in lines if line[:1] in "+-"))
    print("sublabels", [line for line in lines if re.match(r"^\[\d+\]", line)])


if __name__ == "__main__":
    main()
