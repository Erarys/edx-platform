"""Generate narrow apply_patch edits; preserve existing PO formatting and translations."""
import argparse
import json
import re
from pathlib import Path

import polib

from sdg4_messages import messages

parser = argparse.ArgumentParser()
parser.add_argument("language", choices=["ru", "kk", "en"])
args = parser.parse_args()
records = json.loads(Path(__file__).with_name("sdg4_translations.json").read_text(encoding="utf-8"))
records += json.loads(Path(__file__).with_name("sdg4_simple_translations.json").read_text(encoding="utf-8"))
mapping = {record[0]: record[{"en": 0, "ru": 1, "kk": 2}[args.language]] for record in records}
assert not messages - mapping.keys(), f"Missing translations: {messages - mapping.keys()}"
target = Path(r"C:\Users\Erarys\Desktop\openedx-translations\translations\edx-platform\conf\locale") / args.language / "LC_MESSAGES/django.po"
original = target.read_text(encoding="utf-8")
# These existing catalogues place their metadata after custom translations.
# Move it only in the parser input; do not reorder the user's source files.
header = re.search(r'(?m)^(?:#, fuzzy\n)?msgid ""\nmsgstr ""\n(?:"[^\n]*"\n)*', original)
normalized = header.group() + "\n" + original[:header.start()] + original[header.end():] if header else original
normalized = re.sub(r"(?m)^#(?=[^ .,:~|!\n])", "# ", normalized)
catalogue = polib.pofile(normalized)
new = []
edits = []
for msgid in sorted(messages):
    existing = catalogue.find(msgid)
    if existing and args.language == "en":
        continue
    if existing and existing.msgstr and "fuzzy" not in existing.flags:
        continue
    entry = polib.POEntry(msgid=msgid, msgstr=mapping[msgid], occurrences=[("lms/djangoapps/news/sdg4", "")])
    if existing:
        old = str(existing).rstrip("\n")
        # Use the original lines, including the entry's original wrap style.
        marker = "msgid " + json.dumps(msgid, ensure_ascii=False)
        if marker in original and 'msgstr ""' in original.split(marker, 1)[1].split("\n\n", 1)[0]:
            body = original.split(marker, 1)[1].split("\n\n", 1)[0]
            old = marker + body
            replacement = 'msgid ' + json.dumps(msgid, ensure_ascii=False) + '\nmsgstr ' + json.dumps(mapping[msgid], ensure_ascii=False)
            edits.append("@@\n" + "\n".join("-" + line for line in old.splitlines()) + "\n" + "\n".join("+" + line for line in replacement.splitlines()))
        else:
            raise RuntimeError(f"Existing entry requires manual review: {msgid}")
    else:
        new.append(str(entry).rstrip("\n"))
print("*** Begin Patch")
print(f"*** Update File: {target.as_posix()}")
for edit in edits:
    print(edit)
if new:
    print("@@")
    for line in original.rstrip("\n").splitlines()[-4:]:
        print(" " + line)
    print("+\n+" + "\n+".join("\n\n".join(new).splitlines()))
print("*** End Patch")
