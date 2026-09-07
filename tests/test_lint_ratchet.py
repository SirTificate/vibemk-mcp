"""
Keeps the type-checking and linting exception lists one-way.

Both tools run in CI at their configured strictness. Modules that do not pass
yet are listed explicitly in pyproject.toml. This test fails when an entry is
no longer needed, so the list has to shrink rather than quietly persist.

Checking a listed module (or file) straight against the real pyproject.toml
would be circular: mypy's ignore_errors = true and ruff's per-file-ignores
both suppress the exact findings being tested for, so every entry would look
"no longer needed" even when it still is. Both checks below run against a
temporary copy of pyproject.toml with only the relevant exception block
removed, so every other setting (strict mode, rule selection, the tests.*
override, ...) still applies and the result reflects the module's true state.
"""

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import List

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

MYPY_OVERRIDE_RE = re.compile(
    r"\[\[tool\.mypy\.overrides\]\]\s*\nmodule\s*=\s*\[(.*?)\]\s*\nignore_errors\s*=\s*true",
    re.S,
)
RUFF_IGNORES_RE = re.compile(r"\[tool\.ruff\.lint\.per-file-ignores\]\n(.*?)(?=\n\[|\Z)", re.S)


def read_mypy_exceptions() -> List[str]:
    """Modules listed with ignore_errors = true in pyproject.toml."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = MYPY_OVERRIDE_RE.search(text)
    if not block:
        return []
    return re.findall(r'"([^"]+)"', block.group(1))


def module_to_path(module: str) -> pathlib.Path:
    return ROOT / (module.replace(".", "/") + ".py")


@pytest.mark.skipif(sys.platform == "win32", reason="mypy invocation differs on Windows")
def test_every_mypy_exception_is_still_needed() -> None:
    modules = read_mypy_exceptions()
    stale = []

    tmpdir = pathlib.Path(tempfile.mkdtemp())
    try:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        config_path = tmpdir / "mypy_no_overrides.toml"
        config_path.write_text(MYPY_OVERRIDE_RE.sub("", text), encoding="utf-8")

        for module in modules:
            path = module_to_path(module)
            if not path.exists():
                stale.append(f"{module} (file is gone)")
                continue
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mypy",
                    str(path),
                    "--config-file",
                    str(config_path),
                    "--ignore-missing-imports",
                    "--no-incremental",
                    "--follow-imports=silent",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                stale.append(f"{module} (now clean)")
    finally:
        shutil.rmtree(tmpdir)

    assert (
        stale == []
    ), f"these entries are no longer needed and must be removed from the mypy overrides in pyproject.toml: {stale}"


def test_every_ruff_exception_is_still_needed() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = RUFF_IGNORES_RE.search(text)
    stale = []

    if block:
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        try:
            config_path = tmpdir / "pyproject.toml"
            config_path.write_text(RUFF_IGNORES_RE.sub("", text), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    ".",
                    "--config",
                    str(config_path),
                    "--output-format=concise",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            shutil.rmtree(tmpdir)
        reported = set(re.findall(r"^(\S+?):\d+:\d+: ([A-Z]+\d+)", result.stdout, re.M))

        for line in block.group(1).splitlines():
            entry = re.match(r'"([^"]+)"\s*=\s*\[(.*)\]', line.strip())
            if not entry:
                continue
            path, codes = entry.group(1), re.findall(r'"([^"]+)"', entry.group(2))
            for code in codes:
                if (path, code) not in reported:
                    stale.append(f"{path}: {code}")

    assert (
        stale == []
    ), f"these per-file-ignores no longer suppress anything and must be removed from pyproject.toml: {stale}"
