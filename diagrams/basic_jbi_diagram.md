``` mermaid
graph TD
    subgraph bugzilla services
        A[Bugzilla] ---|bugzilla event| B[(Webhook Queue)]
        B --- C[Webhook Push Service]
    end
    D --> |create/update/delete issue| E[Jira]
    D<-->|Read Bug| A
    D -->|update see_also| A
    subgraph jira-bugzilla-integration
        C -->|webhook event| D{JBI}
        F["config.{ENV}.yaml"] ---|action configuration| D
    end
```
