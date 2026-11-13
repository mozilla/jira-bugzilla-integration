"""Echo suppression: events caused by JBI's own Jira account are ignored.

JBI's writes to Jira come back as events authored by the account whose
credentials it uses (`JIRA_USERNAME`). Left alone they would be processed as if
a person had made them. The account is recognised by id (looked up from the
credentials, or configured) and by email, because a payload may carry either.
"""

import logging

import pytest

from jbi.jira_inbound import handler
from jbi.jira_inbound.handler import is_bot_authored, resolve_bot_identity

BOT_ID = "712020:bot-account-id"
BOT_EMAIL = "jbi-bot@mozilla.com"


@pytest.fixture(autouse=True)
def clean_identity(monkeypatch):
    """No leakage of a looked-up identity between tests, and a known account."""
    monkeypatch.setattr(handler, "_resolved_bot_account_id", None)
    monkeypatch.setattr(handler.settings, "jira_bot_account_id", None)
    monkeypatch.setattr(handler.settings, "jira_username", BOT_EMAIL)


def _event(factory, **user):
    """An event from an ordinary person, overridden by the given user fields."""
    fields = {"accountId": "someone-else", "emailAddress": "p@x.org", **user}
    return factory(**{f"user__{key}": value for key, value in fields.items()})


# --- recognising the bot ----------------------------------------------------


def test_a_person_is_not_the_bot(jira_webhook_request_factory):
    assert not is_bot_authored(_event(jira_webhook_request_factory))


def test_the_configured_account_id_matches(monkeypatch, jira_webhook_request_factory):
    monkeypatch.setattr(handler.settings, "jira_bot_account_id", BOT_ID)

    assert is_bot_authored(_event(jira_webhook_request_factory, accountId=BOT_ID))


def test_the_email_of_the_credentials_matches(jira_webhook_request_factory):
    """`JIRA_USERNAME` is what the user asked for: it identifies the account."""
    assert is_bot_authored(_event(jira_webhook_request_factory, emailAddress=BOT_EMAIL))


def test_the_email_comparison_ignores_case_and_whitespace(
    jira_webhook_request_factory,
):
    event = _event(jira_webhook_request_factory, emailAddress=" JBI-Bot@Mozilla.com ")

    assert is_bot_authored(event)


def test_a_comment_author_counts_when_there_is_no_user(
    jira_webhook_request_factory,
):
    """A comment payload may carry only the author."""
    event = jira_webhook_request_factory(
        with_comment=True,
        user=None,
        comment__author__accountId="x",
        comment__author__emailAddress=BOT_EMAIL,
    )

    assert is_bot_authored(event)


def test_a_username_that_is_not_an_email_is_not_compared(
    monkeypatch, jira_webhook_request_factory
):
    """Some deployments use a bare username; comparing it to an email field
    would never match, and an empty one must not match an absent email."""
    monkeypatch.setattr(handler.settings, "jira_username", "")

    assert not is_bot_authored(_event(jira_webhook_request_factory, emailAddress=""))


def test_a_payload_without_identity_is_not_the_bot(jira_webhook_request_factory):
    event = jira_webhook_request_factory(user=None, actor=None)

    assert not is_bot_authored(event)


# --- looking the account up from the credentials ----------------------------


def test_the_account_id_is_looked_up_from_the_credentials(
    mocked_jira, jira_webhook_request_factory
):
    mocked_jira.myself.return_value = {"accountId": BOT_ID}

    assert resolve_bot_identity() == BOT_ID
    assert is_bot_authored(_event(jira_webhook_request_factory, accountId=BOT_ID))


def test_a_configured_account_id_is_not_looked_up(monkeypatch, mocked_jira):
    monkeypatch.setattr(handler.settings, "jira_bot_account_id", BOT_ID)

    assert resolve_bot_identity() == BOT_ID
    assert not mocked_jira.myself.called


def test_a_failed_lookup_is_logged_and_does_not_stop_startup(
    mocked_jira, capturelogs, jira_webhook_request_factory
):
    mocked_jira.myself.side_effect = RuntimeError("jira down")

    with capturelogs.for_logger("jbi.jira_inbound.handler").at_level(logging.WARNING):
        assert resolve_bot_identity() is None

    assert any("look up" in r.message for r in capturelogs.records)
    # The email still protects against the loop.
    assert is_bot_authored(_event(jira_webhook_request_factory, emailAddress=BOT_EMAIL))


@pytest.mark.parametrize("reply", [{}, {"accountId": ""}, None, {"accountId": 7}])
def test_an_unusable_lookup_reply_is_not_trusted(mocked_jira, reply):
    mocked_jira.myself.return_value = reply

    assert resolve_bot_identity() is None


# --- both gates use it ------------------------------------------------------


def test_the_publisher_skips_the_bots_own_events(
    jira_webhook_request_factory, action_factory
):
    from jbi.jira_inbound.preflight import SkipReason, preflight
    from jbi.models import Actions

    actions = Actions(
        root=[
            action_factory(
                whiteboard_tag="devtest",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
            )
        ]
    )

    skip = preflight(
        _event(jira_webhook_request_factory, emailAddress=BOT_EMAIL), actions
    )

    assert skip.reason == SkipReason.BOT_AUTHORED
