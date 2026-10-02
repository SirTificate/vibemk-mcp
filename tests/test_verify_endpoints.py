"""
Tests for scripts/verify_endpoints.py.

The script compares every call against the API document of one site, and that
document describes one edition. A Raw site's document lacks the endpoints only
the commercial editions serve, which looks exactly like a path that exists
nowhere -- two tools were once removed on that misreading.
"""

import importlib.util
import pathlib
from typing import Any, Dict

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("verify_endpoints", ROOT / "scripts" / "verify_endpoints.py")
assert _spec is not None
assert _spec.loader is not None
verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify)

RAW_DOCUMENT: Dict[str, Dict[str, Any]] = {"/domain-types/metric/actions/get/invoke": {"post": {}}}


def test_a_commercial_only_endpoint_missing_from_the_document_is_not_a_mismatch() -> None:
    calls = [("POST", "domain-types/metric/actions/filter/invoke", "handlers/metrics.py:1")]

    mismatches, other_edition = verify.compare(RAW_DOCUMENT, calls)

    assert mismatches == []
    assert len(other_edition) == 1


def test_any_other_missing_endpoint_is_still_a_mismatch() -> None:
    calls = [("POST", "domain-types/metric/actions/no_such_action/invoke", "handlers/metrics.py:1")]

    mismatches, other_edition = verify.compare(RAW_DOCUMENT, calls)

    assert len(mismatches) == 1
    assert other_edition == []


def test_every_commercial_only_endpoint_is_still_called(monkeypatch: pytest.MonkeyPatch) -> None:
    # The list exempts calls from the check, so an entry nobody calls any more
    # must not linger.
    monkeypatch.chdir(ROOT)
    calls, _ = verify.collect_calls()
    called = {endpoint for _, endpoint, _ in calls}

    assert verify.COMMERCIAL_ONLY - called == set()
