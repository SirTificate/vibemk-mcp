"""
Tests for ServiceHandler's service-status fallback chain.

_get_service_status tries four independent CheckMK endpoints in order and
falls through to the next one whenever a fallback doesn't produce an answer.
That has to include an exception raised while processing an already-successful
response (a malformed 'state' value, an unexpected shape) -- not just a
failure of the API call itself. Each fallback method's try/except therefore
has to wrap its own response processing, not just the network call, or a
processing exception propagates past the whole chain instead of falling
through to the next method.
"""

import pytest

from handlers.services import ServiceHandler


@pytest.fixture
def handler(mock_checkmk_client):
    return ServiceHandler(mock_checkmk_client)


def ok(data=None):
    return {"success": True, "status": 200, "headers": {}, "data": data if data is not None else {}}


def failed(data=None):
    return {"success": False, "status": 404, "headers": {}, "data": data if data is not None else {}}


class TestServiceStatusFallbackChain:
    @pytest.mark.asyncio
    async def test_an_exception_during_processing_falls_through_to_the_next_method(self, handler):
        """A malformed 'state' (unhashable) must not abort the whole chain.

        Method 1's response looks successful at the HTTP level, but its
        'state' value is a list -- unhashable, so looking it up in the status
        map raises a TypeError while building the response, not while making
        the request. That has to be treated the same as Method 1's request
        itself failing: fall through to Method 2, not propagate out past the
        whole fallback chain.
        """
        handler.client.get.side_effect = [
            # Method 1 (show_service): succeeds at the HTTP level, but 'state'
            # is unhashable, so building the response raises during processing.
            ok({"extensions": {"state": ["unhashable"], "description": "CPU utilization"}}),
            # Method 2 (direct object): a clean miss, no exception.
            failed({}),
            # Method 3 (query API): succeeds, and is what the test expects to see.
            ok({"value": [{"extensions": {"state": 0, "plugin_output": "OK", "last_check": 0}}]}),
        ]

        result = await handler.handle(
            "vibemk_get_service_status",
            {"host_name": "example.com", "service_description": "CPU utilization"},
        )

        text = result[0]["text"]
        assert "Dict Format" in text, text
        assert "Unexpected Error" not in text, text
        assert "Service Status Retrieval Failed" not in text, text
