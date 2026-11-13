# Queue Inbound Jira Events Instead of Processing Them in the Webhook

- Status: Proposed
- Date: 2026-10-27

Amends ADR 005, which added the Pub/Sub pull consumer but left
`POST /jira_webhook` processing events inline.

## Context and Problem Statement

With the consumer in place, the Jira webhook was still the one path that did
all the work inside the HTTP request: three Jira/Bugzilla round trips per event
while Jira Automation waited. Jira Automation forwards far more events than JBI
acts on, so most of that work ends in "ignored", and a Jira or Bugzilla outage
turns into failed webhook deliveries that are not reliably retried.

## Decision

**1. The webhook validates and queues.** When `PUBSUB_TOPIC_ID` is set,
`/jira_webhook` runs the publish-time gates (`jbi/jira_inbound/preflight.py`)
and publishes the request body, untouched, to the topic. The consumer decodes it
with the same models and runs the existing `execute_jira_event`. Unset, the
endpoint behaves as before, which is also the rollback.

**2. The gates look at the payload and the configuration only.** Valid issue
key and project, not authored by JBI, the project has an action with
`jira_inbound_enabled`, a changelog is present (unless an action allows
otherwise), and the stop label is not set. No Jira or Bugzilla call, so
publishing keeps working when either is down. Per-action rules skip an event
only when *every* action that could handle it agrees, because the publisher
does not know which action a bug will match. Correlation to a bug, the bug
fetch and whiteboard matching stay in the consumer. Dropping an event at the
publisher cannot be undone, so every doubt resolves to "publish".

**3. Per-issue ordering.** The Jira issue key is the ordering key; the
subscription has message ordering enabled and the publisher uses the regional
endpoint. A failed publish pauses its key in the client, so the publisher calls
`resume_publish` and returns 5xx, and Jira Automation retries.

**4. Idempotency through a claim/lease ledger** (`jbi/dedup.py`), in Firestore
in production. `delivery_id` is a hash of the event (issue, event name,
timestamp, comment id, changes), so a Jira retry, which arrives as a *new*
Pub/Sub message, maps to the same ledger entry. An event is claimed before it is
processed and marked done after; a failed attempt releases the claim. A bare
"seen" flag was rejected: it loses the event when a worker fails after
recording it. The earlier in-process cache had exactly that flaw, since the
redelivery of an event that returned `RETRY` was acknowledged as a duplicate.
If Firestore is unreachable the consumer fails closed (nack), because
processing without the guard is what posts a comment twice.

**5. The Jira endpoint authenticates by header or by signature, never by URL.**
A native Jira webhook cannot send custom headers, so it is configured with a
secret and signs each delivery: `X-Hub-Signature: sha256=<hex>`, the
HMAC-SHA256 of the raw body (`jbi/jira_inbound/signature.py`, secret
`JBI_JIRA_WEBHOOK_SECRET`; `..._PREVIOUS` is accepted too, for rotation). A
Jira Automation rule can send `X-Api-Key` (`JBI_JIRA_API_KEY`, falling back to
`JBI_API_KEY`). When a signature header is present only the signature is
checked, so a bad signature is not rescued by a valid key. `?token=` and basic
auth are rejected, and `request.summary` redacts secrets in query strings. The
signature is verified over the bytes as received, in constant time, and only
`sha256` is accepted.

## Consequences

- Residual duplicate window: a crash after the Bugzilla write but before the
  ledger is updated re-runs the event once the lease expires, which can repeat
  a single comment. Embedding the Jira comment id in the copied text and
  checking it before posting would close it.
- Ordering means a message that keeps failing blocks later events for the same
  issue until it succeeds or is dead-lettered. The oldest-unacked-age alert
  exists for this.
- Jira Automation deliveries are not themselves ordered; the publisher cannot
  reorder two webhooks that race. The consumer re-reads live state, so the final
  state converges.
- Ordering and the dead-letter policy are fixed when the subscription is
  created; changing them means recreating it.

## Metrics

Publisher: `jbi.publisher.{received,published,invalid,publish_failed,publish_resumed,unauthorized}.count`,
`jbi.publisher.ignored.<reason>.count`, `jbi.publisher.publish.timer`.
Consumer: `jbi.consumer.{received,acked,nacked,undecodable,redelivered}.count`,
`jbi.consumer.outcome.<outcome>.count`, `jbi.consumer.process.timer`,
`jbi.consumer.end_to_end_lag.timer`. Ledger: `jbi.dedup.{claimed,duplicate,in_progress,takeover,store_error}.count`,
`jbi.dedup.claim.timer`. Handler: `jbi.jira.ignored.<reason>.count`.
