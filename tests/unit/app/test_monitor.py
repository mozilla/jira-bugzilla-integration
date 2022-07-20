"""
Module for testing src/app/monitor.py
"""
# pylint: disable=cannot-enumerate-pytest-fixtures

import json
import os.path
from unittest.mock import patch


def test_read_version(anon_client):
    """__version__ returns the contents of version.json."""
    here = os.path.dirname(__file__)
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    version_path = os.path.join(root_dir, "version.json")
    with open(version_path, "r", encoding="utf8") as vp_file:
        version_contents = vp_file.read()
    expected = json.loads(version_contents)
    resp = anon_client.get("/__version__")
    assert resp.status_code == 200
    assert resp.json() == expected


def test_read_heartbeat_all_services_fail(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ returns 503 when all the services are unavailable."""
    mocked_bugzilla().logged_in = False
    mocked_jira().get_server_info.return_value = None

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert resp.json() == {
        "jira": {
            "up": False,
        },
        "bugzilla": {
            "up": False,
        },
    }


def test_read_heartbeat_jira_services_fails(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ returns 503 when one service is unavailable."""
    mocked_bugzilla().logged_in = True
    mocked_jira().get_server_info.return_value = None

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert resp.json() == {
        "jira": {
            "up": False,
        },
        "bugzilla": {
            "up": True,
        },
    }


def test_read_heartbeat_bugzilla_services_fails(
    anon_client, mocked_jira, mocked_bugzilla
):
    """/__heartbeat__ returns 503 when one service is unavailable."""
    mocked_bugzilla().logged_in = False
    mocked_jira().get_server_info.return_value = {}
    mocked_jira().projects.return_value = [{"key": "MR2"}, {"key": "JST"}]

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert resp.json() == {
        "jira": {
            "up": True,
            "all_projects_are_visible": True,
        },
        "bugzilla": {
            "up": False,
        },
    }


def test_read_heartbeat_success(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ returns 200 when checks succeed."""
    mocked_bugzilla().logged_in = True
    mocked_jira().get_server_info.return_value = {}
    mocked_jira().projects.return_value = [{"key": "MR2"}, {"key": "JST"}]

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 200
    assert resp.json() == {
        "jira": {
            "up": True,
            "all_projects_are_visible": True,
        },
        "bugzilla": {
            "up": True,
        },
    }


def test_jira_heartbeat_visible_projects(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ fails if configured projects don't match."""
    mocked_bugzilla().logged_in = True
    mocked_jira().get_server_info.return_value = {}

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert resp.json() == {
        "jira": {
            "up": True,
            "all_projects_are_visible": False,
        },
        "bugzilla": {
            "up": True,
        },
    }


def test_head_heartbeat(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ support head requests"""
    mocked_bugzilla().logged_in = True
    mocked_jira().get_server_info.return_value = {}
    mocked_jira().projects.return_value = [{"key": "MR2"}, {"key": "JST"}]

    resp = anon_client.head("/__heartbeat__")

    assert resp.status_code == 200


def test_lbheartbeat(anon_client):
    """/__lbheartbeat__ always returns 200"""

    resp = anon_client.get("/__lbheartbeat__")
    assert resp.status_code == 200

    resp = anon_client.head("/__lbheartbeat__")
    assert resp.status_code == 200
