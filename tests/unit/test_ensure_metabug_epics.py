"""The metabug backfill finds a team's metabugs by whiteboard tag."""

import importlib.util
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

_spec = importlib.util.spec_from_file_location(
    "ensure_metabug_epics", Path("bin/ensure_metabug_epics.py")
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


@pytest.mark.parametrize(
    "whiteboard,expected",
    [
        ("[aiact]", True),
        ("[perf][AIACT]", True),
        ("[aiact-ux]", True),
        ("[aiactx]", False),
        ("aiact", False),
        ("", False),
    ],
)
def test_carries_tag(bug_factory, whiteboard, expected):
    assert script.carries_tag(bug_factory(whiteboard=whiteboard), "aiact") is expected


def test_find_metabugs_queries_by_keyword_and_tag(bug_factory):
    client = mock.Mock(base_url="https://bmo.test")
    client._call.return_value = {
        "bugs": [
            {"id": 2, "whiteboard": "[aiact]", "keywords": ["meta"]},
            # substring search over-matches; the regex filter drops it
            {"id": 3, "whiteboard": "[aiactx]", "keywords": ["meta"]},
        ]
    }

    found = script.find_metabugs(client, "aiact")

    assert [b.id for b in found] == [2]
    query = parse_qs(urlparse(client._call.call_args.args[1]).query)
    assert query["keywords"] == ["meta"]
    assert query["status_whiteboard"] == ["[aiact"]
    assert "product" not in query


def test_find_metabugs_honours_a_component_scope():
    client = mock.Mock(base_url="https://bmo.test")
    client._call.return_value = {"bugs": []}

    script.find_metabugs(client, "aiact", scope=["Core::Machine Learning"])

    query = parse_qs(urlparse(client._call.call_args.args[1]).query)
    assert query["product"] == ["Core"]
    assert query["component"] == ["Machine Learning"]
