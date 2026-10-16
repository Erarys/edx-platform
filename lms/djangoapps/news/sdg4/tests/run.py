"""Run SDG 4 tests without initializing the LMS Celery application.

The real Django ORM, middleware, forms, storage and showcase templates are used.
Only the platform shell/Celery bootstrap is excluded from this isolated suite.
"""

import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
package = ModuleType("lms")
package.__path__ = [str(ROOT / "lms")]
package.__file__ = str(ROOT / "lms" / "__init__.py")
sys.modules["lms"] = package

from django.core.management import execute_from_command_line  # noqa: E402

if __name__ == "__main__":
    execute_from_command_line(
        [
            sys.argv[0],
            "test",
            "lms.djangoapps.news.sdg4.tests",
            "--settings=lms.djangoapps.news.sdg4.tests.settings",
            *sys.argv[1:],
        ]
    )
