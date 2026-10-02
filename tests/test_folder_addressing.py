"""
Folder addressing across the handlers.

CheckMK addresses a folder as "~servers~linux". The tools accept that form as
well as the readable "/servers/linux", but seven places converted it by hand,
and every one of them turned the tilde form into "~~servers~linux" -- a folder
that does not exist: 404 in a URL, 400 in a request body. One conversion in
BaseHandler serves them all now.
"""

import pathlib
from typing import Any, Dict, List, Tuple, Type

import pytest

from handlers.base import BaseHandler
from handlers.folders import FolderHandler
from handlers.host_group_rules import HostGroupRulesHandler
from handlers.hosts import HostHandler
from handlers.rules import RulesHandler

HANDLERS = pathlib.Path(__file__).resolve().parent.parent / "handlers"
FOLDER_PATH = "objects/folder_config/"

ToolCall = Tuple[Type[BaseHandler], str, Dict[str, Any]]

# What a caller may send, and how CheckMK addresses it.
FOLDERS = [
    ("/servers/linux", "~servers~linux"),
    ("servers/linux", "~servers~linux"),
    ("~servers~linux", "~servers~linux"),
    ("/servers/linux/", "~servers~linux"),
    ("/", "~"),
    ("~", "~"),
]

ANSWER: Dict[str, Any] = {"success": True, "data": {"id": "rule-1", "value": [], "extensions": {}}, "etag": '"0"'}

# Tools that put the folder into the URL.
IN_THE_PATH: List[ToolCall] = [
    (FolderHandler, "vibemk_delete_folder", {}),
    (FolderHandler, "vibemk_update_folder", {"title": "Linux"}),
    (FolderHandler, "vibemk_move_folder", {"destination": "~archive"}),
    (FolderHandler, "vibemk_get_folder_hosts", {}),
    (HostHandler, "vibemk_get_checkmk_hosts", {}),
]

# Tools that put the folder into a rule's request body.
IN_THE_BODY: List[ToolCall] = [
    (
        RulesHandler,
        "vibemk_create_rule",
        {"ruleset_name": "checkgroup_parameters:filesystem", "rule_config": {"levels": [80.0, 90.0]}},
    ),
    (HostGroupRulesHandler, "vibemk_create_host_contactgroup_rule", {"contact_groups": ["linux-admins"]}),
    (HostGroupRulesHandler, "vibemk_create_host_hostgroup_rule", {"host_groups": ["linux"]}),
]


@pytest.fixture
def client(mock_checkmk_client: Any) -> Any:
    for method in (mock_checkmk_client.get, mock_checkmk_client.post, mock_checkmk_client.put):
        method.return_value = ANSWER
    mock_checkmk_client.delete.return_value = ANSWER
    return mock_checkmk_client


def addressed_folders(client: Any) -> List[str]:
    """The folder segment of every folder URL the handler called."""
    calls = [c for m in (client.get, client.post, client.put, client.delete) for c in m.call_args_list if c.args]
    return [c.args[0][len(FOLDER_PATH) :].split("/")[0] for c in calls if c.args[0].startswith(FOLDER_PATH)]


@pytest.mark.asyncio
@pytest.mark.parametrize("folder", FOLDERS, ids=[given for given, _ in FOLDERS])
@pytest.mark.parametrize("call", IN_THE_PATH, ids=[tool for _, tool, _ in IN_THE_PATH])
async def test_a_folder_in_the_url_is_addressed_as_checkmk_expects(
    client: Any, call: ToolCall, folder: Tuple[str, str]
) -> None:
    handler_class, tool, arguments = call
    given, addressed = folder

    await handler_class(client).handle(tool, {**arguments, "folder": given})

    folders = addressed_folders(client)
    assert folders, "the tool never addressed the folder"
    assert set(folders) == {addressed}


@pytest.mark.asyncio
@pytest.mark.parametrize("folder", FOLDERS, ids=[given for given, _ in FOLDERS])
@pytest.mark.parametrize("call", IN_THE_BODY, ids=[tool for _, tool, _ in IN_THE_BODY])
async def test_a_folder_in_a_rule_is_addressed_as_checkmk_expects(
    client: Any, call: ToolCall, folder: Tuple[str, str]
) -> None:
    handler_class, tool, arguments = call
    given, addressed = folder

    await handler_class(client).handle(tool, {**arguments, "folder": given})

    created = next(c for c in client.post.call_args_list if c.args[0] == "domain-types/rule/collections/all")
    assert created.kwargs["data"]["folder"] == addressed


def test_folders_are_converted_in_one_place() -> None:
    hand_rolled = [
        path.name
        for path in sorted(HANDLERS.glob("*.py"))
        if path.name != "base.py" and 'replace("/", "~")' in path.read_text(encoding="utf-8")
    ]

    assert hand_rolled == [], "use BaseHandler._folder_for_api"
