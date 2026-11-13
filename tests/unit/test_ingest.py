"""Tests for the transport-agnostic ingest seam (event injection)."""

import pytest

from jbi.ingest import (
    EventSource,
    InboundEvent,
    IngestOutcome,
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


# --- The claim/lease ledger around processing --------------------------------


class RecordingStore:
    """A dedup store that records settlement and can be told to fail."""

    def __init__(self, status=None, fail_on=None):
        from jbi.dedup import ClaimStatus

        self.status = status or ClaimStatus.CLAIMED
        self.fail_on = fail_on
        self.calls = []

    async def claim(self, delivery_id):
        self.calls.append(("claim", delivery_id))
        if self.fail_on == "claim":
            raise RuntimeError("firestore down")
        return self.status

    async def complete(self, delivery_id):
        self.calls.append(("complete", delivery_id))
        if self.fail_on == "complete":
            raise RuntimeError("firestore down")

    async def release(self, delivery_id):
        self.calls.append(("release", delivery_id))
        if self.fail_on == "release":
            raise RuntimeError("firestore down")


def _jira_envelope(jira_webhook_event, message_id="msg-1"):
    return InboundEvent(
        source=EventSource.JIRA, payload=jira_webhook_event, message_id=message_id
    )


@pytest.mark.asyncio
async def test_a_finished_event_is_marked_done(
    jira_webhook_event, inbound_actions, mocked_jira
):
    mocked_jira.get_issue_remote_links.return_value = []
    store = RecordingStore()

    result = await ingest_event(
        _jira_envelope(jira_webhook_event), inbound_actions, dedup=store
    )

    assert result.outcome == IngestOutcome.IGNORED
    assert store.calls == [("claim", "msg-1"), ("complete", "msg-1")]


@pytest.mark.asyncio
async def test_a_retried_event_is_processed_again_not_dropped_as_a_duplicate(
    jira_webhook_event, inbound_actions, mocked_jira
):
    """The old bare cache recorded an event on first sight, so the redelivery
    of one that had failed was acknowledged as a duplicate and lost."""
    mocked_jira.get_issue_remote_links.side_effect = RuntimeError("jira down")
    envelope = _jira_envelope(jira_webhook_event)

    first = await ingest_event(envelope, inbound_actions)
    mocked_jira.get_issue_remote_links.side_effect = None
    mocked_jira.get_issue_remote_links.return_value = []
    second = await ingest_event(envelope, inbound_actions)

    assert first.outcome == IngestOutcome.RETRY
    assert second.reason != "duplicate delivery"
    assert second.outcome == IngestOutcome.IGNORED  # processed: uncorrelated


@pytest.mark.asyncio
async def test_a_failed_event_releases_its_claim(
    jira_webhook_event, inbound_actions, mocked_jira
):
    mocked_jira.get_issue_remote_links.side_effect = RuntimeError("jira down")
    store = RecordingStore()

    result = await ingest_event(
        _jira_envelope(jira_webhook_event), inbound_actions, dedup=store
    )

    assert result.outcome == IngestOutcome.RETRY
    assert store.calls == [("claim", "msg-1"), ("release", "msg-1")]


@pytest.mark.asyncio
async def test_an_event_another_worker_holds_is_retried_not_processed(
    jira_webhook_event, inbound_actions, mocked_jira
):
    from jbi.dedup import ClaimStatus

    store = RecordingStore(status=ClaimStatus.IN_PROGRESS)

    result = await ingest_event(
        _jira_envelope(jira_webhook_event), inbound_actions, dedup=store
    )

    assert result.outcome == IngestOutcome.RETRY
    assert result.should_acknowledge is False
    assert not mocked_jira.get_issue_remote_links.called
    assert store.calls == [("claim", "msg-1")]


@pytest.mark.asyncio
async def test_a_dedup_store_outage_fails_closed(
    jira_webhook_event, inbound_actions, mocked_jira
):
    """Processing without the guard is what writes a comment twice."""
    store = RecordingStore(fail_on="claim")

    result = await ingest_event(
        _jira_envelope(jira_webhook_event), inbound_actions, dedup=store
    )

    assert result.outcome == IngestOutcome.RETRY
    assert "dedup store unavailable" in result.reason
    assert not mocked_jira.get_issue_remote_links.called


@pytest.mark.asyncio
async def test_failing_to_settle_does_not_mask_the_result(
    jira_webhook_event, inbound_actions, mocked_jira
):
    mocked_jira.get_issue_remote_links.return_value = []
    store = RecordingStore(fail_on="complete")

    result = await ingest_event(
        _jira_envelope(jira_webhook_event), inbound_actions, dedup=store
    )

    assert result.outcome == IngestOutcome.IGNORED


@pytest.mark.asyncio
async def test_events_without_an_id_skip_the_ledger(
    jira_webhook_event, inbound_actions, mocked_jira
):
    """HTTP transports have no id; they must not collide with each other."""
    mocked_jira.get_issue_remote_links.return_value = []
    store = RecordingStore()
    envelope = InboundEvent(source=EventSource.JIRA, payload=jira_webhook_event)

    await ingest_event(envelope, inbound_actions, dedup=store)
    await ingest_event(envelope, inbound_actions, dedup=store)

    assert store.calls == []
