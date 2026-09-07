"""
Optional .env file support for vibeMK

Copyright (C) 2024 Andre <andre@example.com>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
"""

import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _package_root() -> Path:
    """Directory holding main.py when vibeMK runs from a checkout."""
    return Path(__file__).resolve().parent.parent


def _candidates() -> list:
    """Where to look for a .env, most specific first.

    The working directory comes first so a pip-installed vibeMK finds the
    operator's file rather than one that happens to sit in site-packages.
    """
    return [Path.cwd() / ".env", _package_root() / ".env"]


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing ` # comment` from an unquoted value.

    A '#' that is not preceded by whitespace belongs to the value, so
    passwords like 'abc#123' survive.
    """
    for index, char in enumerate(value):
        if char == "#" and index > 0 and value[index - 1] in " \t":
            return value[:index]
    return value


def _unquote(value: str) -> str:
    """Remove a matching pair of surrounding quotes, if that is what they are.

    Requires the quote character to occur exactly twice, so a value such as
    '"a"b"' is left intact instead of losing two characters.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        if value.count(value[0]) == 2:
            return value[1:-1]
    return value


def parse_dotenv(text: str) -> Dict[str, str]:
    """Parse .env content into a mapping, skipping anything unparseable."""
    values: Dict[str, str] = {}

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export ") :].lstrip()

        key, separator, value = line.partition("=")
        if not separator:
            logger.debug("Ignoring .env line %d: no '=' found", number)
            continue

        key = key.strip()
        if not key.isidentifier():
            logger.debug("Ignoring .env line %d: %r is not a valid variable name", number, key)
            continue

        value = value.strip()
        quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
        if not quoted:
            value = _strip_inline_comment(value).strip()
        values[key] = _unquote(value)

    return values


def load_dotenv(path: Optional[Path] = None) -> Optional[Path]:
    """Load a .env file into os.environ and return the file that was used.

    Variables already present in the environment always win, including ones
    deliberately set to an empty string. Returns None when no file was found
    or the file could not be read.
    """
    paths = [Path(path)] if path is not None else _candidates()

    for candidate in paths:
        if not candidate.is_file():
            continue
        try:
            # utf-8-sig transparently drops the BOM that Windows editors add;
            # replacing undecodable bytes beats refusing to start.
            text = candidate.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as error:
            logger.warning("Could not read %s: %s", candidate, error)
            return None

        applied = 0
        for key, value in parse_dotenv(text).items():
            if key in os.environ:
                logger.debug("Keeping environment value for %s over %s", key, candidate)
                continue
            os.environ[key] = value
            applied += 1

        logger.info("Loaded %d variable(s) from %s", applied, candidate)
        return candidate

    logger.debug("No .env file found in %s", ", ".join(str(p) for p in paths))
    return None
