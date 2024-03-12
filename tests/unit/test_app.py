import logging
from unittest.mock import patch

import pytest

from jbi.app import traces_sampler
from jbi.environment import get_settings


def test_request_summary_is_logged(caplog, anon_client):
    with caplog.at_level(logging.INFO):
        # https://fastapi.tiangolo.com/advanced/testing-events/
        anon_client.get(
            "/",
            headers={
                "X-Request-Id": "foo-bar",
            },
        )

    summary = [r for r in caplog.records if r.name == "request.summary"][0]

    assert summary.rid == "foo-bar"
    assert summary.method == "GET"
    assert summary.path == "/"
    assert summary.querystring == ""


def test_request_summary_defaults_user_agent_to_empty_string(caplog, anon_client):
    with caplog.at_level(logging.INFO):
        del anon_client.headers["User-Agent"]
        anon_client.get("/")

        summary = [r for r in caplog.records if r.name == "request.summary"][0]

        assert summary.agent == ""


def test_422_errors_are_logged(authenticated_client, webhook_request_factory, caplog):
    webhook = webhook_request_factory.build(bug=None)

    with caplog.at_level(logging.INFO):
        authenticated_client.post(
            "/bugzilla_webhook",
            headers={"X-Api-Key": "fake_api_key"},
            data=webhook.model_dump_json(),
        )

    logged = [r for r in caplog.records if r.name == "jbi.app"][0]
    assert logged.errors[0]["loc"] == ("body", "bug")
    assert (
        logged.errors[0]["msg"]
        == "Input should be a valid dictionary or object to extract fields from"
    )


@pytest.mark.parametrize(
    "sampling_context,expected",
    [
        # /__lbheartbeat__
        ({"asgi_scope": {"path": "/__lbheartbeat__"}}, 0),
        # path that isn't /__lbheartbeat__
        (
            {"asgi_scope": {"path": "/"}},
            get_settings().sentry_traces_sample_rate,
        ),
        # context w/o an asgi_scope
        (
            {"parent_sampled": None},
            get_settings().sentry_traces_sample_rate,
        ),
        # context w/o an asgi_scope.path
        (
            {"asgi_scope": {"type": "lifespan"}},
            get_settings().sentry_traces_sample_rate,
        ),
    ],
)
def test_traces_sampler(sampling_context, expected):
    assert traces_sampler(sampling_context) == expected


@pytest.mark.asyncio
async def test_errors_are_reported_to_sentry(anon_client, bugzilla_webhook_request):
    with patch("sentry_sdk.hub.Hub.capture_event") as mocked:
        with patch("jbi.router.execute_or_queue", side_effect=ValueError):
            with pytest.raises(ValueError):
                anon_client.post(
                    "/bugzilla_webhook",
                    headers={"X-Api-Key": "fake_api_key"},
                    data=bugzilla_webhook_request.model_dump_json(),
                )

    assert mocked.called, "Sentry captured the exception"


@pytest.mark.asyncio
async def test_request_id_is_passed_down_to_logger_contexts(
    caplog,
    bugzilla_webhook_request,
    authenticated_client,
    mocked_jira,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = bugzilla_webhook_request.bug
    mocked_jira.create_issue.return_value = {
        "key": "JBI-1922",
    }
    with caplog.at_level(logging.DEBUG):
        authenticated_client.post(
            "/bugzilla_webhook",
            data=bugzilla_webhook_request.model_dump_json(),
            headers={
                "X-Request-Id": "foo-bar",
            },
        )

    runner_logs = [r for r in caplog.records if r.name == "jbi.runner"]
    assert runner_logs[0].rid == "foo-bar"
