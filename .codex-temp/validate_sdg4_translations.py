"""Validate all showcase messages and compile test-only MO catalogues."""
import gettext
import io
import re
from pathlib import Path
from string import Formatter

import polib

from sdg4_messages import messages

root = Path(__file__).resolve().parent
source = Path(r"C:\Users\Erarys\Desktop\openedx-translations\translations\edx-platform\conf\locale")
for language in ("en", "ru", "kk"):
    original = (source / language / "LC_MESSAGES/django.po").read_text(encoding="utf-8")
    header = re.search(r'(?m)^(?:#, fuzzy\n)?msgid ""\nmsgstr ""\n(?:"[^\n]*"\n)*', original)
    normalized = header.group() + "\n" + original[:header.start()] + original[header.end():] if header else original
    normalized = re.sub(r"(?m)^#(?=[^ .,:~|!\n])", "# ", normalized)
    catalogue = polib.pofile(normalized)
    selected = polib.POFile()
    selected.metadata = {"Content-Type": "text/plain; charset=UTF-8", "Language": language}
    for message in sorted(messages):
        matches = [entry for entry in catalogue if entry.msgid == message and not entry.msgctxt and not entry.obsolete]
        assert len(matches) == 1, (language, message, len(matches))
        entry = matches[0]
        assert language == "en" or (entry.msgstr and "fuzzy" not in entry.flags), (language, message)
        expected = {name for _literal, name, _spec, _conversion in Formatter().parse(message) if name}
        actual = {name for _literal, name, _spec, _conversion in Formatter().parse(entry.msgstr or message) if name}
        assert actual == expected, (language, message, actual, expected)
        selected.append(polib.POEntry(msgid=message, msgstr=entry.msgstr or message))
    contextual = catalogue.find("Faculties", msgctxt="SDG 4 showcase")
    assert contextual and contextual.msgstr == {"en": "Faculties", "ru": "Факультеты", "kk": "Факультеттер"}[language]
    selected.append(contextual)
    destination = root / "sdg4-locales" / language / "LC_MESSAGES" / "django.mo"
    destination.parent.mkdir(parents=True, exist_ok=True)
    selected.save_as_mofile(str(destination))
    translated = gettext.GNUTranslations(io.BytesIO(selected.to_binary()))
    assert translated.gettext("SDG 4: Quality Education") != "SDG 4: Quality Education" or language == "en"
    print(language, len(selected), "messages verified; MO load successful")
