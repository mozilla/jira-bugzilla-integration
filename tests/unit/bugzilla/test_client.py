import pytest
import requests
import responses
from responses import matchers

from jbi.bugzilla.client import (
    BugNotAccessibleError,
    BugzillaClient,
    BugzillaClientError,
)


@pytest.fixture
def webhook_private_comment_example(
    webhook_user_factory, webhook_event_factory, bug_factory, webhook_request_factory
):
    user = webhook_user_factory(login="mathieu@mozilla.org")
    event = webhook_event_factory(target="comment", user=user)
    bug = bug_factory(
        comment={"id": 344, "number": 2, "is_private": True},
        see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
    )
    webhook_payload = webhook_request_factory(bug=bug, event=event)
    return webhook_payload


@pytest.fixture
def bugzilla_client(settings):
    return BugzillaClient(
        base_url=settings.bugzilla_base_url, api_key=settings.bugzilla_api_key
    )


@pytest.mark.no_mocked_bugzilla
def test_timer_is_used_on_bugzilla_get_comments(
    bugzilla_client, settings, mocked_responses, mocked_statsd
):
    mocked_responses.add(
        "GET",
        f"{settings.bugzilla_base_url}/rest/bug/42/comment",
        json={
            "bugs": {"42": {"comments": []}},
        },
    )
    bugzilla_client.get_comments(42)
    mocked_statsd.timer.assert_called_with("jbi.bugzilla.methods.get_comments.timer")


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_methods_are_retried_if_raising(
    bugzilla_client, settings, mocked_responses
):
    url = f"{settings.bugzilla_base_url}/rest/bug/42/comment"
    mocked_responses.add(responses.GET, url, status=503, json={})
    mocked_responses.add(
        responses.GET,
        url,
        json={
            "bugs": {"42": {"comments": []}},
        },
    )

    # Not raising
    bugzilla_client.get_comments(42)

    assert len(mocked_responses.calls) == 2


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_key_is_passed_in_header(bugzilla_client, settings, mocked_responses):
    url = f"{settings.bugzilla_base_url}/rest/whoami"
    mocked_responses.add(
        responses.GET,
        url,
        json={"id": "you"},
        match=[
            matchers.header_matcher({"x-bugzilla-api-key": "fake_bugzilla_api_key"})
        ],
    )

    assert bugzilla_client.logged_in()

    assert len(mocked_responses.calls) == 1
    # The following assertion is redundant with matchers but also more explicit.
    assert "x-bugzilla-api-key" in mocked_responses.calls[0].request.headers


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_raises_if_response_has_error(
    bugzilla_client, settings, mocked_responses
):
    url = f"{settings.bugzilla_base_url}/rest/bug/42"
    mocked_responses.add(
        responses.GET, url, json={"error": True, "message": "not happy"}
    )

    with pytest.raises(BugzillaClientError) as exc:
        bugzilla_client.get_bug(42)

    assert "not happy" in str(exc)


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_get_bug_raises_if_response_is_401_and_credentials_invalid(
    bugzilla_client, settings, mocked_responses
):
    url = f"{settings.bugzilla_base_url}/rest/bug/42"
    mocked_responses.add(
        responses.GET,
        url,
        status=401,
        json={
            "code": 102,
            "documentation": "https://bmo.readthedocs.io/en/latest/api/",
            "error": True,
            "message": "You are not authorized to access bug 42.",
        },
    )
    mocked_responses.add(
        responses.GET,
        f"{settings.bugzilla_base_url}/rest/whoami",
        status=401,
    )

    with pytest.raises(requests.HTTPError) as exc:
        bugzilla_client.get_bug(42)

    assert (
        "401 Client Error: Unauthorized for url: https://bugzilla.mozilla.org/rest/bug/42"
        in str(exc)
    )


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_get_bug_raises_if_response_is_401_and_credentials_valid(
    bugzilla_client, settings, mocked_responses
):
    url = f"{settings.bugzilla_base_url}/rest/bug/42"
    mocked_responses.add(
        responses.GET,
        url,
        status=401,
        json={
            "code": 102,
            "documentation": "https://bmo.readthedocs.io/en/latest/api/",
            "error": True,
            "message": "You are not authorized to access bug 42.",
        },
    )
    mocked_responses.add(
        responses.GET,
        f"{settings.bugzilla_base_url}/rest/whoami",
        json={"id": "you"},
    )

    with pytest.raises(BugNotAccessibleError) as exc:
        bugzilla_client.get_bug(42)

    assert "You are not authorized to access bug 42" in str(exc)


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_get_bug_raises_if_response_has_no_bugs(
    bugzilla_client, settings, mocked_responses
):
    url = f"{settings.bugzilla_base_url}/rest/bug/42"
    mocked_responses.add(responses.GET, url, json={"bugs": []})

    with pytest.raises(BugzillaClientError) as exc:
        bugzilla_client.get_bug(42)

    assert "Unexpected response" in str(exc)


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_get_comments_raises_if_response_has_no_bugs(
    bugzilla_client, settings, mocked_responses
):
    url = f"{settings.bugzilla_base_url}/rest/bug/42/comment"
    mocked_responses.add(responses.GET, url, json={"bugs": {"42": {}}})

    with pytest.raises(BugzillaClientError) as exc:
        bugzilla_client.get_comments(42)

    assert "Unexpected response" in str(exc)


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_update_bug_uses_a_put(bugzilla_client, settings, mocked_responses):
    url = f"{settings.bugzilla_base_url}/rest/bug/42"
    mocked_responses.add(responses.PUT, url, json={"bugs": [{"id": 42}]})

    bugzilla_client.update_bug(42, see_also={"add": ["http://url.com"]})

    assert (
        mocked_responses.calls[0].request.body
        == b'{"see_also": {"add": ["http://url.com"]}}'
    )


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_get_bug_comment(
    bugzilla_client, settings, mocked_responses, webhook_private_comment_example
):
    # given
    bug_url = (
        f"{settings.bugzilla_base_url}/rest/bug/%s"
        % webhook_private_comment_example.bug.id
    )
    mocked_responses.add(
        responses.GET,
        bug_url,
        json={"bugs": [webhook_private_comment_example.bug.model_dump()]},
    )
    mocked_responses.add(
        responses.GET,
        bug_url + "/comment",
        json={
            "bugs": {
                str(webhook_private_comment_example.bug.id): {
                    "comments": [
                        {
                            "id": 343,
                            "text": "not this one",
                            "is_private": False,
                            "creator": "mathieu@mozilla.org",
                        },
                        {
                            "id": 344,
                            "text": "hello",
                            "is_private": False,
                            "creator": "mathieu@mozilla.org",
                        },
                        {
                            "id": 345,
                            "text": "not this one",
                            "is_private": False,
                            "creator": "mathieu@mozilla.org",
                        },
                    ]
                }
            },
            "comments": {},
        },
    )

    expanded = bugzilla_client.get_bug(webhook_private_comment_example.bug.id)

    # then
    assert expanded.comment.creator == "mathieu@mozilla.org"
    assert expanded.comment.text == "hello"


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_missing_private_comment(
    bugzilla_client,
    settings,
    mocked_responses,
    webhook_private_comment_example,
):
    bug_url = (
        f"{settings.bugzilla_base_url}/rest/bug/%s"
        % webhook_private_comment_example.bug.id
    )
    mocked_responses.add(
        responses.GET,
        bug_url,
        json={"bugs": [webhook_private_comment_example.bug.model_dump()]},
    )
    mocked_responses.add(
        responses.GET,
        bug_url + "/comment",
        json={
            "bugs": {str(webhook_private_comment_example.bug.id): {"comments": []}},
            "comments": {},
        },
    )

    expanded = bugzilla_client.get_bug(webhook_private_comment_example.bug.id)

    assert not expanded.comment


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_list_webhooks(bugzilla_client, settings, mocked_responses):
    url = f"{settings.bugzilla_base_url}/rest/webhooks/list"
    mocked_responses.add(
        responses.GET,
        url,
        json={
            "webhooks": [
                {
                    "id": 0,
                    "creator": "Bob",
                    "name": "",
                    "url": "http://server/bugzilla_webhook",
                    "event": "create,change,comment",
                    "product": "Any",
                    "component": "Any",
                    "enabled": True,
                    "errors": 0,
                }
            ]
        },
    )

    webhooks = bugzilla_client.list_webhooks()

    assert len(webhooks) == 1
    assert webhooks[0].event == "create,change,comment"
    assert "/bugzilla_webhook" in webhooks[0].url


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_list_webhooks_raises_if_response_has_no_webhooks(
    bugzilla_client, settings, mocked_responses
):
    url = f"{settings.bugzilla_base_url}/rest/webhooks/list"
    mocked_responses.add(responses.GET, url, json={})

    with pytest.raises(BugzillaClientError) as exc:
        bugzilla_client.list_webhooks()

    assert "Unexpected response" in str(exc)
