"""Regression tests against a payload captured from a real Jira Automation rule.

Everything here was verified by pointing a live sandbox rule at JBI through a
tunnel. Each assertion corresponds to something that differed from what the
plan assumed, so this file is the record of what Jira actually sends.

The fixture is pruned: the real delivery carried ~200 mostly-null custom
fields, avatar URLs containing md5 hashes of email addresses, and one custom
field listing unrelated colleagues by name and account id. None of that is
ours to commit, and none of it is read by JBI. The fields the code touches
are kept verbatim.
"""

import json
import pathlib

import pytest

from jbi.jira_inbound.models import JiraWebhookRequest

FIXTURE = (
    pathlib.Path(__file__).parents[2] / "fixtures" / "jira_automation_payload.json"
)


@pytest.fixture
def real_payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def parsed(real_payload):
    return JiraWebhookRequest.model_validate(real_payload)


def test_a_real_automation_payload_parses(parsed):
    """Jira sends ids as JSON *numbers* (`"id": 747979`), and pydantic v2 does
    not coerce int to str -- so this failed validation on a field JBI never
    reads until `coerce_numbers_to_str` was set."""
    assert parsed.issue.key == "AIPLAT-1306"
    assert parsed.issue.id == "747979"


def test_status_category_is_present(parsed):
    """The whole reverse status mapping rests on this (plan section 4.1 and
    risk 13-8), and until this capture it was an assumption."""
    assert parsed.issue.status_category == "indeterminate"


def test_security_arrives_as_null_not_an_empty_object(parsed, real_payload):
    """The feared case was `{"name": ""}`, which the Invariant D guard would
    read as "embargoed" and use to block all free-text write-back on every
    issue. Automation sends a real null, so the guard behaves."""
    assert real_payload["issue"]["fields"]["security"] is None
    assert parsed.issue.fields.security is None

    from jbi.visibility import jira_issue_restriction_reason

    assert jira_issue_restriction_reason(parsed) is None


def test_labels_are_present_so_the_stop_label_needs_no_extra_fetch(parsed):
    assert parsed.issue.fields.labels == ["bugzilla", "jbi-sandbox"]


def test_the_actor_is_identifiable_for_echo_suppression(parsed):
    assert parsed.actor_account_id == "712020:b4494526-035e-4c6a-9d91-d42e37912bc9"


def test_assignee_email_is_hidden_even_though_the_actor_email_is_not(
    parsed, real_payload
):
    """R-11 in practice: the issue's assignee comes back with
    `emailAddress: null`, so tier-2 resolution (use the email Jira gave us)
    has nothing to work with and the identity map is not optional here --
    even though the *actor* block does carry an email."""
    assert parsed.issue.fields.assignee.accountId
    assert parsed.issue.fields.assignee.emailAddress is None
    assert real_payload["user"]["emailAddress"] == "jgauf@mozilla.com"


def test_automations_builtin_body_carries_no_changelog(parsed, real_payload):
    """ "Issue data (Jira format)" nests an empty `issue.changelog`
    (`histories: null`) and sends no top-level `changelog`. JBI can see the
    issue's current state but not which field moved."""
    assert "changelog" not in real_payload
    assert real_payload["issue"]["changelog"]["histories"] is None
    assert parsed.has_changelog is False
    assert parsed.changed_fields() == []


# --- The custom body we ask operators to configure --------------------------


def _custom_body_rendering(**overrides):
    """What `.local-harness/automation-custom-body.json` renders to.

    Automation fills unset smart values with empty strings, so this is the
    sparse case: no security level, no assignee, no resolution.
    """
    payload = {
        "webhookEvent": "jira:issue_updated",
        "user": {"accountId": "712020:b449", "displayName": "John Gauf"},
        "issue": {
            "id": "747979",
            "key": "AIPLAT-1306",
            "fields": {
                "summary": "a summary",
                "project": {"key": "AIPLAT"},
                "status": {
                    "name": "In Progress",
                    "statusCategory": {"key": "indeterminate"},
                },
                "resolution": {"name": ""},
                "priority": {"name": "None"},
                "assignee": {"accountId": "", "displayName": "", "emailAddress": ""},
                "security": {"name": ""},
                "labels": ["bugzilla"],
            },
        },
        "changelog": {
            "items": [
                {"field": "__rule__"},
                {"field": "status", "fromString": "To Do", "toString": "In Progress"},
            ]
        },
    }
    payload["issue"]["fields"].update(overrides)
    return payload


def test_custom_body_supplies_the_changelog_the_builtin_format_lacks():
    """The whole reason for a custom body: without it JBI cannot tell which
    field moved and refuses the event."""
    event = JiraWebhookRequest.model_validate(_custom_body_rendering())

    assert event.has_changelog is True
    assert "status" in event.changed_fields()


def test_the_sentinel_item_keeps_the_json_valid_and_writes_nothing():
    """A leading `__rule__` item lets every real item be comma-prefixed, so
    the array stays valid JSON however many sections render. It must not be
    mistaken for a field to write back."""
    from jbi.writeback import is_writeback_allowed

    event = JiraWebhookRequest.model_validate(_custom_body_rendering())

    assert "__rule__" in event.changed_fields()
    assert is_writeback_allowed("__rule__") is False


def test_empty_smart_values_do_not_look_like_a_security_level():
    """The trap this template would otherwise spring: Automation renders an
    unset `{{issue.security.name}}` as "", and a present-but-empty security
    object would make Invariant D block all free-text write-back on every
    issue."""
    from jbi.visibility import jira_issue_restriction_reason

    event = JiraWebhookRequest.model_validate(_custom_body_rendering())

    assert event.issue.fields.security.name is None
    assert jira_issue_restriction_reason(event) is None


def test_empty_assignee_is_not_mistaken_for_a_user():
    event = JiraWebhookRequest.model_validate(_custom_body_rendering())

    assert event.issue.fields.assignee.accountId is None


def test_a_real_security_level_in_a_custom_body_still_blocks():
    """The normalisation must not defeat the guard when a level *is* set."""
    from jbi.visibility import jira_issue_restriction_reason

    event = JiraWebhookRequest.model_validate(
        _custom_body_rendering(security={"name": "Security Team Only"})
    )

    assert "Security Team Only" in jira_issue_restriction_reason(event)
