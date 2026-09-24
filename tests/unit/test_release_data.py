"""Tests for release flags and Target Milestone mirroring (R-09/R-10, P2-4).

Both land in labels: the pilot project has no release-flag or milestone custom
field, and `fixVersions` -- the only release-shaped field there -- already has
another writer (a separate release automation), and two writers on one field
fight.
"""

import pytest

from jbi import Operation, steps
from jbi.bugzilla.models import Bug
from jbi.jira import JiraService
from jbi.steps import _milestone_label, _release_flag_labels

# --- Harvesting the dynamic BMO fields --------------------------------------


def test_release_flags_are_harvested_from_dynamic_fields():
    """BMO names these per release (`cf_status_firefox157`), so they cannot be
    declared as model fields."""
    bug = Bug.model_validate(
        {
            "id": 1,
            "cf_status_firefox157": "fixed",
            "cf_status_firefox_esr140": "affected",
            "cf_fx_points": "3",
        }
    )

    assert bug.release_flags == {"firefox157": "fixed", "firefox_esr140": "affected"}


def test_unset_flags_are_not_harvested():
    """`---` is BMO's "not set": an absence of information, not a state."""
    bug = Bug.model_validate(
        {"id": 1, "cf_status_firefox157": "---", "cf_status_firefox156": ""}
    )

    assert bug.release_flags == {}


def test_target_milestone_is_parsed():
    bug = Bug.model_validate({"id": 1, "target_milestone": "157 Branch"})

    assert bug.target_milestone == "157 Branch"


def test_harvesting_does_not_disturb_other_custom_fields():
    bug = Bug.model_validate({"id": 1, "cf_fx_points": "5"})

    assert bug.cf_fx_points == "5"
    assert bug.release_flags == {}


# --- Label rendering --------------------------------------------------------


@pytest.mark.parametrize(
    "flags,expected",
    [
        ({"firefox157": "fixed"}, ["fx157-fixed"]),
        ({"firefox_esr140": "affected"}, ["fxesr140-affected"]),
        # Sorted, so the label set is stable between runs.
        (
            {"firefox158": "affected", "firefox157": "fixed"},
            ["fx157-fixed", "fx158-affected"],
        ),
        ({}, []),
    ],
)
def test_release_flag_label_rendering(flags, expected):
    assert _release_flag_labels(flags) == expected


@pytest.mark.parametrize(
    "milestone,expected",
    [
        ("157 Branch", "milestone-157-branch"),
        ("mozilla160", "milestone-mozilla160"),
        # Jira labels cannot contain spaces.
        ("Future Work", "milestone-future-work"),
        ("---", None),
        ("", None),
        (None, None),
    ],
)
def test_milestone_label_rendering(milestone, expected):
    assert _milestone_label(milestone) == expected


# --- The steps --------------------------------------------------------------


@pytest.fixture
def flagged_context(action_context_factory):
    return action_context_factory(
        operation=Operation.CREATE,
        jira__issue="JBI-234",
        current_step="mirror_release_flags",
    )


def test_release_flags_are_added_as_labels(action_context_factory, mocked_jira):
    context = action_context_factory(
        operation=Operation.CREATE,
        jira__issue="JBI-234",
        current_step="mirror_release_flags",
        bug__release_flags={"firefox157": "fixed"},
    )
    mocked_jira.get_issue.return_value = {"fields": {"labels": ["bugzilla"]}}

    result, _ = steps.mirror_release_flags(
        context, jira_service=JiraService(mocked_jira)
    )

    assert result == steps.StepStatus.SUCCESS
    mocked_jira.update_issue.assert_called()


