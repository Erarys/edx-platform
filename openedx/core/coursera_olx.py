"""Post-process Open edX OLX exports for Coursera course import."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree


class CourseraOlxConversionError(ValueError):
    """Raised when an OLX archive cannot be converted without losing a PDF."""


@dataclass
class CourseraOlxConversionReport:
    """Summary of changes made to one extracted OLX course directory."""

    converted_pdf_blocks: int = 0
    local_pdf_assets: int = 0
    external_pdf_links: int = 0
    removed_pdf_advanced_modules: int = 0

    def to_dict(self):
        return asdict(self)


def _local_name(tag):
    """Return an XML tag without an optional namespace."""
    return tag.rsplit('}', 1)[-1]


def _read_asset_policy(course_dir):
    policy_path = course_dir / 'policies' / 'assets.json'
    if not policy_path.is_file():
        return {}
    with policy_path.open(encoding='utf-8') as policy_file:
        return json.load(policy_file)


def _asset_key_from_href(href):
    marker = 'block@'
    if marker not in href:
        return None
    return unquote(href.rsplit(marker, 1)[-1].split('?', 1)[0].split('#', 1)[0])


def _local_asset_path(course_dir, href, assets):
    static_dir = (course_dir / 'static').resolve()

    def existing_static_file(candidate):
        candidate = candidate.resolve()
        if static_dir not in candidate.parents or not candidate.is_file():
            return None
        return candidate

    parsed = urlparse(href)
    if parsed.path.startswith('/static/'):
        relative_path = unquote(parsed.path[len('/static/'):]).replace('\\', '/')
        candidate = existing_static_file(static_dir / Path(relative_path))
        if candidate is not None:
            return candidate

    asset_key = _asset_key_from_href(href)
    if not asset_key:
        return None
    metadata = assets.get(asset_key, {})
    display_name = metadata.get('displayname')
    import_path = metadata.get('import_path')
    candidates = []
    if display_name:
        candidates.append(static_dir / Path(import_path or '').parent / display_name)
    candidates.append(static_dir / asset_key)
    return next((candidate for candidate in map(existing_static_file, candidates) if candidate is not None), None)


def _coursera_href(course_dir, href, assets):
    asset_path = _local_asset_path(course_dir, href, assets)
    if asset_path is None:
        if urlparse(href).scheme in {'http', 'https'}:
            return href, False
        raise CourseraOlxConversionError(f'PDF asset is missing from the OLX export: {href}')

    relative_path = asset_path.relative_to(course_dir / 'static').as_posix()
    return '/static/' + quote(relative_path, safe='/'), True


def _write_reading(course_dir, block_id, display_name, href):
    html_dir = course_dir / 'html'
    html_dir.mkdir(exist_ok=True)

    descriptor = ElementTree.Element(
        'html',
        {
            'filename': block_id,
            'display_name': display_name,
        },
    )
    ElementTree.ElementTree(descriptor).write(
        html_dir / f'{block_id}.xml',
        encoding='utf-8',
        xml_declaration=False,
    )

    safe_name = escape(display_name)
    safe_href = escape(href, quote=True)
    body = (
        f'<p><strong>{safe_name}</strong></p>\n'
        f'<p><a href="{safe_href}" type="application/pdf" target="_blank" rel="noopener">'
        f'{safe_name} (PDF)</a></p>\n'
    )
    (html_dir / f'{block_id}.html').write_text(body, encoding='utf-8')


def _pdf_definition(course_dir, element):
    """Resolve an inline PDF block or an OLX pointer to a PDF descriptor."""
    if element.get('href'):
        return element, None

    block_id = element.get('url_name')
    descriptor_path = course_dir / 'pdf' / f'{block_id}.xml'
    if not block_id or not descriptor_path.is_file():
        return element, None
    return ElementTree.parse(descriptor_path).getroot(), descriptor_path


def _convert_pdf_elements(course_dir, assets, report):
    source_files = [
        path for path in course_dir.rglob('*.xml')
        if path.parent.name not in {'html', 'assets'}
    ]
    converted_ids = set()
    removable_descriptors = set()

    for xml_path in source_files:
        tree = ElementTree.parse(xml_path)
        root = tree.getroot()
        changed = False
        for parent in root.iter():
            for position, child in enumerate(list(parent)):
                if _local_name(child.tag) != 'pdf':
                    continue

                definition, descriptor_path = _pdf_definition(course_dir, child)
                block_id = child.get('url_name') or definition.get('url_name')
                href = definition.get('href')
                if not block_id or not href:
                    raise CourseraOlxConversionError(
                        f'PDF block in {xml_path} has no url_name or href'
                    )

                display_name = definition.get('display_name') or child.get('display_name') or 'PDF'
                if display_name.strip().lower() in {'pdf', 'pdf file'}:
                    display_name = parent.get('display_name') or display_name
                target_href, is_local = _coursera_href(course_dir, href, assets)
                if block_id not in converted_ids:
                    _write_reading(course_dir, block_id, display_name, target_href)
                    converted_ids.add(block_id)
                    if is_local:
                        report.local_pdf_assets += 1
                    else:
                        report.external_pdf_links += 1

                replacement = ElementTree.Element('html', {'url_name': block_id})
                replacement.tail = child.tail
                parent.remove(child)
                parent.insert(position, replacement)
                report.converted_pdf_blocks += 1
                changed = True
                if descriptor_path is not None:
                    removable_descriptors.add(descriptor_path)

        if changed:
            tree.write(xml_path, encoding='utf-8', xml_declaration=False)

    for descriptor_path in removable_descriptors:
        descriptor_path.unlink(missing_ok=True)
    pdf_dir = course_dir / 'pdf'
    if pdf_dir.is_dir() and not any(pdf_dir.iterdir()):
        pdf_dir.rmdir()


def _remove_pdf_advanced_module(course_dir, report):
    course_pointer = ElementTree.parse(course_dir / 'course.xml').getroot()
    run_name = course_pointer.get('url_name')
    if not run_name:
        return
    run_path = course_dir / 'course' / f'{run_name}.xml'
    if not run_path.is_file():
        return

    tree = ElementTree.parse(run_path)
    root = tree.getroot()
    raw_modules = root.get('advanced_modules')
    if not raw_modules:
        return
    try:
        modules = json.loads(raw_modules)
    except json.JSONDecodeError:
        return
    filtered = [module for module in modules if module != 'pdf']
    if filtered == modules:
        return
    root.set('advanced_modules', json.dumps(filtered))
    tree.write(run_path, encoding='utf-8', xml_declaration=False)
    report.removed_pdf_advanced_modules = len(modules) - len(filtered)


def prepare_coursera_olx(course_directory):
    """Convert unsupported PDF XBlocks in an extracted course to HTML readings.

    The conversion is deliberately limited to PDF blocks. Other components keep
    their native OLX representation so Coursera can apply its own importer rules.
    """
    course_dir = Path(os.fspath(course_directory))
    if not (course_dir / 'course.xml').is_file():
        raise CourseraOlxConversionError(f'course.xml was not found in {course_dir}')

    report = CourseraOlxConversionReport()
    assets = _read_asset_policy(course_dir)
    _convert_pdf_elements(course_dir, assets, report)
    _remove_pdf_advanced_module(course_dir, report)
    return report
