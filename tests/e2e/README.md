# End-to-end scenarios (plan D12)

These exercise the four PRD acceptance scenarios against **real** systems: a
Jira sandbox project and a Bugzilla test component. They are excluded from
`make test` (`testpaths = tests/unit` in `pyproject.toml`) and skip themselves
unless the credentials below are present, so they can never run by accident
against production data.

## Running them

```sh
export JBI_E2E_JIRA_PROJECT=SANDBOX          # a throwaway Jira project
export JBI_E2E_BUGZILLA_PRODUCT="Invalid Bugs"
export JBI_E2E_BUGZILLA_COMPONENT="General"
export JBI_E2E_BASE_URL=http://localhost:8000 # a running JBI
# plus the usual JIRA_*/BUGZILLA_* credentials from .env

uv run pytest tests/e2e -v
```

## Status

The harness and the four scenario bodies are **stubbed**: each test documents
the exact steps and assertions, and fails with a clear message if run before
being filled in. They were written alongside the implementation so that
wiring them up is a matter of sandbox access, not design work.

Scenario coverage (PRD section 8):

| Scenario | What it proves |
|---|---|
| 1 | An in-scope bug creates a Jira issue with its fields mapped |
| 2 | An out-of-scope bug creates nothing (R-01) |
| 3 | A status change flows in both directions (Invariant C: once each way) |
| 4 | A Jira comment reaches the bug, attributed, and a restricted bug is skipped |

Each scenario asserts the 5-minute SLA, and scenarios 3 and 4 assert the
loop terminates: exactly one BMO write and zero return writes to Jira.
