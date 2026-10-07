"""The four PRD acceptance scenarios, end to end (plan D12).

Stubbed harness: each test states its steps and assertions precisely so that
filling it in is mechanical once sandbox access exists. See README.md.
"""

import pytest


@pytest.mark.e2e
def test_scenario_1_in_scope_bug_creates_a_mapped_jira_issue(sandbox, not_implemented):
    """PRD scenario 1.

    Steps:
      1. File a bug in the sandbox Product/Component with the pilot
         whiteboard tag, above the configured priority/severity threshold.
      2. Wait for JBI to process the Bugzilla webhook (<= SLA_SECONDS).

    Assertions:
      - exactly one Jira issue exists in the sandbox project for that bug;
      - its summary, status, assignee, priority and severity match the bug;
      - the bug's `see_also` links to the issue and the issue carries the
        Bugzilla remote link (this is what the reverse direction correlates
        on, so scenario 3 depends on it).
    """
    not_implemented("file a bug and assert the created issue's fields")


@pytest.mark.e2e
def test_scenario_2_out_of_scope_bug_creates_nothing(sandbox, not_implemented):
    """PRD scenario 2 (R-01 / R-04).

    Steps:
      1. File a bug with the pilot tag but in a Product/Component outside
         `sync_products_components`.
      2. File a second bug in scope but below `min_priority`.

    Assertions:
      - no Jira issue is created for either bug;
      - JBI logged both as ignored, with the reason (scope vs. threshold),
        so an operator can tell the two cases apart.
    """
    not_implemented("file out-of-scope and below-threshold bugs")


@pytest.mark.e2e
def test_scenario_3_status_flows_in_both_directions_and_stops(sandbox, not_implemented):
    """PRD scenario 3, plus Invariant C end to end.

    Steps:
      1. Start from a synced bug/issue pair (scenario 1).
      2. Change the bug's status in BMO; wait for the forward sync.
      3. Move the Jira issue to a `done`-category status with a resolution;
         wait for the Automation rule to post to /jira_webhook.

    Assertions:
      - after step 2 the Jira issue's status matches the configured mapping;
      - after step 3 the bug is RESOLVED with the resolution recovered from
        the inverted `resolution_map` (never from inverting `status_map`);
      - **the loop terminates**: the BMO write in step 3 fires exactly one
        Bugzilla webhook, which JBI ignores as self-authored, and zero
        further writes reach Jira. Assert on JBI's request log, not just on
        the final values, since a loop that converges still burns quota.
      - both directions complete within SLA_SECONDS.
    """
    not_implemented("drive a status change each way and assert the loop stops")


@pytest.mark.e2e
def test_scenario_4_comment_reaches_the_bug_with_attribution(sandbox, not_implemented):
    """PRD scenario 4 (R-12).

    Steps:
      1. Comment on the Jira issue of a synced, public bug.
      2. Comment on the Jira issue of a synced but group-restricted bug.

    Assertions:
      - the public bug receives the comment, prefixed "from Jira, by <name>",
        posted by JBI's service account (never impersonating the author);
      - the restricted bug receives nothing, and the skip is logged;
      - redelivering the same Jira event posts no second copy.
    """
    not_implemented("post Jira comments on a public and a restricted bug")


@pytest.mark.e2e
def test_invariant_a_resyncing_a_linked_bug_creates_no_duplicate(
    sandbox, not_implemented
):
    """Permanent regression guard (Invariant A), end to end.

    Re-add the whiteboard tag to a bug that is already linked, and assert the
    Jira project still contains exactly one issue for it. This is the failure
    mode that would flood Jira at pilot rollout, since the pilot component
    already holds many previously-linked bugs.
    """
    not_implemented("re-tag an already-linked bug and count issues")
