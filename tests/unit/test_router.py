import json
import os
from datetime import datetime
from unittest import mock

from fastapi.testclient import TestClient

from jbi.app import app
from jbi.environment import get_settings
from jbi.models import BugzillaWebhook, BugzillaWebhookRequest
from tests.fixtures.factories import bugzilla_webhook_factory


def test_read_root(anon_client):
    """The root URL provides information"""
    resp = anon_client.get("/")
    infos = resp.json()

    assert get_settings().jira_base_url in infos["configuration"]["jira_base_url"]


def test_whiteboard_tags(anon_client):
    resp = anon_client.get("/whiteboard_tags")
    actions = resp.json()

    assert actions["devtest"]["description"] == "DevTest whiteboard tag"


def test_jira_projects(anon_client, mocked_jira):
    mocked_jira.projects.return_value = [{"key": "Firefox"}, {"key": "Fenix"}]

    resp = anon_client.get("/jira_projects/")
    infos = resp.json()

    assert infos == ["Firefox", "Fenix"]


def test_whiteboard_tags_filtered(anon_client):
    resp = anon_client.get("/whiteboard_tags/?whiteboard_tag=devtest")
    infos = resp.json()
    assert sorted(infos.keys()) == ["devtest"]

    resp = anon_client.get("/whiteboard_tags/?whiteboard_tag=foo")
    infos = resp.json()
    assert sorted(infos.keys()) == ["devtest"]


def test_powered_by_jbi(exclude_middleware, anon_client):
    resp = anon_client.get("/powered_by_jbi/")
    html = resp.text
    assert "<title>Powered by JBI</title>" in html
    assert 'href="/static/styles.css"' in html
    assert "DevTest" in html


def test_powered_by_jbi_filtered(exclude_middleware, anon_client):
    resp = anon_client.get("/powered_by_jbi/?enabled=false")
    html = resp.text
    assert "DevTest" not in html


def test_webhooks_details(anon_client, mocked_bugzilla):
    mocked_bugzilla.list_webhooks.return_value = [
        bugzilla_webhook_factory(),
        bugzilla_webhook_factory(errors=42, enabled=False),
    ]
    resp = anon_client.get("/bugzilla_webhooks/")

    wh1, wh2 = resp.json()

    assert "creator" not in wh1
    assert wh1["enabled"]
    assert wh1["errors"] == 0
    assert not wh2["enabled"]
    assert wh2["errors"] == 42


def test_statics_are_served(anon_client):
    resp = anon_client.get("/static/styles.css")
    assert resp.status_code == 200


