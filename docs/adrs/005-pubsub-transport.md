# Deliver Events via a Pub/Sub Pull Consumer, Behind a Single Ingest Seam

- Status: Accepted
- Date: 2026-09-16 (revised same day: pull, not push)

Supersedes the transport decision in
`docs/bmo-jira-bidirectional-integration-plan.md` §1 ("Push over polling" via a
Jira Automation web request straight to `/jira_webhook`). Amends ADR 004, which
remains correct about *where* the reverse capability lives.

## Context and Problem Statement

Phase 1 delivered events straight over HTTP: BMO's webhook to
`/bugzilla_webhook`, and a Jira Automation rule to `/jira_webhook`. That works,
and it was the right thing to prove the pipeline with, but it left JBI owning
problems a message broker already solves:

- **Retries.** JBI has a file-based dead-letter queue and a separate retry
  runner. It is typed to Bugzilla payloads, so it cannot hold Jira events at
  all, and it assumes a single instance — both recorded as Phase 1
  limitations.
- **Buffering.** If JBI is down, a webhook delivery is simply lost; BMO and
  Jira Automation do not retry indefinitely.
- **Fan-out.** Only one consumer can receive an HTTP webhook.

Separately, each transport had wired itself into the core its own way, so
adding a fourth would have meant a fourth bespoke wiring.

## Decision

**1. One ingest seam.** Every transport calls
`jbi.ingest.ingest_event(InboundEvent) -> IngestResult`. The envelope carries
the source, the typed payload, and broker metadata (message id, delivery
attempt). The result carries an *acknowledgement decision*:
`HANDLED`, `IGNORED`, `RETRY`, `PERMANENT_FAILURE`.

**2. Pub/Sub pull, not push.** A streaming-pull consumer
(`python -m jbi consume`) runs as its own process beside the web service.
Pull gives explicit control over concurrency and ordering: flow control
bounds how many messages are in flight, and ordering keys keep one bug's
events in sequence.

*This reverses the first version of this ADR, which chose push.* Push was
argued for on the grounds that it keeps JBI stateless and turns delivery
policy into configuration, which remains true — but it gives up ordering
control, and the reverse conflict rule depends on sequence, since it
compares against "the value before this change".

**3. Both sources through one topic.** Bugzilla and Jira events share the
transport, so there is one retry story and one dead-letter topic. The direct
HTTP endpoints remain for compatibility and local testing.

**4. Duplicate suppression on the message id**, bounded and in-process.

## Consequences

**Acknowledgement is an explicit ack/nack**, which forces three rules that
are easy to get wrong:

- An event JBI deliberately ignores must **ack**. Nacking "out of scope" or
  "self-authored" would redeliver it until it expired — and those are the
  *majority* of events.
- An undecodable payload also acks, with a log. Redelivery cannot fix data
  that is not UTF-8, not JSON, or does not match the schema.
- Only genuine transient failures nack. An unexpected exception nacks too:
  the state is unknown, so redelivering is safer than dropping, and duplicate
  suppression covers the replay.

**A second deployment unit.** The consumer is a separate process with its own
lifecycle: SIGTERM handling, `await_callbacks_on_shutdown`, a bounded pull
window that must sit below both the lease duration and any job task timeout,
and a grace period so in-flight acks complete.

**Two operational constraints inherited from the reference implementation**,
recorded because they were learned there the hard way:

- Flow control must never be `max_messages=1`. One slot serialises every
  ordering key, so a backlog cannot drain before the pull window closes and
  held ordered messages are stranded.
- Messages still held for an ordering key when the window closes are
  abandoned by the client and redelivered later. The client logs this without
  context, so JBI's own warning names it and says which knob to turn.

**The broker's dead-letter topic replaces the file queue** for
broker-delivered events, which resolves the two Phase 1 limitations above.
The file queue stays for the legacy webhook path.

**At-least-once delivery makes duplicates normal.** Most of the pipeline is
already idempotent — Invariant A stops duplicate issues, read-before-write
stops duplicate field writes — but a redelivered *comment* event would post
twice, and no field-level check catches that. Hence the message-id cache.

**The cache is a compromise we are naming, not hiding.** It is per-process
and forgotten on restart, so across two consumer replicas a redelivered
comment can still double-post. The reference implementation solves this with
a Firestore-backed idempotency service keyed on `delivery_id` with a 24h
TTL — the right shape, and the obvious next step if JBI ever runs more than
one consumer. Until then the constraint is: **run a single consumer
replica.** Tracked as plan §13-11.

**Ordering is not guaranteed** unless the publisher sets an ordering key.
Out-of-order events weaken the conflict rule that compares against "the value
before this change". Recommended key: the bug id. Tracked as §13-12.

**No inbound HTTP, so no shared secret for this path.** The consumer
authenticates to Pub/Sub with application default credentials and
`roles/pubsub.subscriber`. This is a clear advantage of pull over push, which
would have needed either a token in the URL (weaker than header auth, since
URLs reach logs) or OIDC verification. The `?token=` query auth added for
push remains only for the Bugzilla webhook, which can carry a URL and nothing
else.

## Alternatives Considered

**Push subscription.** Keeps JBI a single stateless service and needs no
consumer process — genuinely simpler to deploy. Rejected: acknowledgement
becomes an HTTP status code, concurrency is whatever the subscription decides,
and ordering control is lost. It also needs a secret in the push URL or OIDC
verification, where pull needs neither.

*This ADR originally chose push and was revised the same day.* The ingest
seam is what made the reversal cheap: only the transport module and its tests
changed, and the ack semantics carried over unaltered, which is some evidence
the seam is drawn in the right place.

**Keep direct webhooks, add retries in JBI.** Rejected: it means reimplementing
backoff, dead-lettering and buffering that the broker already provides, and it
would not fix the queue's single-instance assumption.

**A separate topic per source.** Rejected: two subscriptions, two retry
policies and two dead-letter topics to keep in step, for no gain — the seam
distinguishes sources from one message attribute.
