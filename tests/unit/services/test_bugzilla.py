from unittest import mock

import pytest
import responses

from jbi.environment import get_settings
from jbi.services.bugzilla import BugzillaClientError, get_client


def test_timer_is_used_on_bugzilla_get_comments():
    bugzilla_client = get_client()

    with mock.patch("jbi.services.common.statsd") as mocked:
        bugzilla_client.get_comments([])

    mocked.timer.assert_called_with("jbi.bugzilla.methods.get_comments.timer")


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_methods_are_retried_if_raising(mocked_responses):
    url = f"{get_settings().bugzilla_base_url}/rest/bug/42/comment"
    mocked_responses.add(responses.GET, url, status=503, json={})
    mocked_responses.add(
        responses.GET,
        url,
        json={
            "bugs": {"42": {"comments": []}},
        },
    )

    # Not raising
    get_client().get_comments(42)

    assert len(mocked_responses.calls) == 2


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_key_is_passed_in_querystring(mocked_responses):
    url = (
        f"{get_settings().bugzilla_base_url}/rest/whoami?api_key=fake_bugzilla_api_key"
    )
    mocked_responses.add(responses.GET, url, json={"id": "you"})

    assert get_client().logged_in


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_raises_if_response_has_error(mocked_responses):
    url = f"{get_settings().bugzilla_base_url}/rest/bug/42"
    mocked_responses.add(
        responses.GET, url, json={"error": True, "message": "not happy"}
    )

    with pytest.raises(BugzillaClientError) as exc:
        get_client().get_bug(42)

    assert "not happy" in str(exc)


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_get_bug_raises_if_response_has_no_bugs(mocked_responses):
    url = f"{get_settings().bugzilla_base_url}/rest/bug/42"
    mocked_responses.add(responses.GET, url, json={"bugs": []})

    with pytest.raises(BugzillaClientError) as exc:
        get_client().get_bug(42)

    assert "Unexpected response" in str(exc)


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_get_comments_raises_if_response_has_no_bugs(mocked_responses):
    url = f"{get_settings().bugzilla_base_url}/rest/bug/42/comment"
    mocked_responses.add(responses.GET, url, json={"bugs": {"42": {}}})

    with pytest.raises(BugzillaClientError) as exc:
        get_client().get_comments(42)

    assert "Unexpected response" in str(exc)


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_update_bug_uses_a_put(mocked_responses):
    url = f"{get_settings().bugzilla_base_url}/rest/bug/42"
    mocked_responses.add(responses.PUT, url, json={"bugs": [{"id": 42}]})

    get_client().update_bug(42, see_also="http://url.com")

    assert mocked_responses.calls[0].request.body == b'{"see_also": "http://url.com"}'


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_get_bug_comment(mocked_responses, webhook_private_comment_example):
    # given
    bug_url = (
        f"{get_settings().bugzilla_base_url}/rest/bug/%s"
        % webhook_private_comment_example.bug.id
    )
    mocked_responses.add(
        responses.GET,
        bug_url,
        json={"bugs": [webhook_private_comment_example.bug.dict()]},
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

    expanded = get_client().get_bug(webhook_private_comment_example.bug.id)

    # then
    assert expanded.comment.creator == "mathieu@mozilla.org"
    assert expanded.comment.text == "hello"


@pytest.mark.no_mocked_bugzilla
def test_bugzilla_missing_private_comment(
    mocked_responses,
    webhook_private_comment_example,
):
    bug_url = (
        f"{get_settings().bugzilla_base_url}/rest/bug/%s"
        % webhook_private_comment_example.bug.id
    )
    mocked_responses.add(
        responses.GET,
        bug_url,
        json={"bugs": [webhook_private_comment_example.bug.dict()]},
    )
    mocked_responses.add(
        responses.GET,
        bug_url + "/comment",
        json={
            "bugs": {str(webhook_private_comment_example.bug.id): {"comments": []}},
            "comments": {},
        },
    )

    expanded = get_client().get_bug(webhook_private_comment_example.bug.id)

    assert not expanded.comment
