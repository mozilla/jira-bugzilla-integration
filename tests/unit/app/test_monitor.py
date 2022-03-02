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


def test_read_heartbeat_no_services_fails(anon_client):
    """/__heartbeat__ returns 503 when the services are unavailable."""
    expected = {
        "jira": {
            "up": False,
        },
        "bugzilla": {
            "up": False,
        },
    }
    with patch("src.app.monitor.jbi_service_health_map", return_value=expected):
        resp = anon_client.get("/__heartbeat__")
    assert resp.status_code == 503
    data = resp.json()
    assert data == expected


def test_read_heartbeat_jira_services_fails(anon_client):
    """/__heartbeat__ returns 503 when the services are unavailable."""
    expected = {
        "jira": {
            "up": False,
        },
        "bugzilla": {
            "up": True,
        },
    }
    with patch("src.app.monitor.jbi_service_health_map", return_value=expected):
        resp = anon_client.get("/__heartbeat__")
    assert resp.status_code == 503
    data = resp.json()
    assert data == expected


def test_read_heartbeat_bugzilla_services_fails(anon_client):
    """/__heartbeat__ returns 503 when the services are unavailable."""
    expected = {
        "jira": {
            "up": True,
        },
        "bugzilla": {
            "up": False,
        },
    }
    with patch("src.app.monitor.jbi_service_health_map", return_value=expected):
        resp = anon_client.get("/__heartbeat__")
    assert resp.status_code == 503
    data = resp.json()
    assert data == expected


def test_read_heartbeat_success(anon_client):
    """/__heartbeat__ returns 200 when measuring the acoustic backlog fails."""
    expected = {
        "jira": {
            "up": True,
        },
        "bugzilla": {
            "up": True,
        },
    }
    with patch("src.app.monitor.jbi_service_health_map", return_value=expected):
        resp = anon_client.get("/__heartbeat__")
    assert resp.status_code == 200
    data = resp.json()
    assert data == expected
