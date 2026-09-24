"""Tests for metabug -> Epic mapping (R-05/R-06, plan section 10.1)."""

import pytest

from jbi import Operation, steps
from jbi.bugzilla.service import BugzillaService
from jbi.hierarchy import (
    candidate_metabug_ids,
    choose_parent_metabug,
    ensure_mirror_epic,
    is_metabug,
    is_open,
    mirror_issue_key,
    resolve_metabugs,
)
from jbi.jira import JiraService

# --- Identification ---------------------------------------------------------


def test_metabug_is_identified_by_keyword(bug_factory):
    """Not by a `[meta]` title prefix: real metabugs exist without it."""
    assert is_metabug(bug_factory(keywords=["meta"])) is True
    assert is_metabug(bug_factory(keywords=["META", "regression"])) is True
    assert (
        is_metabug(bug_factory(summary="[meta] looks like one", keywords=[])) is False
    )
    assert is_metabug(bug_factory(keywords=None)) is False


@pytest.mark.parametrize(
    "status,resolution,expected",
    [
        ("NEW", "", True),
        ("ASSIGNED", "", True),
        ("REOPENED", "", True),
        ("RESOLVED", "FIXED", False),
        ("VERIFIED", "FIXED", False),
    ],
)
def test_is_open(bug_factory, status, resolution, expected):
    """The eager scan uses this to skip metabugs closed years ago."""
    assert is_open(bug_factory(status=status, resolution=resolution)) is expected


def test_candidate_metabugs_come_from_blocks_sorted(bug_factory):
    """A child bug lists its metabugs in `blocks` (the metabug lists children
    in `depends_on`). Sorted so the tie-break cannot flap."""
    bug = bug_factory(blocks=[300, 100, 200], depends_on=[999])

    assert candidate_metabug_ids(bug) == [100, 200, 300]


def test_no_blocks_means_no_candidates(bug_factory):
    assert candidate_metabug_ids(bug_factory(blocks=[])) == []


# --- Resolution & scope -----------------------------------------------------


def test_resolve_metabugs_filters_non_meta_and_out_of_scope(
    bug_factory, action_params_factory, mocked_bugzilla
):
    bug = bug_factory(blocks=[10, 20, 30])
    service = BugzillaService(mocked_bugzilla)
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: {
        10: bug_factory(id=10, keywords=["meta"], product="Core", component="General"),
        # not a metabug: just another bug this one blocks
        20: bug_factory(id=20, keywords=[], product="Core", component="General"),
        # a metabug, but in a component this action does not sync
        30: bug_factory(id=30, keywords=["meta"], product="Firefox", component="Sync"),
    }[bug_id]

    metabugs = resolve_metabugs(
        bug,
        action_params_factory(sync_products_components=["Core::General"]),
        service,
    )

    assert [m.id for m in metabugs] == [10]


def test_resolve_metabugs_is_ordered_by_id(
    bug_factory, action_params_factory, mocked_bugzilla
):
    bug = bug_factory(blocks=[50, 20])
    service = BugzillaService(mocked_bugzilla)
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: bug_factory(
        id=bug_id, keywords=["meta"]
    )

    metabugs = resolve_metabugs(bug, action_params_factory(), service)

    assert [m.id for m in metabugs] == [20, 50]
    assert choose_parent_metabug(metabugs).id == 20


def test_choose_parent_of_nothing_is_none():
    assert choose_parent_metabug([]) is None


def test_mirror_issue_key_reads_see_also(bug_factory, settings):
    metabug = bug_factory(
        see_also=[f"{settings.jira_base_url}browse/JBI-99", "https://example.com/x"]
    )

    assert mirror_issue_key(metabug, "JBI") == "JBI-99"
    assert mirror_issue_key(metabug, "OTHER") is None


# --- Epic creation ----------------------------------------------------------


