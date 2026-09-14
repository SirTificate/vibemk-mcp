"""
Guard against escaped newlines reaching the user.

Response text is assembled from f-strings, and three handler modules wrote
``\\n`` where they meant ``\n``. That is a backslash followed by the letter
n: the user reads a literal "\n" in the chat instead of a line break, and
every affected answer arrives as one unbroken run of text.

Nothing caught it, because no test asserted on the shape of a response and
the handlers returned "success" either way. This checks the class rather
than the instances: the sequence has no legitimate use anywhere in this
codebase, so its presence is always the bug.
"""

from pathlib import Path
from typing import List

SOURCE_DIRECTORIES = ("handlers", "mcp", "api", "config")

# Three characters as they appear on disk: backslash, backslash, "n". Python
# renders that as a backslash followed by n — not a newline.
ESCAPED_NEWLINE = r"\\n"

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def modules() -> List[Path]:
    found = [path for directory in SOURCE_DIRECTORIES for path in (PROJECT_ROOT / directory).rglob("*.py")]
    assert found, "no source modules were found — the paths in SOURCE_DIRECTORIES are wrong"
    return found


def test_no_module_writes_an_escaped_newline() -> None:
    offenders = []
    for path in modules():
        text = path.read_text(encoding="utf-8")
        count = text.count(ESCAPED_NEWLINE)
        if count:
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {count}")

    assert not offenders, "these modules print a literal backslash-n instead of a line break: " + ", ".join(offenders)
