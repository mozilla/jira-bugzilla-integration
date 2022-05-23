``` mermaid
graph TD
    A[Bugzilla Webhook Queue] --> B[Bugzila]
    B[Bugzilla] -->|webhook event| C(JBI)
    C --> |create/update/delete issue| D(Jira)
    C -->|Read Bug| B
    C -->|update see_also| B
    E["config.{ENV}.yaml"] -->|action configuration| C
```
