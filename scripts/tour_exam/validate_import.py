from __future__ import annotations

import importlib.util
import json
import sys
import types
import xml.etree.ElementTree as stdlib_et
from collections import Counter
from pathlib import Path


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: validate_import.py repo_root workbook.xlsx [workbook.xlsx ...]")
    root = Path(sys.argv[1])
    package = types.ModuleType("library_tools_standalone")
    package.__path__ = []
    sys.modules[package.__name__] = package
    try:
        import defusedxml  # noqa: F401
    except ModuleNotFoundError:
        defused_package = types.ModuleType("defusedxml")
        defused_package.__path__ = []
        defused_element = types.ModuleType("defusedxml.ElementTree")
        defused_element.fromstring = lambda value, **_kwargs: stdlib_et.fromstring(value)
        defused_common = types.ModuleType("defusedxml.common")
        defused_common.DefusedXmlException = ValueError
        sys.modules["defusedxml"] = defused_package
        sys.modules["defusedxml.ElementTree"] = defused_element
        sys.modules["defusedxml.common"] = defused_common
    base = root / "lms" / "djangoapps" / "library_tools"
    load(package.__name__ + ".questions", base / "questions.py")
    excel = load(package.__name__ + ".excel", base / "excel.py")
    for filename in sys.argv[2:]:
        workbook = Path(filename)
        result = excel.parse_xlsx(workbook.read_bytes())
        print(json.dumps({
            "file": workbook.name,
            "questions": len(result.questions),
            "errors": result.errors,
            "warnings": result.warnings,
            "types": dict(Counter(question.type for question in result.questions)),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