def test_existing_epic_mirror_is_reused(
    action_context_factory, bug_factory, mocked_jira, mocked_bugzilla, settings
):
    """Invariant A: one Jira issue per bug. A second Epic must never appear."""
    context = action_context_factory(jira__project="JBI")
    metabug = bug_factory(
        id=10, keywords=["meta"], see_also=[f"{settings.jira_base_url}browse/JBI-50"]
    )
    mocked_jira.get_issue.return_value = {"fields": {"issuetype": {"name": "Epic"}}}

    key = ensure_mirror_epic(
        context, metabug, JiraService(mocked_jira), BugzillaService(mocked_bugzilla)
    )

    assert key == "JBI-50"
    assert not mocked_jira.create_issue.called


def test_existing_non_epic_mirror_is_left_alone(
    action_context_factory,
    bug_factory,
    mocked_jira,
    mocked_bugzilla,
    settings,
    capturelogs,
):
    """The rollout case: metabugs synced under today's behavior became Tasks
    (issue type comes from the bug's `type`), and Jira generally refuses a
    hierarchy-level change. Leave them; children link instead."""
    import logging

    context = action_context_factory(jira__project="JBI")
    metabug = bug_factory(
        id=10, keywords=["meta"], see_also=[f"{settings.jira_base_url}browse/JBI-50"]
    )
    mocked_jira.get_issue.return_value = {"fields": {"issuetype": {"name": "Task"}}}

    with capturelogs.for_logger("jbi.hierarchy").at_level(logging.INFO):
        key = ensure_mirror_epic(
            context, metabug, JiraService(mocked_jira), BugzillaService(mocked_bugzilla)
        )

    assert key is None
    assert not mocked_jira.create_issue.called
    assert not mocked_bugzilla.update_bug.called
    assert any("not an Epic" in r.message for r in capturelogs.records)


def test_epic_is_created_and_linked_back(
    action_context_factory, bug_factory, mocked_jira, mocked_bugzilla
):
    """The see_also link is what stops the next run creating a second Epic."""
    context = action_context_factory(jira__project="JBI")
    metabug = bug_factory(id=10, keywords=["meta"], see_also=[], summary="[meta] thing")
    mocked_jira.create_issue.return_value = {"key": "JBI-77"}

    key = ensure_mirror_epic(
        context, metabug, JiraService(mocked_jira), BugzillaService(mocked_bugzilla)
    )

    assert key == "JBI-77"
    created_fields = mocked_jira.create_issue.call_args.kwargs["fields"]
    assert created_fields["issuetype"] == {"name": "Epic"}
    assert created_fields["summary"] == "[meta] thing"
    assert created_fields["project"] == {"key": "JBI"}
    assert mocked_bugzilla.update_bug.called


# --- The parent-seeding step ------------------------------------------------


@pytest.fixture
def child_context(action_context_factory):
    return action_context_factory(
        operation=Operation.CREATE,
        bug__blocks=[10],
        bug__keywords=[],
        jira__issue="JBI-1",
        jira__project="JBI",
        current_step="maybe_seed_epic_parent",
    )


def _services(mocked_jira, mocked_bugzilla):
    return {
        "jira_service": JiraService(mocked_jira),
        "bugzilla_service": BugzillaService(mocked_bugzilla),
    }


def test_parent_is_seeded_on_create(
    child_context, action_params_factory, mocked_jira, mocked_bugzilla, bug_factory
):
    mocked_bugzilla.get_bug.return_value = bug_factory(id=10, keywords=["meta"])
    mocked_jira.create_issue.return_value = {"key": "JBI-77"}

    result, _ = steps.maybe_seed_epic_parent(
        child_context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        **_services(mocked_jira, mocked_bugzilla),
    )

    assert result == steps.StepStatus.SUCCESS
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-1", fields={"parent": {"key": "JBI-77"}}
    )


def test_step_is_inert_when_disabled(
    child_context, action_params_factory, mocked_jira, mocked_bugzilla
):
    result, _ = steps.maybe_seed_epic_parent(
        child_context,
        parameters=action_params_factory(),
        **_services(mocked_jira, mocked_bugzilla),
    )

    assert result == steps.StepStatus.NOOP
    assert not mocked_jira.update_issue_field.called


