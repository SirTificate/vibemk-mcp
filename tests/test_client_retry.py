"""
Tests for what the client retries and what it sends.

Three defects, all in the same area:

A 5xx retry was applied to every method. Re-issuing a GET is free; re-issuing
`POST .../downtime/actions/delete/invoke` after a gateway timeout can delete
twice, and the first attempt may well have succeeded before the proxy gave
up. HTTP calls GET, PUT and DELETE idempotent and POST not, and that is the
line drawn here.

429 was not retried at all, so a rate-limited call failed outright while the
server was telling us exactly how long to wait.

An empty dict body was indistinguishable from no body: `if ... and opts.data`
is false for `{}`, so a handler that deliberately sent an empty object sent
no body at all.
"""

import http
import urllib.error
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from api.client import CheckMKClient
from api.exceptions import CheckMKError
from config import CheckMKConfig

# One failure followed by one success: two attempts in total.
ONE_RETRY = 2
RETRY_AFTER_SECONDS = 7


def make_client(**overrides: Any) -> CheckMKClient:
    values: Dict[str, Any] = {
        "server_url": "https://checkmk.example.com",
        "site": "test",
        "username": "automation",
        "password": "secret",
        "verify_ssl": False,
        "timeout": 5,
        "max_retries": 2,
    }
    values.update(overrides)
    return CheckMKClient(CheckMKConfig(**values), skip_url_detection=True)


def http_error(code: int, headers: Optional[Dict[str, str]] = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://checkmk.example.com", code, "boom", headers or {}, None)  # type: ignore[arg-type]


class Recorder:
    """Answers with a given sequence of outcomes and counts the attempts."""

    def __init__(self, outcomes: List[Any]) -> None:
        self.outcomes = list(outcomes)
        self.attempts = 0

    def __call__(self, *_a: Any, **_k: Any) -> Any:
        self.attempts += 1
        outcome = self.outcomes.pop(0) if self.outcomes else self.outcomes
        if isinstance(outcome, Exception):
            raise outcome
        response = MagicMock()
        response.status = 200
        response.read.return_value = b"{}"
        response.headers = {}
        response.__enter__ = lambda s: s
        response.__exit__ = lambda *_: False
        return response


class TestRetriesRespectIdempotence:
    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
    def test_an_idempotent_method_is_retried(self, method: str) -> None:
        recorder = Recorder([http_error(503), None])

        with patch("urllib.request.urlopen", recorder), patch("time.sleep"):
            make_client().request("domain-types/host/collections/all", method)

        assert recorder.attempts == ONE_RETRY

    def test_a_post_is_not_retried(self) -> None:
        # The first attempt may already have created or deleted something.
        recorder = Recorder([http_error(503), None])

        with patch("urllib.request.urlopen", recorder), patch("time.sleep"), pytest.raises(CheckMKError):
            make_client().request("domain-types/downtime/actions/delete/invoke", "POST")

        assert recorder.attempts == 1


class TestRateLimitingIsRetried:
    def test_a_429_is_retried(self) -> None:
        recorder = Recorder([http_error(http.HTTPStatus.TOO_MANY_REQUESTS), None])

        with patch("urllib.request.urlopen", recorder), patch("time.sleep"):
            make_client().request("domain-types/host/collections/all", "GET")

        assert recorder.attempts == ONE_RETRY

    def test_retry_after_sets_the_wait(self) -> None:
        recorder = Recorder(
            [http_error(http.HTTPStatus.TOO_MANY_REQUESTS, {"Retry-After": str(RETRY_AFTER_SECONDS)}), None]
        )

        with patch("urllib.request.urlopen", recorder), patch("time.sleep") as slept:
            make_client().request("domain-types/host/collections/all", "GET")

        assert slept.call_args.args[0] == RETRY_AFTER_SECONDS, "the server said how long to wait"


class TestAnEmptyBodyIsStillABody:
    def test_an_empty_dict_is_sent(self) -> None:
        recorder = Recorder([None])

        with patch("urllib.request.urlopen", recorder), patch("urllib.request.Request") as request:
            make_client().post("domain-types/activation_run/actions/activate-changes/invoke", data={})

        assert request.call_args.kwargs["data"] == b"{}"

    def test_no_data_still_means_no_body(self) -> None:
        recorder = Recorder([None])

        with patch("urllib.request.urlopen", recorder), patch("urllib.request.Request") as request:
            make_client().post("objects/notification_rule/1/actions/delete/invoke")

        assert request.call_args.kwargs["data"] is None
