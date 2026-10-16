import sys
from pathlib import Path

language = sys.argv[1]
target = Path(r"C:\Users\Erarys\Desktop\openedx-translations\translations\edx-platform\conf\locale") / language / "LC_MESSAGES/django.po"
lines = target.read_text(encoding="utf-8").rstrip("\n").splitlines()
translation = {"en": "Faculties", "ru": "Факультеты", "kk": "Факультеттер"}[language]
print("*** Begin Patch")
print(f"*** Update File: {target.as_posix()}")
print("@@")
for line in lines[-4:]:
    print(" " + line)
print('+\n+#: lms/templates/news/sdg4/index.html lms/templates/news/sdg4/faculty.html lms/templates/news/sdg4/teacher.html')
print('+msgctxt "SDG 4 showcase"\n+msgid "Faculties"\n+msgstr "' + translation + '"')
print("*** End Patch")
