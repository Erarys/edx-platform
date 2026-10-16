"""List the gettext literals in the new showcase."""
import ast
import re
from pathlib import Path

root = Path(__file__).resolve().parent.parent
messages = set()
paths = list((root / "lms/djangoapps/news/sdg4").glob("*.py"))
paths += list((root / "lms/templates/news/sdg4").glob("*.html"))
paths += [root / "lms/templates/index.html", root / "lms/djangoapps/news/forms.py"]
for path in paths:
    content = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        for node in ast.walk(ast.parse(content)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_" and node.args and isinstance(node.args[0], ast.Constant):
                messages.add(node.args[0].value)
    else:
        for match in re.finditer(r"_\(((?:\"(?:[^\"\\]|\\.)*\")|(?:'(?:[^'\\]|\\.)*'))\)", content):
            messages.add(ast.literal_eval(match.group(1)))
if __name__ == "__main__":
    print("\n".join(sorted(messages)))
