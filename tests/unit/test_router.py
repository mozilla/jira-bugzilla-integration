import base64
import json
import os
from datetime import datetime
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from jbi.environment import get_settings
from jbi.queue import get_dl_queue


def test_read_root(anon_client):
    """The root URL provides information"""
    resp = anon_client.get("/")
    infos = resp.json()

    assert get_settings().jira_base_url in infos["configuration"]["jira_base_url"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "/whiteboard_tags",
        "/jira_projects/",
        "/powered_by_jbi/",
        "/bugzilla_webhooks/",
    ],
)
def test_get_protected_endpoints(
    endpoint, webhook_request_factory, mocked_bugzilla, anon_client, test_api_key
):
    resp = anon_client.get(endpoint)
    assert resp.status_code == 401

    # Supports authentication via `X-Api-Key` header
    resp = anon_client.get(endpoint, headers={"X-Api-Key": test_api_key})
    assert resp.status_code == 200

    # Supports authentication via Basic Auth header
    username_password = ":" + test_api_key
    credentials_b64 = base64.b64encode(username_password.encode("utf8")).decode("utf8")
    resp = anon_client.get(
        endpoint,
        headers={"Authorization": f"Basic {credentials_b64}"},
    )
    assert resp.status_code == 200


def test_whiteboard_tags(authenticated_client):
    resp = authenticated_client.get("/whiteboard_tags")
    actions = resp.json()

    assert actions["devtest"]["description"] == "DevTest whiteboard tag"


def test_jira_projects(authenticated_client, mocked_jira):
    mocked_jira.permitted_projects.return_value = [{"key": "Firefox"}, {"key": "Fenix"}]

    resp = authenticated_client.get("/jira_projects/")
    infos = resp.json()

    assert infos == ["Firefox", "Fenix"]


def test_whiteboard_tags_filtered(authenticated_client):
    resp = authenticated_client.get("/whiteboard_tags/?whiteboard_tag=devtest")
    infos = resp.json()
    assert sorted(infos.keys()) == ["devtest"]

    resp = authenticated_client.get("/whiteboard_tags/?whiteboard_tag=foo")
    infos = resp.json()
    assert sorted(infos.keys()) == ["devtest"]


def test_powered_by_jbi(exclude_middleware, authenticated_client):
    resp = authenticated_client.get("/powered_by_jbi/")
    html = resp.text
    assert "<title>Powered by JBI</title>" in html
    assert 'href="/static/styles.css"' in html
    assert "DevTest" in html


def test_powered_by_jbi_filtered(exclude_middleware, authenticated_client):
    resp = authenticated_client.get("/powered_by_jbi/?enabled=false")
    html = resp.text
    assert "DevTest" not in html


def test_webhooks_details(authenticated_client, mocked_bugzilla, webhook_factory):
    mocked_bugzilla.list_webhooks.return_value = [
        webhook_factory(),
        webhook_factory(errors=42, enabled=False),
    ]
    resp = authenticated_client.get("/bugzilla_webhooks/")

    wh1, wh2 = resp.json()

    assert "creator" not in wh1
    assert wh1["enabled"]
    assert wh1["errors"] == 0
    assert not wh2["enabled"]
    assert wh2["errors"] == 42


def test_statics_are_served(authenticated_client):
    resp = authenticated_client.get("/static/styles.css")
    assert resp.status_code == 200


def test_webhook_is_200_if_action_succeeds(
    bugzilla_webhook_request, mocked_jira, mocked_bugzilla, authenticated_client
):
    mocked_bugzilla.get_bug.return_value = bugzilla_webhook_request.bug
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

    response = authenticated_client.post(
        "/bugzilla_webhook",
        data=bugzilla_webhook_request.model_dump_json(),
    )
    assert response
    assert response.status_code == 200


async def test_webhook_is_200_if_action_raises_IgnoreInvalidRequestError(
    webhook_request_factory, mocked_bugzilla, authenticated_client
):
    webhook = webhook_request_factory(bug__whiteboard="unmatched")
    mocked_bugzilla.get_bug.return_value = webhook.bug

    response = authenticated_client.post(
        "/bugzilla_webhook",
        data=webhook.model_dump_json(),
    )
    assert response
    assert response.status_code == 200
    assert response.json()["error"] == "no bug whiteboard matching action tags: devtest"


def test_webhook_is_401_if_unathenticated(
    webhook_request_factory, mocked_bugzilla, anon_client
):
    response = anon_client.post(
        "/bugzilla_webhook",
        data={},
    )
    assert response.status_code == 401


def test_webhook_is_401_if_wrong_key(
    webhook_request_factory, mocked_bugzilla, anon_client
):
    response = anon_client.post(
        "/bugzilla_webhook",
        headers={"X-Api-Key": "not the right key"},
        data={},
    )
    assert response.status_code == 401


