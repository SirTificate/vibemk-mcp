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
from mcp.tools import get_all_tools

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


# CheckMK types every comment: the Livestatus `entry_type` column is 1 for a user
# comment, 2 for downtime, 3 for flapping and 4 for an acknowledgement. The code
# used to ignore that and guess — "ack" as a substring of the comment text, or
# merely the persistent flag — which matches "track the vendor ticket",
# "packaging", and every persistent comment ever written. Those guesses fed
# remove_acknowledgement, which deletes by comment id.
ACK_ENTRY_TYPE = "4"
COMMENT_COLLECTION = "domain-types/comment/collections/all"


class TestAcknowledgementsAreIdentifiedByTheirType:
    @pytest.mark.asyncio
    async def test_the_listing_asks_checkmk_for_acknowledgements(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        await handler.handle("vibemk_list_acknowledgements", {})

        call = next(c for c in mock_checkmk_client.get.call_args_list if c.args[0] == COMMENT_COLLECTION)
        assert call.kwargs["params"]["query"] == {
            "op": "=",
            "left": "entry_type",
            "right": ACK_ENTRY_TYPE,
        }, "the type is a column CheckMK can filter on; guessing from the text is not needed"

    @pytest.mark.asyncio
    async def test_a_persistent_note_is_not_an_acknowledgement(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        # The old rule treated every persistent comment as an acknowledgement.
        mock_checkmk_client.get.return_value = ok({"value": []})

        result = await handler.handle("vibemk_list_acknowledgements", {})

        assert "No active acknowledgements" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_the_text_is_never_searched_for_ack(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        # Whatever CheckMK returns for the query is an acknowledgement by
        # definition — including one whose text contains no form of "ack".
        mock_checkmk_client.get.return_value = ok(
            {"value": [{"id": "9", "extensions": {"host_name": HOST, "comment": "disk replaced, watching"}}]}
        )

        result = await handler.handle("vibemk_list_acknowledgements", {})

        assert "disk replaced, watching" in result[0]["text"]


class TestAcknowledgementFlagsAgreeWithCheckmk:
    """Three tools acknowledge a problem, and they disagreed about what an
    acknowledgement is.

    vibemk_acknowledge_problem hard-wired sticky and notify to True and
    offered no way to change them. The two specific tools read both from the
    caller and defaulted them to False. CheckMK's own schema defaults sticky
    and notify to True and persistent to False, so it was the pair defaulting
    to False that diverged — and either way, the same request through two
    tools produced two different acknowledgements.
    """

    ACK_HOST = "domain-types/acknowledge/collections/host"

    def body(self, client: Any) -> Dict[str, Any]:
        return dict(next(c for c in client.post.call_args_list if c.args[0] == self.ACK_HOST).kwargs["data"])

    @pytest.mark.asyncio
    async def test_the_specific_tool_follows_checkmks_defaults(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        await handler.handle("vibemk_acknowledge_host_problem", {"host_name": HOST, "comment": "looking into it"})

        body = self.body(mock_checkmk_client)
        assert body["sticky"] is True
        assert body["notify"] is True
        assert body["persistent"] is False

    @pytest.mark.asyncio
    async def test_the_caller_can_still_turn_them_off(
        self, handler: AcknowledgementHandler, mock_checkmk_client: Any
    ) -> None:
        await handler.handle(
            "vibemk_acknowledge_host_problem",
            {"host_name": HOST, "comment": "quietly", "sticky": False, "notify": False},
        )

        body = self.body(mock_checkmk_client)
        assert body["sticky"] is False
        assert body["notify"] is False

    def test_the_schemas_no_longer_advertise_false(self) -> None:
        for name in ("vibemk_acknowledge_host_problem", "vibemk_acknowledge_service_problem"):
            tool = next(t for t in get_all_tools() if t["name"] == name)
            properties = tool["inputSchema"]["properties"]
            assert properties["sticky"].get("default") is True, name
            assert properties["notify"].get("default") is True, name
            assert properties["persistent"].get("default") is False, name
