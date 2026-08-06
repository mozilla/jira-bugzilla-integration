# Troubleshooting

## Bugzilla tickets are not showing up as issues on Jira

As a consumer, you can:

- Open https://jbi.services.mozilla.com/powered_by_jbi/ and check that your project is listed and enabled there
- Open https://jbi.services.mozilla.com/__heartbeat__ and make sure everything is `true`

If you have access to the configured Bugzilla account:

- Open https://bugzilla.mozilla.org/userprefs.cgi?tab=webhooks
- Check that Webhook is still **enabled**
- Check that WebHook is setup to be executed for your product

If the ticket still has no Jira link, an earlier event for that bug may have
failed and be waiting in the dead letter queue. While a bug has pending items,
its later events are queued as well instead of being processed right away.
Making a new change to the bug (eg. removing and re-adding the whiteboard tag)
makes JBI reprocess the pending events first. If they fail again, the retry job
picks them up later, and they are dropped once they are older than
`DL_QUEUE_RETRY_TIMEOUT_DAYS`.

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