def test_stale_flag_label_is_retired(action_context_factory, mocked_jira):
    """A flag flipping from `affected` to `fixed` must not leave both labels
    behind -- and the old value is not derivable from the bug's new state,
    which is why the current labels are read first."""
    context = action_context_factory(
        operation=Operation.UPDATE,
        jira__issue="JBI-234",
        current_step="mirror_release_flags",
        bug__release_flags={"firefox157": "fixed"},
    )
    mocked_jira.get_issue.return_value = {
        "fields": {"labels": ["bugzilla", "fx157-affected"]}
    }

    steps.mirror_release_flags(context, jira_service=JiraService(mocked_jira))

    call = mocked_jira.update_issue.call_args
    update = call.kwargs.get("update") or call.args[1]
    ops = update["update"]["labels"] if "update" in update else update["labels"]
    added = [o["add"] for o in ops if "add" in o]
    removed = [o["remove"] for o in ops if "remove" in o]
    assert "fx157-fixed" in added
    assert "fx157-affected" in removed


def test_unrelated_labels_are_never_touched(action_context_factory, mocked_jira):
    """JBI owns the `fx`/`milestone-` namespaces only. A whiteboard-derived
    label must survive untouched."""
    context = action_context_factory(
        operation=Operation.UPDATE,
        jira__issue="JBI-234",
        current_step="mirror_release_flags",
        bug__release_flags={},
    )
    mocked_jira.get_issue.return_value = {
        "fields": {"labels": ["bugzilla", "devtest", "important"]}
    }

    result, _ = steps.mirror_release_flags(
        context, jira_service=JiraService(mocked_jira)
    )

    assert result == steps.StepStatus.NOOP
    assert not mocked_jira.update_issue.called


def test_milestone_label_is_added(action_context_factory, mocked_jira):
    context = action_context_factory(
        operation=Operation.CREATE,
        jira__issue="JBI-234",
        current_step="mirror_target_milestone",
        bug__target_milestone="157 Branch",
    )
    mocked_jira.get_issue.return_value = {"fields": {"labels": []}}

    result, _ = steps.mirror_target_milestone(
        context, jira_service=JiraService(mocked_jira)
    )

    assert result == steps.StepStatus.SUCCESS


def test_milestone_change_replaces_the_old_label(action_context_factory, mocked_jira):
    context = action_context_factory(
        operation=Operation.UPDATE,
        jira__issue="JBI-234",
        current_step="mirror_target_milestone",
        bug__target_milestone="158 Branch",
    )
    mocked_jira.get_issue.return_value = {
        "fields": {"labels": ["milestone-157-branch", "bugzilla"]}
    }

    steps.mirror_target_milestone(context, jira_service=JiraService(mocked_jira))

    call = mocked_jira.update_issue.call_args
    update = call.kwargs.get("update") or call.args[1]
    ops = update["update"]["labels"] if "update" in update else update["labels"]
    assert {"add": "milestone-158-branch"} in ops
    assert {"remove": "milestone-157-branch"} in ops


def test_milestone_cleared_in_bmo_removes_the_label(
    action_context_factory, mocked_jira
):
    context = action_context_factory(
        operation=Operation.UPDATE,
        jira__issue="JBI-234",
        current_step="mirror_target_milestone",
        bug__target_milestone="---",
    )
    mocked_jira.get_issue.return_value = {
        "fields": {"labels": ["milestone-157-branch"]}
    }

    result, _ = steps.mirror_target_milestone(
        context, jira_service=JiraService(mocked_jira)
    )

    assert result == steps.StepStatus.SUCCESS
    call = mocked_jira.update_issue.call_args
    update = call.kwargs.get("update") or call.args[1]
    ops = update["update"]["labels"] if "update" in update else update["labels"]
    assert {"remove": "milestone-157-branch"} in ops


def test_no_release_data_is_a_noop(action_context_factory, mocked_jira):
    context = action_context_factory(
        operation=Operation.CREATE,
        jira__issue="JBI-234",
        current_step="mirror_target_milestone",
        bug__target_milestone=None,
    )
    mocked_jira.get_issue.return_value = {"fields": {"labels": []}}

    result, _ = steps.mirror_target_milestone(
        context, jira_service=JiraService(mocked_jira)
    )

    assert result == steps.StepStatus.NOOP
    assert not mocked_jira.update_issue.called
