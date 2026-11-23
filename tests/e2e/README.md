# End-to-end smoke tests (plan D12)

Live tests against a **Jira sandbox** and a **Bugzilla dev instance**, through a
real, running JBI. They are not part of `make test`: without the variables
below they skip, and they refuse to start unless Jira looks like a sandbox and
Bugzilla like a dev instance.

## What is real and what is stood in for

| | |
|---|---|
| JBI, its config, both APIs | real |
| BMO's webhook | stood in for: the test posts the event JBI would receive |
| The Jira webhook | stood in for: the test builds an event from the live issue and posts it to `/jira_webhook` (in the Automation-body shape, which JBI also accepts; the native webhook sends the same fields and more) |

The producers cannot be pointed at a developer machine, so the tests cover
everything from "event arrives" onwards. The Jira events name a separate
initiator (`e2e-human`) because the sandbox has one account; with JBI's own
id, the echo gate (Invariant C) would correctly ignore them.

## Running

1. Start JBI against the sandbox with a config that onboards your tag
   (`jira_inbound_enabled: true`). With the local harness:
   `bash .local-harness/gen_config.sh && bash .local-harness/run_jbi.sh`.
2. In another shell, with the sandbox credentials loaded
   (`. .local-harness/load_env.sh`):

```sh
export JBI_E2E_BASE_URL=http://localhost:8000
export JBI_E2E_WHITEBOARD_TAG=jbi-sandbox
export JBI_E2E_JIRA_PROJECT=AIPLAT
export JBI_E2E_BUGZILLA_PRODUCT=Testing
export JBI_E2E_BUGZILLA_COMPONENT=General
# Project-specific names, if they differ from the defaults:
export JBI_E2E_REVIEW_STATUS="ENG REVIEW"   # default: In Review
export JBI_E2E_CHANGES_STATUS="In Progress" # default: In Progress
export JBI_E2E_DONE_STATUS=Closed           # a done-category status
export JBI_E2E_MILESTONE=Future             # an active milestone in the product
make smoke
```

`make smoke` disables `pytest-dotenv`, which otherwise replaces real
credentials with the fakes in `.env.example`.

Optional:

- `JBI_E2E_RESTRICTED_GROUP` - a Bugzilla group your account can put a bug
  in; enables the restricted-bug scenario.
- `JBI_E2E_KEEP=1` - leave the created bugs and issues in place to inspect.
- `JBI_E2E_ALLOW_NON_SANDBOX=1` - skip the host safety check. Don't.

## What they cover

| Test | Proves |
|---|---|
| scenario 1 | a tagged bug becomes one issue, linked both ways (PRD 1) |
| scenario 2 | an untagged bug creates nothing (PRD 2) |
| scenario 3 | a Jira close reaches the bug with the mapped resolution, and the resulting BMO event does not bounce back (PRD 3, Invariant C) |
| scenario 4 | a Jira comment reaches the bug once, attributed (PRD 4) |
| scenario 4b | a restricted bug is never synced (R-12), when a group is configured |
| invariant A | re-syncing a linked bug creates no duplicate |
| review moves | D4/D5: request -> review, `review-` -> out, `review+` -> no move |
| stop label | D15: pause and resume |
| metabug | Phase 2: tagged metabug -> Epic, nesting -> "blocks" link, child parented |
| release data | Phase 2: `bz-status-`/`bz-milestone-` labels, swapped on change |
| depends on | a metabug's Depends On makes an unparented issue a child of its Epic, with no link to its own parent |

Each run creates a handful of bugs and issues and cleans them up afterwards:
issues are deleted; bugs, which Bugzilla cannot delete, are resolved INVALID.
A full run takes about three minutes.