def test_webhook_is_422_if_bug_information_missing(
    webhook_request_factory, authenticated_client
):
    webhook = webhook_request_factory.build(bug=None)

    response = authenticated_client.post(
        "/bugzilla_webhook",
        headers={"X-Api-Key": "fake_api_key"},
        data=webhook.model_dump_json(),
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "bug"]


@pytest.mark.asyncio
async def test_webhook_adds_to_queue_on_failure(
    webhook_request_factory,
    authenticated_client,
):
    webhook = webhook_request_factory.build()
    dl_queue = get_dl_queue()
    before = await dl_queue.backend.size()

    with mock.patch("jbi.runner.execute_action", side_effect=ValueError("Boom!")):
        response = authenticated_client.post(
            "/bugzilla_webhook",
            data=webhook.model_dump_json(),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert await dl_queue.backend.size() == before + 1


@pytest.mark.asyncio
async def test_webhook_skips_processing_if_blocking_in_queue(
    webhook_request_factory,
    authenticated_client,
):
    webhook = webhook_request_factory.build()
    dl_queue = get_dl_queue()
    await dl_queue.track_failed(webhook, ValueError("boom!"))
    before = await dl_queue.backend.size()

    with mock.patch("jbi.runner.execute_action") as mocked_execution:
        response = authenticated_client.post(
            "/bugzilla_webhook",
            data=webhook.model_dump_json(),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "skipped"
        mocked_execution.assert_not_called()
        assert await dl_queue.backend.size() == before + 1


#
# Dockerflow
#


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


def test_read_heartbeat_all_services_fail(app, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ returns 503 when all the services are unavailable."""
    mocked_bugzilla.logged_in.return_value = False
    mocked_jira.get_server_info.return_value = None

    with TestClient(app) as anon_client:
        resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    results = resp.json()
    assert results["status"] == "error"
    assert results["checks"]["bugzilla.up"] == "error"
    assert results["checks"]["jira.up"] == "error"


def test_read_heartbeat_jira_services_fails(app, mocked_jira):
    """/__heartbeat__ returns 503 when one service is unavailable."""
    mocked_jira.get_server_info.return_value = None

    with TestClient(app) as anon_client:
        resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 503
    results = resp.json()
    assert results["status"] == "error"
    assert results["checks"]["bugzilla.up"] == "ok"
    assert results["checks"]["jira.up"] == "error"


def test_read_heartbeat_bugzilla_webhooks_fails(app, mocked_bugzilla, webhook_factory):
    mocked_bugzilla.logged_in.return_value = True
    mocked_bugzilla.list_webhooks.return_value = [webhook_factory(enabled=False)]

    with TestClient(app) as anon_client:
        resp = anon_client.get("/__heartbeat__")

    results = resp.json()
    assert results["checks"]["bugzilla.all_webhooks_enabled"] == "error"
    assert results["details"]["bugzilla.all_webhooks_enabled"] == {
        "level": 40,
        "messages": {
            "bugzilla.webhooks.disabled": "Webhook Test Webhooks is disabled (0 errors)",
        },
        "status": "error",
    }


def test_heartbeat_bugzilla_reports_webhooks_errors(
    app, mocked_bugzilla, webhook_factory
):
    mocked_bugzilla.logged_in.return_value = True
    mocked_bugzilla.list_webhooks.return_value = [
        webhook_factory(id=1, errors=0, product="Remote Settings"),
        webhook_factory(id=2, errors=3, name="Search Toolbar"),
    ]
    with (
        mock.patch("jbi.bugzilla.service.statsd") as mocked,
        TestClient(app) as anon_client,
    ):
        anon_client.get("/__heartbeat__")

    mocked.gauge.assert_any_call(
        "jbi.bugzilla.webhooks.1-test-webhooks-remote-settings.errors", 0
    )
    mocked.gauge.assert_any_call(
        "jbi.bugzilla.webhooks.2-search-toolbar-firefox.errors", 3
    )


def test_read_heartbeat_bugzilla_services_fails(app, mocked_bugzilla):
    """/__heartbeat__ returns 503 when one service is unavailable."""
    mocked_bugzilla.logged_in.return_value = False

    with TestClient(app) as anon_client:
        resp = anon_client.get("/__heartbeat__")

    results = resp.json()
    assert resp.status_code == 503
    assert results["checks"]["bugzilla.up"] == "error"
    assert results["details"]["bugzilla.up"] == {
        "level": 40,
        "messages": {
            "bugzilla.login": "Login fails or service down",
        },
        "status": "error",
    }


def test_jira_heartbeat_visible_projects(app, mocked_jira):
    """/__heartbeat__ fails if configured projects don't match."""
    mocked_jira.get_server_info.return_value = {}

    with TestClient(app) as anon_client:
        resp = anon_client.get("/__heartbeat__")

    results = resp.json()
    assert results["checks"]["jira.all_projects_are_visible"] == "warning"
    assert results["details"]["jira.all_projects_are_visible"] == {
        "level": 30,
        "messages": {
            "jira.projects.missing": "Jira projects {'DevTest'} are not visible with configured "
            "credentials",
        },
        "status": "warning",
    }


def test_jira_heartbeat_missing_permissions(app, mocked_jira):
    """/__heartbeat__ fails if configured projects don't match."""
    mocked_jira.get_server_info.return_value = {}
    mocked_jira.get_project_permission_scheme.return_value = {
        "permissions": {
            "ADD_COMMENTS": {"havePermission": True},
            "CREATE_ISSUES": {"havePermission": True},
            "EDIT_ISSUES": {"havePermission": False},
            "DELETE_ISSUES": {"havePermission": True},
        },
    }

    with TestClient(app) as anon_client:
        resp = anon_client.get("/__heartbeat__")

    results = resp.json()
    assert results["checks"]["jira.all_projects_have_permissions"] == "warning"
    assert results["details"]["jira.all_projects_have_permissions"] == {
        "level": 30,
        "messages": {
            "jira.permitted.missing": "Missing permissions for projects DevTest",
        },
        "status": "warning",
    }


def test_jira_heartbeat_unknown_components(app, mocked_jira):
    mocked_jira.get_server_info.return_value = {}

    with TestClient(app) as anon_client:
        resp = anon_client.get("/__heartbeat__")

    results = resp.json()
    assert results["checks"]["jira.all_project_custom_components_exist"] == "warning"
    assert results["details"]["jira.all_project_custom_components_exist"] == {
        "level": 30,
        "messages": {
            "jira.components.missing": "Jira project DevTest does not have components {'Main'}",
        },
        "status": "warning",
    }


def test_jira_heartbeat_unknown_issue_types(app, mocked_jira):
    mocked_jira.get_server_info.return_value = {}
    mocked_jira.get_project.return_value = {
        "issueTypes": [
            {"name": "Task"},
            # missing "Bug"
        ]
    }

    with TestClient(app) as anon_client:
        resp = anon_client.get("/__heartbeat__")

    results = resp.json()
    assert results["checks"]["jira.all_project_issue_types_exist"] == "warning"
    assert results["details"]["jira.all_project_issue_types_exist"] == {
        "level": 30,
        "messages": {
            "jira.types.missing": "Jira projects {'DevTest'} with missing issue types",
        },
        "status": "warning",
    }


@pytest.mark.parametrize("method", ["HEAD", "GET"])
def test_read_heartbeat_success(
    app, method, mocked_jira, mocked_bugzilla, bugzilla_webhook
):
    """/__heartbeat__ returns 200 when checks succeed."""
    mocked_bugzilla.logged_in.return_value = True
    mocked_bugzilla.list_webhooks.return_value = [bugzilla_webhook]
    mocked_jira.get_server_info.return_value = {}
    mocked_jira.paginated_projects.return_value = {
        "values": [
            {
                "key": "DevTest",
                "issueTypes": [
                    {"name": "Task"},
                    {"name": "Bug"},
                ],
            }
        ]
    }
    mocked_jira.get_project_components.return_value = [{"name": "Main"}]
    mocked_jira.permitted_projects.return_value = [{"key": "DevTest"}]

    with TestClient(app) as client:
        resp = client.request(method, "__heartbeat__")

    assert resp.status_code == 200
    if method == "GET":
        assert resp.json() == {
            "checks": {
                "bugzilla.up": "ok",
                "bugzilla.all_webhooks_enabled": "ok",
                "jira.up": "ok",
                "jira.all_project_custom_components_exist": "ok",
                "jira.all_project_issue_types_exist": "ok",
                "jira.all_projects_are_visible": "ok",
                "jira.all_projects_have_permissions": "ok",
                "jira.pandoc_install": "ok",
            },
            "details": {},
            "status": "ok",
        }


def test_heartbeat_with_warning_only(app, mocked_jira, mocked_bugzilla):
    """/__heartbeat__ returns 200 when checks are only warning."""
    mocked_bugzilla.logged_in.return_value = True
    mocked_bugzilla.list_webhooks.return_value = []

    with TestClient(app) as anon_client:
        resp = anon_client.get("/__heartbeat__")

    assert resp.status_code == 200
    assert resp.json()["status"] == "warning"


@pytest.mark.parametrize("method", ["HEAD", "GET"])
def test_lbheartbeat(anon_client, method):
    """/__lbheartbeat__ always returns 200"""

    resp = anon_client.request(method, "__lbheartbeat__")
    assert resp.status_code == 200
