"""Convert extracted CN lab paragraphs into library-tools question rows."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


MARKER = re.compile(r"^\s*(?:Лаб|Даб)(\d+)-(\d+)\s*(.*)$", re.IGNORECASE)
LAB_HEADING = re.compile(r"^\s*Лабораторная\s+\d+\s*$", re.IGNORECASE)
RED = {"EE0000", "FF0000", "C00000", "F00"}
BLANK = re.compile(r"\[\[(.*?)\]\]")


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


def colored_text(block) -> str:
    return clean("".join(
        run["text"] for run in block["runs"]
        if (run.get("color") or "").upper() in RED
    ))


def block_is_red(block) -> bool:
    return bool(colored_text(block).strip(" ,.;:-"))


def blank_details(block):
    full = "".join(run["text"] for run in block["runs"])
    if not full:
        full = block["text"]
    colors = []
    for run in block["runs"]:
        is_red = (run.get("color") or "").upper() in RED
        colors.extend([is_red] * len(run["text"]))
    if len(colors) < len(full):
        colors.extend([False] * (len(full) - len(colors)))

    details = []
    for match in BLANK.finditer(full):
        start, end = match.span(1)
        content = match.group(1)
        red_value = clean("".join(
            full[position]
            for position in range(start, end)
            if colors[position]
        )).strip(" ,")
        if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}\.", red_value):
            red_value = red_value[:-1]
        pieces = []
        cursor = 0
        for raw in content.split(","):
            rel_start = content.find(raw, cursor)
            rel_end = rel_start + len(raw)
            cursor = rel_end + 1
            value = clean(raw)
            if not value:
                continue
            is_correct = any(
                colors[position] and not full[position].isspace() and full[position] not in ",;|[]"
                for position in range(start + rel_start, start + rel_end)
            )
            pieces.append({"value": value, "correct": is_correct})
        correct = [item["value"] for item in pieces if item["correct"]]
        if len(pieces) == 1 and red_value and clean(pieces[0]["value"]) != red_value:
            correct = [red_value]
        elif red_value and correct and not any(clean(value) == red_value for value in correct):
            if any(red_value in clean(value) for value in correct):
                correct = [red_value]
        if not correct:
            if red_value:
                correct = [red_value]
        details.append({
            "choices": [item["value"] for item in pieces],
            "correct": correct,
            "red_text": red_value,
        })
    return details


def groups(blocks):
    result = []
    current = None
    for block in blocks:
        text = clean(block["text"])
        marker = MARKER.match(text)
        if marker:
            current = {
                "lab": int(marker.group(1)),
                "number": int(marker.group(2)),
                "source_label": text,
                "note": marker.group(3).strip(),
                "blocks": [],
            }
            result.append(current)
        elif current is not None and not LAB_HEADING.match(text):
            current["blocks"].append(block)
    return result


def text_row(group, prompt, answer, warning=None):
    return {
        "lab": group["lab"],
        "number": group["number"],
        "source_label": group["source_label"],
        "question": f"Лаб{group['lab']}-{group['number']}. {clean(prompt)}",
        "variants": [],
        "correct_answer": clean(answer),
        "type": "text",
        "warning": warning,
    }


def parse_group(group):
    blocks = [block for block in group["blocks"] if clean(block["text"])]
    if not blocks:
        return text_row(group, "Вопрос отсутствует в исходном документе.", "нет данных", "empty source group")

    note = group["note"].casefold()
    numbered = [block for block in blocks if block["num_id"] is not None]
    blanks = [(block, blank_details(block)) for block in blocks if "[[" in block["text"] and "]]" in block["text"]]

    bracket_overrides = {
        (4, 7): ["192.168.100.0", "192.168.100.1", "192.168.100.30", "192.168.100.31"],
        (4, 9): ["192.168.100.64", "192.168.100.65", "192.168.100.94", "192.168.100.95"],
        (5, 7): ["172.31.1.48", "172.31.1.49", "172.31.1.62", "172.31.1.63"],
        (5, 8): ["172.31.1.64", "172.31.1.65", "172.31.1.78", "172.31.1.79"],
    }
    override = bracket_overrides.get((group["lab"], group["number"]))
    if override:
        prompt = clean(" ".join(block["text"] for block in blocks))
        prompt = re.sub(r"(?<!\[)\[(?!\[)", "[[", prompt)
        prompt = re.sub(r"(?<!\])\](?!\])", "]]", prompt)
        blank_index = 0

        def replace_override(_match):
            nonlocal blank_index
            blank_index += 1
            return f"[{blank_index}]"

        prompt = BLANK.sub(replace_override, prompt)
        prompt += " Введите ответы по порядку через точку с запятой."
        return text_row(group, prompt, "; ".join(override), "source brackets normalized")

    if "соответств" in note:
        pairs = []
        pending = None
        intro = None
        for block in blocks:
            text = clean(block["text"])
            if block["num_id"] is not None:
                pending = text
            elif block_is_red(block) and pending:
                answer = re.sub(r"^Ответ\s+", "", colored_text(block), flags=re.IGNORECASE).strip(" ,")
                pairs.append((pending, answer))
                pending = None
            elif intro is None:
                intro = text
        prompt = (intro or "Установите соответствие.") + " " + " ".join(
            f"{index}) {left}" for index, (left, _) in enumerate(pairs, 1)
        )
        answer = "; ".join(f"{index}) {right}" for index, (_, right) in enumerate(pairs, 1))
        return text_row(group, prompt, answer, "matching converted to text response")

    if blanks:
        block, details = blanks[0]
        prompt = clean(block["text"])
        if len(details) == 1 and len(details[0]["choices"]) >= 2 and len(details[0]["correct"]) == 1:
            choices = details[0]["choices"]
            correct_value = details[0]["correct"][0]
            try:
                correct_index = choices.index(correct_value) + 1
            except ValueError:
                correct_index = next(
                    (index for index, value in enumerate(choices, 1) if clean(value) == clean(correct_value)),
                    None,
                )
            if correct_index:
                prompt = BLANK.sub("_____", prompt)
                return {
                    "lab": group["lab"],
                    "number": group["number"],
                    "source_label": group["source_label"],
                    "question": f"Лаб{group['lab']}-{group['number']}. {prompt}",
                    "variants": choices,
                    "correct_answer": str(correct_index),
                    "type": "dropdown",
                    "warning": None,
                }

        numbered_blanks = []
        answers = []
        all_details = []
        for source_block, source_details in blanks:
            all_details.extend(source_details)
        for index, detail in enumerate(all_details, 1):
            numbered_blanks.append(f"[{index}] варианты: {', '.join(detail['choices'])}")
            answers.append(" / ".join(detail["correct"]) or detail.get("red_text", ""))
        replaced_index = 0

        def replace_blank(_match):
            nonlocal replaced_index
            replaced_index += 1
            return f"[{replaced_index}]"

        prompt = BLANK.sub(replace_blank, prompt)
        if numbered_blanks:
            prompt += " " + " ".join(numbered_blanks) + " Введите ответы по порядку через точку с запятой."
        answer = "; ".join(value for value in answers if value)
        if not answer:
            answer = "; ".join(colored_text(source_block).strip(" ,") for source_block, _ in blanks if colored_text(source_block))
        return text_row(group, prompt, answer, "multiple blanks converted to text response")

    if numbered:
        first_numbered = next(index for index, block in enumerate(blocks) if block["num_id"] is not None)
        prompt_parts = [clean(block["text"]) for block in blocks[:first_numbered] if clean(block["text"])]
        options = [clean(block["text"]) for block in blocks[first_numbered:] if block["num_id"] is not None]
        option_blocks = [block for block in blocks[first_numbered:] if block["num_id"] is not None]
        correct = [index for index, block in enumerate(option_blocks, 1) if block_is_red(block)]
        prompt = " ".join(prompt_parts) or clean(blocks[0]["text"])
        if len(options) >= 2 and correct:
            return {
                "lab": group["lab"],
                "number": group["number"],
                "source_label": group["source_label"],
                "question": f"Лаб{group['lab']}-{group['number']}. {prompt}",
                "variants": options,
                "correct_answer": ",".join(map(str, correct)),
                "type": "multi" if len(correct) > 1 else "single",
                "warning": None,
            }

        red_answers = [colored_text(block).strip(" ,") for block in option_blocks if block_is_red(block)]
        answer = " || ".join(value for value in red_answers if value) or "верно"
        if re.fullmatch(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:\s*(?:±|\+/-)\s*\d+(?:[.,]\d*)?%?)?", answer):
            row = text_row(group, prompt, answer, None)
            row["type"] = "numeric"
            return row
        warning = "image options converted to text response" if len(options) < 2 else "selection converted to text response"
        if "изображ" not in prompt.casefold() and "значок" in prompt.casefold():
            prompt += " В исходном документе варианты ответа представлены изображениями."
        return text_row(group, prompt, answer, warning)

    red = [colored_text(block).strip(" ,") for block in blocks if block_is_red(block)]
    red = [value for value in red if value]
    prompt = clean(" ".join(block["text"] for block in blocks))
    return text_row(group, prompt, " || ".join(dict.fromkeys(red)), "free response derived from red text")


def main():
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    data = json.loads(source.read_text(encoding="utf-8"))
    parsed_groups = groups(data["blocks"])
    rows = [parse_group(group) for group in parsed_groups]
    rows.sort(key=lambda item: (item["lab"], item["number"]))

    per_lab = defaultdict(list)
    for row in rows:
        per_lab[row["lab"]].append(row)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({str(lab): items for lab, items in per_lab.items()}, ensure_ascii=False, indent=2), encoding="utf-8")

    issues = []
    for lab in range(1, 16):
        numbers = [row["number"] for row in per_lab[lab]]
        missing = sorted(set(range(1, 11)) - set(numbers))
        duplicate = sorted(number for number, count in Counter(numbers).items() if count > 1)
        if missing or duplicate:
            issues.append({"lab": lab, "missing": missing, "duplicate": duplicate})
    print(json.dumps({
        "rows": len(rows),
        "per_lab": {str(lab): len(items) for lab, items in per_lab.items()},
        "types": Counter(row["type"] for row in rows),
        "warnings": Counter(row["warning"] for row in rows if row["warning"]),
        "sequence_issues": issues,
        "invalid": [row["source_label"] for row in rows if not row["question"] or not row["correct_answer"]],
        "output": str(target),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
