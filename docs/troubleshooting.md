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

