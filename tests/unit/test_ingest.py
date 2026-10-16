"""Tests for the transport-agnostic ingest seam (event injection)."""

import pytest

from jbi.ingest import (
    EventSource,
    InboundEvent,
    IngestOutcome,
    already_delivered,
    ingest_event,
    reset_delivery_cache,
)


@pytest.fixture(autouse=True)
def clean_delivery_cache():
    reset_delivery_cache()
    yield
    reset_delivery_cache()


@pytest.fixture
def inbound_actions(action_factory):
    from jbi.models import Actions

    return Actions(
        root=[
            action_factory(
                whiteboard_tag="devtest",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
            )
        ]
    )


# --- Acknowledgement decisions ---------------------------------------------


@pytest.mark.asyncio
async def test_ignored_events_are_acknowledged(
    jira_webhook_event, inbound_actions, mocked_jira
):
    """The decisive property for a broker: an event JBI does not act on is
    normal traffic. Nacking it would redeliver it forever."""
    mocked_jira.get_issue_remote_links.return_value = []

    result = await ingest_event(
        InboundEvent(source=EventSource.JIRA, payload=jira_webhook_event),
        inbound_actions,
    )

    assert result.outcome == IngestOutcome.IGNORED
    assert result.should_acknowledge is True


@pytest.mark.asyncio
async def test_unexpected_failure_asks_for_redelivery(
    jira_webhook_event, inbound_actions, mocked_jira
):
    mocked_jira.get_issue_remote_links.side_effect = RuntimeError("jira is down")

    result = await ingest_event(
        InboundEvent(source=EventSource.JIRA, payload=jira_webhook_event),
        inbound_actions,
    )

    assert result.outcome == IngestOutcome.RETRY
    assert result.should_acknowledge is False


@pytest.mark.asyncio
async def test_handled_event_is_acknowledged(
    jira_webhook_event,
    inbound_actions,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    mocked_jira.get_issue_remote_links.return_value = [{"globalId": "654321"}]
    mocked_bugzilla.get_bug.return_value = bug_factory(
        id=654321,
        whiteboard="[devtest]",
        assigned_to="owner@mozilla.com",
        see_also=[f"{settings.jira_base_url}browse/JBI-234"],
    )

    result = await ingest_event(
        InboundEvent(source=EventSource.JIRA, payload=jira_webhook_event),
        inbound_actions,
    )

    assert result.outcome == IngestOutcome.HANDLED
    assert result.should_acknowledge is True


@pytest.mark.asyncio
async def test_bugzilla_event_without_a_queue_runs_directly(
    bugzilla_webhook_request, actions, mocked_bugzilla, mocked_jira
):
    """A broker transport passes no dead-letter queue, because the
    subscription owns retries; stacking both would multiply redeliveries."""
    mocked_bugzilla.get_bug.return_value = bugzilla_webhook_request.bug

    result = await ingest_event(
        InboundEvent(source=EventSource.BUGZILLA, payload=bugzilla_webhook_request),
        actions,
    )

    assert result.outcome == IngestOutcome.HANDLED


@pytest.mark.asyncio
async def test_bugzilla_event_with_a_queue_keeps_todays_behaviour(
    bugzilla_webhook_request, actions, mocked_bugzilla, dl_queue
):
    """The webhook transport still uses the dead-letter queue, so a failure
    is captured there rather than asking the transport to retry."""
    mocked_bugzilla.get_bug.side_effect = RuntimeError("boom")

    result = await ingest_event(
        InboundEvent(source=EventSource.BUGZILLA, payload=bugzilla_webhook_request),
        actions,
        queue=dl_queue,
    )

    assert result.outcome == IngestOutcome.PERMANENT_FAILURE
    assert result.should_acknowledge is True
    assert await dl_queue.size() == 1


# --- Duplicate suppression --------------------------------------------------


def test_message_ids_are_remembered():
    assert already_delivered("m-1") is False
    assert already_delivered("m-1") is True
    assert already_delivered("m-2") is False


def test_missing_message_id_is_never_a_duplicate():
    """HTTP transports have no message id; they must not collide with
    each other on `None`."""
    assert already_delivered(None) is False
    assert already_delivered(None) is False


def test_delivery_cache_is_bounded():
    from jbi.ingest import _SEEN_MAX, _SEEN_MESSAGE_IDS

    for i in range(_SEEN_MAX + 50):
        already_delivered(f"m-{i}")

    assert len(_SEEN_MESSAGE_IDS) <= _SEEN_MAX
    # The oldest ids were evicted, so they are no longer recognised. This is
    # the documented limit of an in-process cache, asserted rather than
    # assumed.
    assert already_delivered("m-0") is False


@pytest.mark.asyncio
async def test_redelivered_message_is_ignored_without_reprocessing(
    jira_webhook_event, inbound_actions, mocked_jira
):
    mocked_jira.get_issue_remote_links.return_value = []
    envelope = InboundEvent(
        source=EventSource.JIRA, payload=jira_webhook_event, message_id="msg-42"
    )

    first = await ingest_event(envelope, inbound_actions)
    call_count = mocked_jira.get_issue_remote_links.call_count
    second = await ingest_event(envelope, inbound_actions)

    assert first.outcome == IngestOutcome.IGNORED
    assert second.outcome == IngestOutcome.IGNORED
    assert second.reason == "duplicate delivery"
    # The second delivery did no work at all.
    assert mocked_jira.get_issue_remote_links.call_count == call_count
