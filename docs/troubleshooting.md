# Troubleshooting

## Bugzilla tickets are not showing up as issues on Jira

As a consumer, you can:

- Open https://jbi.services.mozilla.com/powered_by_jbi/ and check that your project is listed and enabled there
- Open https://jbi.services.mozilla.com/__heartbeat__ and make sure everything is `true`

If you have access to the configured Bugzilla account:

- Open https://bugzilla.mozilla.org/userprefs.cgi?tab=webhooks
- Check that Webhook is still **enabled**
- Check that WebHook is setup to be executed for your product

## Log Explorer Queries Examples

* All incoming WebHooks:

```
jsonPayload.Type="request.summary"
jsonPayload.Fields.path="/bugzilla_webhook"
```

* All action log entries:

```
jsonPayload.Type!="request.summary" AND
(
   NOT jsonPayload.Fields.operation:*  --Entries without `operation` field
   OR (jsonPayload.Fields.operation!="handle" AND jsonPayload.Fields.operation!="ignore")
)
```

* For bugs whose whiteboard contains a certain string:

```
jsonPayload.Fields.bug.whiteboard=~"flowstate"
```

* For a certain Bug number:

```
jsonPayload.Fields.bug.id=1780798
```

* For a certain Jira project:

```
jsonPayload.Fields.action.parameters.jira_project_key="MR"
```


## Jira events are not being processed (Pub/Sub)

Events flow `POST /jira_webhook` -> topic `jbi-<env>-jira-events` -> consumer
(`python -m jbi consume`). Work out which stage is stuck with the metrics:

| Symptom | Look at | Likely cause |
|---|---|---|
| Webhook answers `ignored` | `jbi.publisher.ignored.<reason>.count` | The project has no action with `jira_inbound_enabled`, no changelog in the payload, a bot-authored event, or the stop label. The response `reason` says which. |
| Webhook answers 401 | `jbi.publisher.unauthorized.count` | A native webhook: its secret differs from `JBI_JIRA_WEBHOOK_SECRET`, or the secret is not set (look for `X-Hub-Signature` in the request headers). An Automation rule: wrong `X-Api-Key`. `?token=` is not accepted on this endpoint. |
| Webhook answers 5xx | `jbi.publisher.publish_failed.count` | Pub/Sub unreachable or the service account lacks `roles/pubsub.publisher`. Jira retries. |
| `published` rises, nothing processed | Subscription `oldest_unacked_message_age`, consumer pod logs | Consumer down, or one issue is blocking its ordering key. |
| `jbi.dedup.store_error.count` rises | `dedup.readable` in `/__heartbeat__` | Firestore unreachable or missing `roles/datastore.user`. The consumer nacks everything until it recovers. |
| `jbi.consumer.outcome.retry.count` rises | Consumer logs | Jira or Bugzilla failing; events redeliver with backoff. |
| `jbi.jira.ignored.uncorrelated.count` high | - | The issue has no Bugzilla remote link; expected for unlinked issues. |

### Replaying dead-lettered events

Events that fail `max_delivery_attempts` times are parked on the
`jbi-<env>-jira-events-dead-letter` subscription (alerted on). After fixing the
cause, republish them to the main topic with the same attributes
(`event_source`, `delivery_id`) and ordering key (the issue key), then
acknowledge them. Their ledger entries were released when they failed, so they
are processed again.

### Rotating the webhook secret or key

Native webhook: put the new secret in `JBI_JIRA_WEBHOOK_SECRET` and move the
old one to `JBI_JIRA_WEBHOOK_SECRET_PREVIOUS`, restart, change the secret in
the Jira webhook, then remove `..._PREVIOUS`. Both are accepted meanwhile, so no
delivery is rejected. Automation rule: set the new `JBI_JIRA_API_KEY` and update
the `X-Api-Key` header in the rule; there is a short window in which Jira's
deliveries return 401 and are retried.
