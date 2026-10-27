#!/usr/bin/env python
# ruff: noqa: I001
"""Create a Coursera-compatible copy of an Open edX OLX archive."""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from openedx.core.coursera_olx import prepare_coursera_olx


def _default_output(input_path):
    name = input_path.name.removesuffix('.tar.gz')
    return input_path.with_name(f'{name}.coursera.tar.gz')


def _safe_extract(archive, destination):
    destination = destination.resolve()
    for member in archive.getmembers():
        member_path = (destination / member.name).resolve()
        if destination not in member_path.parents and member_path != destination:
            raise ValueError(f'Unsafe archive member: {member.name}')
    archive.extractall(destination, filter='data')


def _find_course_directory(destination):
    candidates = [path.parent for path in destination.rglob('course.xml')]
    if len(candidates) != 1:
        raise ValueError(f'Expected one course.xml, found {len(candidates)}')
    return candidates[0]


def convert_archive(input_path, output_path):
    with tempfile.TemporaryDirectory(prefix='coursera-olx-') as temp_name:
        temp_dir = Path(temp_name)
        with tarfile.open(input_path, 'r:*') as archive:
            _safe_extract(archive, temp_dir)
        course_dir = _find_course_directory(temp_dir)
        report = prepare_coursera_olx(course_dir)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output_path, 'w:gz') as archive:
            archive.add(course_dir, arcname=course_dir.name)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Open edX .tar.gz archive')
    parser.add_argument('-o', '--output', type=Path, help='Destination .tar.gz archive')
    args = parser.parse_args()
    output = args.output or _default_output(args.input)
    report = convert_archive(args.input.resolve(), output.resolve())
    print(json.dumps({'output': str(output.resolve()), **report.to_dict()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
