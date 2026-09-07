"""
Tests for the optional .env loader.

CONTRIBUTING.md tells contributors to `cp .env.example .env` and edit it, so
the parser has to survive what people actually write into such a file:
editors that prepend a BOM, shell-sourceable `export` lines, trailing
comments, and quoted values.
"""

import os
from pathlib import Path
from typing import Callable, Union
from unittest.mock import patch

import pytest

from config.dotenv import load_dotenv


@pytest.fixture
def env_file(tmp_path: Path) -> Callable[..., Path]:
    """Write a .env file and return a loader bound to it."""

    def write(content: Union[str, bytes], encoding: str = "utf-8") -> Path:
        path = tmp_path / ".env"
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding=encoding)
        return path

    return write


@pytest.fixture(autouse=True)
def clean_environment():
    with patch.dict(os.environ, {}, clear=True):
        yield


class TestBasicParsing:
    def test_reads_a_simple_assignment(self, env_file):
        load_dotenv(env_file("CHECKMK_SITE=mysite\n"))

        assert os.environ["CHECKMK_SITE"] == "mysite"

    def test_skips_comments_and_blank_lines(self, env_file):
        load_dotenv(env_file("# a comment\n\n   \nCHECKMK_SITE=mysite\n"))

        assert os.environ["CHECKMK_SITE"] == "mysite"

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert load_dotenv(tmp_path / "does-not-exist") is None

    def test_returns_the_path_it_loaded(self, env_file):
        path = env_file("CHECKMK_SITE=mysite\n")

        assert load_dotenv(path) == path

    def test_loads_keys_that_are_not_checkmk_prefixed(self, env_file):
        # main.py reads LOGFILE, so restricting to CHECKMK_ would silently drop it.
        load_dotenv(env_file("LOGFILE=/tmp/vibemk.log\n"))

        assert os.environ["LOGFILE"] == "/tmp/vibemk.log"

    def test_value_may_contain_equals_signs(self, env_file):
        load_dotenv(env_file("CHECKMK_PASSWORD=a=b=c\n"))

        assert os.environ["CHECKMK_PASSWORD"] == "a=b=c"


class TestShellCompatibleSyntax:
    def test_export_prefix_is_accepted(self, env_file):
        load_dotenv(env_file("export CHECKMK_SITE=mysite\n"))

        assert os.environ["CHECKMK_SITE"] == "mysite"


class TestQuoting:
    def test_double_quotes_are_stripped(self, env_file):
        load_dotenv(env_file('CHECKMK_SITE="mysite"\n'))

        assert os.environ["CHECKMK_SITE"] == "mysite"

    def test_single_quotes_are_stripped(self, env_file):
        load_dotenv(env_file("CHECKMK_SITE='mysite'\n"))

        assert os.environ["CHECKMK_SITE"] == "mysite"

    def test_interior_quotes_are_not_stripped(self, env_file):
        # Stripping first/last whenever they merely match silently eats
        # characters from a password and produces a puzzling 401.
        load_dotenv(env_file('CHECKMK_PASSWORD="a"b"\n'))

        assert os.environ["CHECKMK_PASSWORD"] == '"a"b"'

    def test_a_quoted_value_keeps_its_hash(self, env_file):
        load_dotenv(env_file('CHECKMK_PASSWORD="p@ss #1"\n'))

        assert os.environ["CHECKMK_PASSWORD"] == "p@ss #1"


class TestInlineComments:
    def test_trailing_comment_is_stripped_from_unquoted_value(self, env_file):
        load_dotenv(env_file("CHECKMK_TIMEOUT=45  # seconds\n"))

        assert os.environ["CHECKMK_TIMEOUT"] == "45"

    def test_hash_without_leading_space_is_part_of_the_value(self, env_file):
        load_dotenv(env_file("CHECKMK_PASSWORD=abc#123\n"))

        assert os.environ["CHECKMK_PASSWORD"] == "abc#123"


class TestPrecedence:
    def test_existing_environment_variable_wins(self, env_file):
        os.environ["CHECKMK_SITE"] = "from-environment"
        load_dotenv(env_file("CHECKMK_SITE=from-file\n"))

        assert os.environ["CHECKMK_SITE"] == "from-environment"

    def test_explicitly_empty_environment_variable_still_wins(self, env_file):
        # `docker run -e CHECKMK_VERIFY_SSL=` is a deliberate override.
        os.environ["CHECKMK_VERIFY_SSL"] = ""
        load_dotenv(env_file("CHECKMK_VERIFY_SSL=false\n"))

        assert os.environ["CHECKMK_VERIFY_SSL"] == ""


class TestEncoding:
    def test_utf8_bom_does_not_break_the_first_key(self, env_file):
        # Notepad and PowerShell Out-File write a BOM by default.
        load_dotenv(env_file("CHECKMK_SERVER_URL=https://cmk.example.com\n", encoding="utf-8-sig"))

        assert os.environ["CHECKMK_SERVER_URL"] == "https://cmk.example.com"

    def test_non_utf8_bytes_do_not_raise(self, tmp_path):
        path = tmp_path / ".env"
        path.write_bytes(b"CHECKMK_SITE=mysite\nCHECKMK_PASSWORD=caf\xe9\n")

        load_dotenv(path)  # must not raise UnicodeDecodeError

        assert os.environ["CHECKMK_SITE"] == "mysite"

    def test_umlaut_in_value_is_read_correctly(self, env_file):
        load_dotenv(env_file("CHECKMK_PASSWORD=Grüße\n"))

        assert os.environ["CHECKMK_PASSWORD"] == "Grüße"


class TestDiscovery:
    def test_finds_dotenv_in_the_working_directory(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("CHECKMK_SITE=from-cwd\n")
        monkeypatch.chdir(tmp_path)

        load_dotenv()

        assert os.environ["CHECKMK_SITE"] == "from-cwd"

    def test_returns_none_when_no_file_is_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("config.dotenv._package_root", lambda: tmp_path / "nowhere")

        assert load_dotenv() is None
