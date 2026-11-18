from __future__ import annotations

import json
import re
import sys
from pathlib import Path


QUESTION_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*\((Dropdown|Matching|Single Select|multiple choice|TextInput)\)\s*$", re.I | re.S)
SECTION_RE = re.compile(r"^(?:Емтихан сұрақтары\.|E\d+:\s*\d+\s+сұрақ)", re.I)
BLANK_RE = re.compile(r"^\[(\d+)\]\s*бос орын\s*:\s*$", re.I)


def paragraphs(doc):
    for block in doc["blocks"]:
        if block["kind"] == "paragraph":
            text = block["text"].strip()
            if text:
                yield text
        else:
            for row in block["rows"]:
                for cell in row:
                    for p in cell:
                        text = p["text"].strip()
                        if text:
                            yield text


def clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


def choice_text(value: str) -> tuple[str, bool]:
    value = clean_space(value)
    if not value.startswith("-"):
        raise ValueError(f"ожидался вариант с '-': {value}")
    value = value[1:].strip()
    correct = bool(re.search(r"\s+\+$", value))
    if correct:
        value = re.sub(r"\s+\+$", "", value).strip()
    return value, correct


def numbered_item(value: str, marker: str) -> tuple[int, str]:
    pattern = r"^" + re.escape(marker) + r"\s*(\d+)\)\s*(.+)$"
    match = re.match(pattern, clean_space(value))
    if not match:
        raise ValueError(f"ожидалась нумерованная пара '{marker} 1)': {value}")
    return int(match.group(1)), match.group(2).strip()


def make_row(question: str, answers: list[str], correct: str, kind: str, source_number: int):
    if len(answers) > 10:
        raise ValueError(f"вопрос {source_number}: больше 10 вариантов")
    return {
        "question": clean_space(question),
        "answers": [clean_space(answer) for answer in answers],
        "correct_answer": correct,
        "type": kind,
        "source_number": source_number,
    }


def parse_choice(prompt: str, body: list[str], kind: str, source_number: int):
    options = [choice_text(line) for line in body if line.startswith("-")]
    if len(options) < 2:
        raise ValueError(f"вопрос {source_number}: меньше двух вариантов")
    correct = [str(i) for i, (_, marked) in enumerate(options, 1) if marked]
    if not correct:
        raise ValueError(f"вопрос {source_number}: нет правильного ответа")
    if kind in {"single", "dropdown"} and len(correct) != 1:
        raise ValueError(f"вопрос {source_number}: тип {kind}, но правильных ответов {len(correct)}")
    return [make_row(prompt, [text for text, _ in options], ",".join(correct), kind, source_number)]


def parse_text(prompt: str, body: list[str], source_number: int):
    answers = []
    for line in body:
        if not line.startswith("-"):
            continue
        answer, marked = choice_text(line)
        if marked:
            answers.append(answer)
    if not answers:
        raise ValueError(f"вопрос {source_number}: нет допустимого текстового ответа")
    return [make_row(prompt, [], " || ".join(answers), "text", source_number)]


def parse_matching(prompt: str, body: list[str], source_number: int):
    left = dict(numbered_item(line, "-") for line in body if line.startswith("-"))
    right = dict(numbered_item(line, "+") for line in body if line.startswith("+"))
    if set(left) != set(right) or len(left) < 2:
        raise ValueError(f"вопрос {source_number}: неполные пары matching")
    indexes = sorted(left)
    if indexes != list(range(1, len(indexes) + 1)):
        raise ValueError(f"вопрос {source_number}: номера пар должны идти подряд")
    return [make_row(prompt, [left[i] for i in indexes], " || ".join(right[i] for i in indexes), "matching", source_number)]


def parse_multiblank_dropdown(prompt: str, body: list[str], source_number: int):
    groups = []
    current = None
    for line in body:
        marker = BLANK_RE.match(line)
        if marker:
            current = {"number": int(marker.group(1)), "lines": []}
            groups.append(current)
        elif current is not None and line.startswith("-"):
            current["lines"].append(line)
    if not groups:
        return parse_choice(prompt, body, "dropdown", source_number)
    rows = []
    for group in groups:
        label = f"{prompt} [{group['number']}] бос орын"
        rows.extend(parse_choice(label, group["lines"], "dropdown", source_number))
    return rows


def parse_exam(doc):
    lines = list(paragraphs(doc))
    rows = []
    originals = 0
    for index, line in enumerate(lines):
        match = QUESTION_RE.match(line)
        if not match:
            continue
        originals += 1
        source_number = int(match.group(1))
        prompt = clean_space(match.group(2))
        body = []
        for following in lines[index + 1:]:
            if QUESTION_RE.match(following) or SECTION_RE.match(following):
                break
            body.append(following)
        kind = match.group(3).casefold()
        if kind == "dropdown":
            rows.extend(parse_multiblank_dropdown(prompt, body, source_number))
        elif kind == "matching":
            rows.extend(parse_matching(prompt, body, source_number))
        elif kind == "single select":
            rows.extend(parse_choice(prompt, body, "single", source_number))
        elif kind == "multiple choice":
            rows.extend(parse_choice(prompt, body, "multi", source_number))
        elif kind == "textinput":
            rows.extend(parse_text(prompt, body, source_number))
        else:
            raise AssertionError(kind)
    return originals, rows


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: prepare_exam.py docs.json output.json")
    docs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    originals, rows = parse_exam(docs[-1])
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {}
    for row in rows:
        counts[row["type"]] = counts.get(row["type"], 0) + 1
    print(json.dumps({"original_questions": originals, "output_rows": len(rows), "types": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