def test_update_never_reparents(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
):
    """R-07: after creation the parent is Jira's to own. A BMO-side change
    must never move a task out of a deliberately-chosen delivery epic."""
    context = action_context_factory(
        operation=Operation.UPDATE,
        bug__blocks=[10],
        jira__issue="JBI-1",
        current_step="maybe_seed_epic_parent",
    )
    mocked_bugzilla.get_bug.return_value = bug_factory(id=10, keywords=["meta"])

    result, _ = steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        **_services(mocked_jira, mocked_bugzilla),
    )

    assert result == steps.StepStatus.NOOP
    assert not mocked_jira.update_issue_field.called
    assert not mocked_jira.create_issue.called


def test_metabug_itself_is_not_parented(
    action_context_factory, action_params_factory, mocked_jira, mocked_bugzilla
):
    context = action_context_factory(
        operation=Operation.CREATE,
        bug__keywords=["meta"],
        bug__blocks=[10],
        jira__issue="JBI-1",
        current_step="maybe_seed_epic_parent",
    )

    result, _ = steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        **_services(mocked_jira, mocked_bugzilla),
    )

    assert result == steps.StepStatus.NOOP
    assert not mocked_jira.update_issue_field.called


def test_lowest_metabug_id_wins_the_parent(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    """Two metabugs, one parent. Lowest id is deterministic, and the other is
    still represented as an issue link by `sync_dependencies`."""
    context = action_context_factory(
        operation=Operation.CREATE,
        bug__blocks=[80, 20],
        bug__keywords=[],
        jira__issue="JBI-1",
        jira__project="JBI",
        current_step="maybe_seed_epic_parent",
    )
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: bug_factory(
        id=bug_id,
        keywords=["meta"],
        see_also=[f"{settings.jira_base_url}browse/JBI-{bug_id}"],
    )
    mocked_jira.get_issue.return_value = {"fields": {"issuetype": {"name": "Epic"}}}

    steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        **_services(mocked_jira, mocked_bugzilla),
    )

    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-1", fields={"parent": {"key": "JBI-20"}}
    )


def test_falls_through_to_the_next_metabug_when_the_first_is_not_an_epic(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    """A metabug already mirrored as a Task cannot parent anything, so the
    next candidate gets the chance rather than the issue going unparented."""
    context = action_context_factory(
        operation=Operation.CREATE,
        bug__blocks=[20, 80],
        bug__keywords=[],
        jira__issue="JBI-1",
        jira__project="JBI",
        current_step="maybe_seed_epic_parent",
    )
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: bug_factory(
        id=bug_id,
        keywords=["meta"],
        see_also=[f"{settings.jira_base_url}browse/JBI-{bug_id}"],
    )
    mocked_jira.get_issue.side_effect = lambda key, **kw: {
        "JBI-20": {"fields": {"issuetype": {"name": "Task"}}},
        "JBI-80": {"fields": {"issuetype": {"name": "Epic"}}},
    }[key]

    result, _ = steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        **_services(mocked_jira, mocked_bugzilla),
    )

    assert result == steps.StepStatus.SUCCESS
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-1", fields={"parent": {"key": "JBI-80"}}
    )


def test_no_epic_anywhere_leaves_the_issue_unparented(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    context = action_context_factory(
        operation=Operation.CREATE,
        bug__blocks=[20],
        bug__keywords=[],
        jira__issue="JBI-1",
        jira__project="JBI",
        current_step="maybe_seed_epic_parent",
    )
    mocked_bugzilla.get_bug.return_value = bug_factory(
        id=20, keywords=["meta"], see_also=[f"{settings.jira_base_url}browse/JBI-20"]
    )
    mocked_jira.get_issue.return_value = {"fields": {"issuetype": {"name": "Task"}}}

    result, _ = steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        **_services(mocked_jira, mocked_bugzilla),
    )

    assert result == steps.StepStatus.INCOMPLETE
    assert not mocked_jira.update_issue_field.called


def test_parent_is_not_written_back_to_bmo(action_factory):
    """R-07 from the other direction: an inbound Jira parent change is a
    planning decision and must never reach BMO."""
    from jbi.writeback import is_writeback_allowed

    assert is_writeback_allowed("parent") is False
    assert is_writeback_allowed("Epic Link") is False
