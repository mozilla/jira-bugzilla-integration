"""End-to-end smoke tests against a live Jira sandbox and Bugzilla dev (plan D12).

The four PRD acceptance scenarios, the invariants, and the Phase 2 behavior.
JBI is the real thing, running with a config that onboards
JBI_E2E_WHITEBOARD_TAG; the two producers (BMO's webhook and the central
Automation rule) are stood in for by `Sandbox.post_*_event`. See README.md.

Run with `make smoke` (or `uv run pytest tests/e2e -m e2e`).
"""

import pytest

from .conftest import SLA_SECONDS

pytestmark = pytest.mark.e2e


def _patch(review=None, action="create"):
    """A Phabricator request attachment, as BMO's webhook carries it."""
    from .sandbox import now_iso

    return {
        "id": 900001,
        "content_type": "text/x-phabricator-request",
        "creation_time": now_iso(),
        "description": "e2e patch",
        "file_name": "phabricator-D99999-url.txt",
        "flags": [{"id": None, "name": "review", "value": review}] if review else [],
        "is_obsolete": False,
        "is_patch": False,
        "is_private": False,
    }


# --- PRD acceptance scenarios ------------------------------------------------


def test_scenario_1_tagged_bug_creates_a_mapped_issue(sandbox, synced_bug):
    """PRD 1: one issue, fields mapped, linked both ways."""
    bug_id, key = synced_bug("scenario 1")

    issue = sandbox.issue(key)
    fields = issue["fields"]
    assert sandbox.bug(bug_id)["summary"] == fields["summary"]
    assert "bugzilla" in fields["labels"]
    remote = sandbox._jira.get(
        f"{sandbox.jira}/rest/api/2/issue/{key}/remotelink", timeout=30
    ).json()
    assert any(str(link.get("globalId")) == str(bug_id) for link in remote)
    assert sandbox.eventually(lambda: sandbox.issues_for_bug(bug_id) == [key])


def test_scenario_2_untagged_bug_creates_nothing(sandbox):
    """PRD 2: a bug no action claims never reaches Jira."""
    bug_id = sandbox.file_bug("scenario 2, no whiteboard tag", tagged=False)

    sandbox.post_bugzilla_event(bug_id, "create")

    assert sandbox.linked_issue(bug_id) is None
    assert sandbox.issues_for_bug(bug_id) == []


def test_scenario_3_status_flows_both_ways_and_the_loop_stops(sandbox, synced_bug):
    """PRD 3 + Invariant C: BMO -> Jira, Jira -> BMO, and no echo."""
    bug_id, key = synced_bug("scenario 3")

    # Jira -> BMO: close the issue, deliver the Automation event.
    before = sandbox.issue(key, fields="status")["fields"]["status"]["name"]
    sandbox.transition(key, sandbox.env.get("JBI_E2E_DONE_STATUS", "Closed"), "Fixed")
    after = sandbox.issue(key, fields="status")["fields"]["status"]["name"]
    response = sandbox.post_jira_event(key, [("status", before, after)])
    assert response.status_code == 200, response.text

    bug = sandbox.eventually(
        lambda: (b := sandbox.bug(bug_id))["status"] == "RESOLVED" and b, SLA_SECONDS
    )
    assert bug, "the Jira close did not reach the bug"
    assert bug["resolution"] == "FIXED"

    # The BMO write fires BMO's webhook; delivering it must not bounce back.
    transitions = sandbox.status_transitions(key)
    sandbox.post_bugzilla_event(bug_id, "modify", changed=("status", "resolution"))
    assert sandbox.status_transitions(key) == transitions


def test_scenario_4_comment_reaches_the_bug_once_with_attribution(sandbox, synced_bug):
    """PRD 4: attributed copy, posted by JBI, never twice."""
    bug_id, key = synced_bug("scenario 4")
    comment = sandbox.add_jira_comment(key, "e2e: looks good from Jira")

    for _ in range(2):  # the second delivery is a redelivery
        response = sandbox.post_jira_event(key, comment=comment)
        assert response.status_code == 200, response.text

    copies = [
        text for text in sandbox.comments(bug_id) if "e2e: looks good from Jira" in text
    ]
    assert len(copies) == 1
    assert copies[0].startswith("from Jira, by e2e human")


@pytest.mark.skipif(
    "not __import__('os').environ.get('JBI_E2E_RESTRICTED_GROUP')",
    reason="set JBI_E2E_RESTRICTED_GROUP to a group your account can add",
)
def test_scenario_4b_nothing_reaches_a_restricted_bug(sandbox):
    """PRD 4 / R-12: a restricted bug is never synced in either direction."""
    import os

    bug_id = sandbox.file_bug(
        "scenario 4b, restricted", groups=[os.environ["JBI_E2E_RESTRICTED_GROUP"]]
    )
    sandbox.post_bugzilla_event(bug_id, "create")

    assert sandbox.linked_issue(bug_id) is None


def test_invariant_a_resyncing_a_linked_bug_creates_no_duplicate(sandbox, synced_bug):
    bug_id, key = synced_bug("invariant A")

    sandbox.post_bugzilla_event(bug_id, "create")
    sandbox.post_bugzilla_event(bug_id, "modify", changed=("whiteboard",))

    assert sandbox.eventually(lambda: sandbox.issues_for_bug(bug_id) == [key])


