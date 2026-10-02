"""
Tests for host tag group creation.

CheckMK 2.4.0b1 renamed the field that identifies a new host tag group from
`ident` to `id` and rejects the old name (Werk 16364); 2.2 and 2.3 accept
both. Sending `id` alone therefore serves every version from 2.2 on.
"""

from typing import Any

import pytest

from handlers.tags import TagsHandler


@pytest.fixture
def handler(mock_checkmk_client: Any) -> TagsHandler:
    return TagsHandler(mock_checkmk_client)


@pytest.mark.asyncio
async def test_a_new_tag_group_is_identified_by_id(handler: TagsHandler, mock_checkmk_client: Any) -> None:
    mock_checkmk_client.post.return_value = {"success": True, "data": {}}

    await handler.handle(
        "vibemk_create_host_tag",
        {"tag_id": "criticality", "title": "Criticality", "tags": [{"id": "prod", "title": "Production"}]},
    )

    body = mock_checkmk_client.post.call_args.kwargs["data"]
    assert body["id"] == "criticality"
    assert "ident" not in body
