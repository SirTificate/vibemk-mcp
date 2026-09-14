"""
Tests for removing acknowledgements.

Every removal path ended in `DELETE objects/comment/{id}`, which CheckMK does
not serve — that path answers GET only. Comments are deleted through
`POST domain-types/comment/actions/delete/invoke`, which discriminates on
`delete_type` and requires a `site_id`.

Removing an acknowledgement for a host or service no longer needs to go
through comments at all. A source comment in this module reads "there are no
direct host/service action endpoints for removal", which was true of the
CheckMK this code was written against and is no longer: 2.4.0p36 serves
`POST domain-types/acknowledge/actions/delete/invoke`, discriminating on
`acknowledge_type`.

That matters beyond tidiness. The old host path listed every comment on the
site and guessed which ones were acknowledgements by looking for "ack" in the
comment text — so a maintenance note reading "ack with vendor pending" was a
candidate for deletion. The endpoint removes the guessing.
"""

from typing import Any, Dict, List

import pytest

from handlers.acknowledgements import AcknowledgementHandler

COMMENT_DELETE = "domain-types/comment/actions/delete/invoke"
ACK_DELETE = "domain-types/acknowledge/actions/delete/invoke"

HOST = "web01"
SERVICE = "CPU load"

# The comment ids the fixtures hand back, named so the assertions read as intent.
ACK_COMMENT_ID = 21
PATTERN_COMMENT_ID = 7


def ok(data: Any = None) -> Dict[str, Any]:
    return {"success": True, "status": 200, "headers": {}, "data": data if data is not None else {}}


@pytest.fixture
def handler(mock_checkmk_client: Any) -> AcknowledgementHandler:
    mock_checkmk_client.post.return_value = ok()
    mock_checkmk_client.get.return_value = ok({"value": []})
    return AcknowledgementHandler(mock_checkmk_client)


def posts_to(client: Any, endpoint: str) -> List[Dict[str, Any]]:
    return [dict(c.kwargs["data"]) for c in client.post.call_args_list if c.args[0] == endpoint]


class TestRemovingByCommentId:
    @pytest.mark.asyncio
    async def test_it_posts_to_the_delete_action(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        await handler.handle("vibemk_remove_acknowledgement", {"acknowledgement_id": str(ACK_COMMENT_ID)})

        bodies = posts_to(mock_checkmk_client, COMMENT_DELETE)
        assert len(bodies) == 1, "the comment delete action was not called"
        assert bodies[0]["delete_type"] == "by_id"

    @pytest.mark.asyncio
    async def test_the_id_is_sent_as_an_integer(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        # The schema types comment_id as an integer; a string is rejected.
        await handler.handle("vibemk_remove_acknowledgement", {"acknowledgement_id": str(ACK_COMMENT_ID)})

        assert posts_to(mock_checkmk_client, COMMENT_DELETE)[0]["comment_id"] == ACK_COMMENT_ID

    @pytest.mark.asyncio
    async def test_the_site_is_named(self, handler: AcknowledgementHandler, mock_checkmk_client: Any) -> None:
        # site_id is required by the schema and was never sent.
        await handler.handle("vibemk_remove_acknowledgement", {"acknowledgement_id": str(ACK_COMMENT_ID)})

        assert posts_to(mock_checkmk_client, COMMENT_DELETE)[0]["site_id"] == mock_checkmk_client.config.site

    @pytest.mark.asyncio
    async def test_no_delete_request_is_made(self, handler: AcknowledgementHandler, mock_checkmk_client: Any) -> None:
        await handler.handle("vibemk_remove_acknowledgement", {"acknowledgement_id": str(ACK_COMMENT_ID)})

        assert not mock_checkmk_client.delete.called, "objects/comment/{id} does not accept DELETE"


class TestRemovingForAHost:
    @pytest.mark.asyncio
    async def test_it_uses_the_acknowledge_delete_action(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        await handler.handle("vibemk_remove_acknowledgement", {"host_name": HOST})

        bodies = posts_to(mock_checkmk_client, ACK_DELETE)
        assert bodies == [{"acknowledge_type": "host", "host_name": HOST}]

    @pytest.mark.asyncio
    async def test_a_service_is_named_in_its_own_shape(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        await handler.handle("vibemk_remove_acknowledgement", {"host_name": HOST, "service_description": SERVICE})

        bodies = posts_to(mock_checkmk_client, ACK_DELETE)
        assert bodies == [{"acknowledge_type": "service", "host_name": HOST, "service_description": SERVICE}]

    @pytest.mark.asyncio
    async def test_comments_are_not_listed_or_guessed_at(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        # The endpoint knows what an acknowledgement is; reading every comment
        # on the site and matching "ack" against its text does not.
        await handler.handle("vibemk_remove_acknowledgement", {"host_name": HOST})

        listed = [c for c in mock_checkmk_client.get.call_args_list if "comment" in c.args[0]]
        assert listed == [], "the host path should not need the comment collection"
        assert not mock_checkmk_client.delete.called


class TestRemovingByCommentPattern:
    @pytest.mark.asyncio
    async def test_matching_comments_are_deleted_through_the_action(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        # Pattern removal still has to find the comments first — there is no
        # endpoint that deletes by comment text — but it must delete them the
        # way the API allows.
        mock_checkmk_client.get.return_value = ok(
            {"value": [{"id": str(PATTERN_COMMENT_ID), "extensions": {"comment": "acknowledged: disk swap pending"}}]}
        )

        await handler.handle(
            "vibemk_remove_acknowledgement", {"comment_pattern": "disk swap", "delete_all_matching": True}
        )

        bodies = posts_to(mock_checkmk_client, COMMENT_DELETE)
        assert bodies == [
            {"delete_type": "by_id", "comment_id": PATTERN_COMMENT_ID, "site_id": mock_checkmk_client.config.site}
        ]
        assert not mock_checkmk_client.delete.called