# --- Phase 1 defaults: review moves and the stop label ----------------------


def test_review_request_and_verdicts(sandbox, synced_bug):
    """D4/D5: a request moves the issue into review; review- moves it out;
    review+ moves nothing."""
    import os

    review_status = os.environ.get("JBI_E2E_REVIEW_STATUS", "In Review")
    work_status = os.environ.get("JBI_E2E_CHANGES_STATUS", "In Progress")
    bug_id, key = synced_bug("review moves")

    def status():
        return sandbox.issue(key, fields="status")["fields"]["status"]["name"]

    sandbox.post_bugzilla_event(bug_id, "create", attachment=_patch())
    assert status().lower() == review_status.lower()

    sandbox.post_bugzilla_event(bug_id, "modify", attachment=_patch("-"))
    assert status().lower() == work_status.lower()

    moves = sandbox.status_transitions(key)
    sandbox.post_bugzilla_event(bug_id, "modify", attachment=_patch("+"))
    assert sandbox.status_transitions(key) == moves


def test_stop_label_pauses_and_resumes(sandbox, synced_bug):
    """D15: the label halts sync; removing it resumes from the current state."""
    bug_id, key = synced_bug("stop label")
    sandbox.label(key, add="jbi-sync-stop")

    sandbox.update_bug(bug_id, summary=sandbox.bug(bug_id)["summary"] + " (paused)")
    sandbox.post_bugzilla_event(bug_id, "modify", changed=("summary",))
    assert "(paused)" not in sandbox.issue(key, fields="summary")["fields"]["summary"]

    sandbox.label(key, remove="jbi-sync-stop")
    sandbox.post_bugzilla_event(bug_id, "modify", changed=("summary",))
    assert "(paused)" in sandbox.issue(key, fields="summary")["fields"]["summary"]


# --- Phase 2: metabugs, nesting, release data --------------------------------


def test_metabug_epic_parent_and_nesting(sandbox):
    """R-05/R-06 + nesting: tagged metabug -> Epic; child parented; inner
    metabug's Epic blocks the outer's."""
    outer = sandbox.file_bug("outer metabug", keywords=["meta"])
    inner = sandbox.file_bug("inner metabug", keywords=["meta"], blocks=[outer])
    sandbox.post_bugzilla_event(inner, "create")
    inner_key = sandbox.track_issue(sandbox.linked_issue(inner))
    outer_key = sandbox.track_issue(sandbox.linked_issue(outer))
    assert inner_key and outer_key

    inner_issue = sandbox.issue(inner_key)["fields"]
    assert inner_issue["issuetype"]["name"] == "Epic"
    assert inner_issue.get("parent") is None
    links = {
        (link.get("outwardIssue") or {}).get("key")
        for link in inner_issue.get("issuelinks", [])
    }
    assert outer_key in links

    child = sandbox.file_bug("child of inner", blocks=[inner])
    sandbox.post_bugzilla_event(child, "create")
    child_key = sandbox.track_issue(sandbox.linked_issue(child))
    assert sandbox.issue(child_key)["fields"]["parent"]["key"] == inner_key


def test_release_data_labels(sandbox, synced_bug):
    """R-09/R-10: flags and milestone become bz- labels, and swap cleanly."""
    import os

    milestone = os.environ.get("JBI_E2E_MILESTONE", "Future")
    flag = os.environ.get("JBI_E2E_STATUS_FLAG", "cf_status_firefox126")
    bug_id, key = synced_bug(
        "release data", target_milestone=milestone, **{flag: "affected"}
    )

    labels = sandbox.issue(key, fields="labels")["fields"]["labels"]
    release = flag.replace("cf_status_firefox", "").lstrip("_")
    assert f"bz-status-{release}-affected" in labels
    assert f"bz-milestone-{milestone.lower().replace(' ', '-')}" in labels

    sandbox.update_bug(bug_id, **{flag: "fixed"})
    sandbox.post_bugzilla_event(bug_id, "modify", changed=(flag,))
    labels = sandbox.issue(key, fields="labels")["fields"]["labels"]
    assert f"bz-status-{release}-fixed" in labels
    assert f"bz-status-{release}-affected" not in labels


def test_depends_on_makes_an_unparented_issue_a_child(sandbox, synced_bug):
    """A metabug's Depends On is the same relationship as its child's Blocks:
    an issue with no parent becomes a child of the metabug's Epic, even after
    creation, with no extra link to its own parent."""
    metabug = sandbox.file_bug("metabug for depends-on", keywords=["meta"])
    sandbox.post_bugzilla_event(metabug, "create")
    epic = sandbox.track_issue(sandbox.linked_issue(metabug))
    bug_id, key = synced_bug("added to a metabug's Depends On")
    # Jira omits `fields` entirely when none of the requested ones is set.
    assert sandbox.issue(key, fields="parent").get("fields", {}).get("parent") is None

    sandbox.update_bug(metabug, depends_on={"add": [bug_id]})
    sandbox.post_bugzilla_event(metabug, "modify", changed=("depends_on",))

    fields = sandbox.issue(key, fields="parent,issuelinks").get("fields", {})
    assert (fields.get("parent") or {}).get("key") == epic
    linked = {
        (link.get("outwardIssue") or link.get("inwardIssue") or {}).get("key")
        for link in fields.get("issuelinks", [])
    }
    assert epic not in linked