def test_webhook_is_200_if_action_succeeds(
    webhook_create_example: BugzillaWebhookRequest,
    mocked_jira,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug
    mocked_bugzilla.update_bug.return_value = {
        "bugs": [
            {
                "changes": {
                    "see_also": {
                        "added": f"{get_settings().jira_base_url}browse/JBI-1922",
                        "removed": "",
                    }
                },
                "last_change_time": datetime.now(),
            }
        ]
    }
    mocked_jira.create_issue.return_value = {
        "key": "JBI-1922",
    }
    mocked_jira.create_or_update_issue_remote_links.return_value = {
        "id": 18936,
        "self": f"{get_settings().jira_base_url}rest/api/2/issue/JBI-1922/remotelink/18936",
    }

    with TestClient(app) as anon_client:
        response = anon_client.post(
            "/bugzilla_webhook", data=webhook_create_example.json()
        )
        assert response
        assert response.status_code == 200


def test_webhook_is_200_if_action_raises_IgnoreInvalidRequestError(
    webhook_create_example: BugzillaWebhookRequest,
    mocked_bugzilla,
):
    assert webhook_create_example.bug
    webhook_create_example.bug.whiteboard = "unmatched"
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug

    with TestClient(app) as anon_client:
        response = anon_client.post(
            "/bugzilla_webhook", data=webhook_create_example.json()
        )
        assert response
        assert response.status_code == 200
        assert (
            response.json()["error"]
            == "no bug whiteboard matching action tags: devtest"
        )


def test_webhook_is_422_if_bug_information_missing(webhook_create_example):
    webhook_create_example.bug = None

    with TestClient(app) as anon_client:
        response = anon_client.post(
            "/bugzilla_webhook", data=webhook_create_example.json()
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["msg"] == "none is not an allowed value"


def test_read_version(anon_client):
    """__version__ returns the contents of version.json."""
    here = os.path.dirname(__file__)
    root_dir = os.path.dirname(os.path.dirname(here))
    version_path = os.path.join(root_dir, "version.json")
    with open(version_path, "r", encoding="utf8") as vp_file:
        version_contents = vp_file.read()
    expected = json.loads(version_contents)
    resp = anon_client.get("/__version__")
    assert resp.status_code == 200
    assert resp.json() == expected


def test_read_heartbeat_all_services_fail(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ returns 503 when all the services are unavailable."""
    mocked_bugzilla.logged_in.return_value = False
    mocked_jira.get_server_info.return_value = None

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert resp.json() == {
        "jira": {
            "up": False,
            "all_projects_are_visible": False,
            "all_projects_have_permissions": False,
            "all_projects_components_exist": False,
        },
        "bugzilla": {
            "up": False,
            "all_webhooks_enabled": False,
        },
    }


def test_read_heartbeat_jira_services_fails(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ returns 503 when one service is unavailable."""
    mocked_jira.get_server_info.return_value = None

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert resp.json()["jira"] == {
        "up": False,
        "all_projects_are_visible": False,
        "all_projects_have_permissions": False,
        "all_projects_components_exist": False,
    }


def test_read_heartbeat_bugzilla_webhooks_fails(
    anon_client, mocked_jira, mocked_bugzilla
):
    mocked_bugzilla.logged_in.return_value = True
    mocked_bugzilla.list_webhooks.return_value = [
        bugzilla_webhook_factory(enabled=False)
    ]

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert resp.json()["bugzilla"] == {
        "up": True,
        "all_webhooks_enabled": False,
    }


def test_heartbeat_bugzilla_reports_webhooks_errors(
    anon_client, mocked_jira, mocked_bugzilla
):
    mocked_bugzilla.logged_in.return_value = True
    mocked_bugzilla.list_webhooks.return_value = [
        bugzilla_webhook_factory(id=1, errors=0, product="Remote Settings"),
        bugzilla_webhook_factory(id=2, errors=3, name="Search Toolbar"),
    ]

    with mock.patch("jbi.services.bugzilla.statsd") as mocked:
        anon_client.get("/__heartbeat__")

    mocked.gauge.assert_any_call(
        "jbi.bugzilla.webhooks.1-test-webhooks-remote-settings.errors", 0
    )
    mocked.gauge.assert_any_call(
        "jbi.bugzilla.webhooks.2-search-toolbar-firefox.errors", 3
    )


def test_read_heartbeat_bugzilla_services_fails(
    anon_client, mocked_jira, mocked_bugzilla
):
    """/__heartbeat__ returns 503 when one service is unavailable."""
    mocked_bugzilla.logged_in.return_value = False
    mocked_jira.get_server_info.return_value = {}
    mocked_jira.projects.return_value = [{"key": "DevTest"}]

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert resp.json()["bugzilla"] == {
        "up": False,
        "all_webhooks_enabled": False,
    }


def test_read_heartbeat_success(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ returns 200 when checks succeed."""
    mocked_bugzilla.logged_in.return_value = True
    mocked_bugzilla.list_webhooks.return_value = [bugzilla_webhook_factory()]
    mocked_jira.get_server_info.return_value = {}
    mocked_jira.projects.return_value = [{"key": "DevTest"}]
    mocked_jira.get_project_components.return_value = [{"name": "Main"}]
    mocked_jira.get_permissions.return_value = {
        "permissions": {
            "ADD_COMMENTS": {"havePermission": True},
            "CREATE_ISSUES": {"havePermission": True},
            "EDIT_ISSUES": {"havePermission": True},
            "DELETE_ISSUES": {"havePermission": True},
        },
    }

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 200
    assert resp.json() == {
        "jira": {
            "up": True,
            "all_projects_are_visible": True,
            "all_projects_have_permissions": True,
            "all_projects_components_exist": True,
        },
        "bugzilla": {
            "up": True,
            "all_webhooks_enabled": True,
        },
    }


def test_jira_heartbeat_visible_projects(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ fails if configured projects don't match."""
    mocked_jira.get_server_info.return_value = {}

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert resp.json()["jira"] == {
        "up": True,
        "all_projects_are_visible": False,
        "all_projects_have_permissions": False,
        "all_projects_components_exist": False,
    }


def test_jira_heartbeat_missing_permissions(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ fails if configured projects don't match."""
    mocked_jira.get_server_info.return_value = {}
    mocked_jira.get_project_components.return_value = [{"name": "Main"}]
    mocked_jira.get_project_permission_scheme.return_value = {
        "permissions": {
            "ADD_COMMENTS": {"havePermission": True},
            "CREATE_ISSUES": {"havePermission": True},
            "EDIT_ISSUES": {"havePermission": False},
            "DELETE_ISSUES": {"havePermission": True},
        },
    }

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert resp.json()["jira"] == {
        "up": True,
        "all_projects_are_visible": False,
        "all_projects_have_permissions": False,
        "all_projects_components_exist": True,
    }


def test_jira_heartbeat_unknown_components(anon_client, mocked_jira, mocked_bugzilla):
    mocked_bugzilla.logged_in.return_value = True
    mocked_bugzilla.list_webhooks.return_value = [bugzilla_webhook_factory()]
    mocked_jira.get_server_info.return_value = {}

    resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    assert not resp.json()["jira"]["all_projects_components_exist"]


def test_head_heartbeat_success(anon_client, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ support head requests"""
    mocked_bugzilla.logged_in.return_value = True
    mocked_bugzilla.list_webhooks.return_value = [bugzilla_webhook_factory()]
    mocked_jira.get_server_info.return_value = {}
    mocked_jira.projects.return_value = [{"key": "DevTest"}]
    mocked_jira.get_project_components.return_value = [{"name": "Main"}]
    mocked_jira.get_permissions.return_value = {
        "permissions": {
            "ADD_COMMENTS": {"havePermission": True},
            "CREATE_ISSUES": {"havePermission": True},
            "EDIT_ISSUES": {"havePermission": True},
            "DELETE_ISSUES": {"havePermission": True},
        },
    }

    resp = anon_client.head("/__heartbeat__")

    assert resp.status_code == 200


def test_lbheartbeat(anon_client):
    """/__lbheartbeat__ always returns 200"""

    resp = anon_client.get("/__lbheartbeat__")
    assert resp.status_code == 200

    resp = anon_client.head("/__lbheartbeat__")
    assert resp.status_code == 200
