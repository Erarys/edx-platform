from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from prepare_exam import (
    QUESTION_RE, clean_space, paragraphs, parse_choice, parse_matching,
    parse_multiblank_dropdown, parse_text,
)

WEEK_RE = re.compile(r'^\s*(\d{1,2})\s*[-–]\s*апта\b', re.I)


def main():
    if len(sys.argv) != 3:
        raise SystemExit('usage: prepare_weekly.py docs.json output.json')
    doc = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))[0]
    lines = list(paragraphs(doc))
    weekly = {week: [] for week in range(1, 16)}
    current_week = None
    counts = Counter()
    unrecognized = []
    for index, line in enumerate(lines):
        heading = WEEK_RE.match(line)
        if heading:
            current_week = int(heading.group(1))
            if current_week not in weekly:
                raise ValueError(f'Unexpected week {current_week}')
            counts[('headings', current_week)] += 1
            continue
        match = QUESTION_RE.match(line)
        if not match:
            if re.match(r'^\d+[.)]\s', line):
                unrecognized.append((index, line))
            continue
        if current_week is None:
            raise ValueError(f'Question before first week at paragraph {index}')
        body = []
        for following in lines[index + 1:]:
            if QUESTION_RE.match(following) or WEEK_RE.match(following) or re.match(r'^\d+[-–]дәріс', following, re.I):
                break
            body.append(following)
        prompt = clean_space(match.group(2))
        number = int(match.group(1))
        kind = match.group(3).casefold()
        if kind == 'dropdown':
            rows = parse_multiblank_dropdown(prompt, body, number)
        elif kind == 'matching':
            rows = parse_matching(prompt, body, number)
        elif kind == 'single select':
            rows = parse_choice(prompt, body, 'single', number)
        elif kind == 'multiple choice':
            rows = parse_choice(prompt, body, 'multi', number)
        elif kind == 'textinput':
            rows = parse_text(prompt, body, number)
        else:
            raise AssertionError(kind)
        weekly[current_week].extend(rows)
        counts[('questions', current_week)] += 1
    if unrecognized:
        raise ValueError(f'Unrecognized numbered paragraphs: {unrecognized[:10]}')
    if any(counts[('headings', week)] != 1 or not weekly[week] for week in weekly):
        raise ValueError(f'Missing or duplicate week heading: {dict(counts)}')
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(weekly, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({str(week): {
        'questions': counts[('questions', week)],
        'rows': len(weekly[week]),
        'types': dict(Counter(row['type'] for row in weekly[week])),
    } for week in weekly}, ensure_ascii=False))


if __name__ == '__main__':
    main()
