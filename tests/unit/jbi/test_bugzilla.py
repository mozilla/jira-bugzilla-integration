"""
Module for testing src/jbi/configuration.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.jbi import bugzilla


def test_get_jira_labels_without_whiteboard():
    test_bug = bugzilla.BugzillaBug(id=0, whiteboard="")
    jira_labels = test_bug.get_jira_labels()
    assert ["bugzilla"] == jira_labels


def test_get_jira_labels_with_space():
    test_bug = bugzilla.BugzillaBug(id=0, whiteboard="[test whiteboard]")
    jira_labels = test_bug.get_jira_labels()
    expected = ["bugzilla", "test.whiteboard", "[test.whiteboard]"]
    assert expected == jira_labels


def test_get_jira_labels_without_space():
    test_bug = bugzilla.BugzillaBug(id=0, whiteboard="[test-whiteboard]")
    jira_labels = test_bug.get_jira_labels()
    expected = ["bugzilla", "test-whiteboard", "[test-whiteboard]"]
    assert expected == jira_labels


def test_get_jira_labels_multiple():
    test_bug = bugzilla.BugzillaBug(
        id=0, whiteboard="[test whiteboard][test-no-space][test-both space-and-not"
    )
    jira_labels = test_bug.get_jira_labels()
    expected = [
        "bugzilla",
        "test.whiteboard",
        "test-no-space",
        "test-both.space-and-not",
        "[test.whiteboard]",
        "[test-no-space]",
        "[test-both.space-and-not]",
    ]
    assert expected == jira_labels


@pytest.mark.parametrize(
    "see_also,expected",
    [
        ([], None),
        (["foo"], None),
        (["fail:/format"], None),
        (["foo", "http://jira.net/123"], "123"),
        (["http://bugzilla.org/123"], None),
        (["http://jira.com"], None),
        (["http://mozilla.jira.com/"], None),
        (["http://mozilla.jira.com/123"], "123"),
        (["http://mozilla.jira.com/123/"], "123"),
        (["http://mozilla.jira.com/ticket/123"], "123"),
        (["http://atlassian.com/ticket/123"], "123"),
        (["http://mozilla.jira.com/123", "http://mozilla.jira.com/456"], "123"),
    ],
)
def test_extract_see_also(see_also, expected):
    test_bug = bugzilla.BugzillaBug(id=0, see_also=see_also)
    assert test_bug.extract_from_see_also() == expected


def test_payload_changes(webhook_request_example):
    assert webhook_request_example.event.changed_fields() is None

    webhook_request_example.event = bugzilla.BugzillaWebhookEvent.parse_obj(
        {
            "action": "modify",
            "routing_key": "bug.modify",
            "target": "bug",
            "changes": [
                {"field": "assigned_to", "removed": "", "added": "dtownsend"},
                {"field": "status", "removed": "UNCONFIRMED", "added": "NEW"},
            ],
            "time": "2022-03-23T20:10:17.495000+00:00",
            "user": {
                "id": 123456,
                "login": "nobody@mozilla.org",
                "real_name": "Nobody [ :nobody ]",
            },
        }
    )
    assert webhook_request_example.event.changed_fields() == ["assigned_to", "status"]

    webhook_request_example.event = bugzilla.BugzillaWebhookEvent.parse_obj(
        {
            "action": "modify",
            "routing_key": "bug.modify:assigned_to,status",
            "target": "bug",
            "time": "2022-03-23T20:10:17.495000+00:00",
            "user": {
                "id": 123456,
                "login": "nobody@mozilla.org",
                "real_name": "Nobody [ :nobody ]",
            },
        }
    )
    assert webhook_request_example.event.changed_fields() == ["assigned_to", "status"]
