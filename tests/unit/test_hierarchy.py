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


@pytest.mark.parametrize(
    "restriction",
    [{"groups": ["core-security"]}, {"is_private": True}],
)
def test_restricted_metabug_is_never_mirrored(
    bug_factory, action_params_factory, mocked_bugzilla, restriction
):
    """A security metabug's summary would become a Jira Epic title (R-12)."""
    bug = bug_factory(blocks=[10, 20])
    service = BugzillaService(mocked_bugzilla)
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: {
        10: bug_factory(id=10, keywords=["meta"], **restriction),
        20: bug_factory(id=20, keywords=["meta"]),
    }[bug_id]

    metabugs = resolve_metabugs(bug, action_params_factory(), service)

    assert [m.id for m in metabugs] == [20]


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

    # Its outer metabugs get Epics, but an Epic is never given a parent.
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


# --- R-07 / R-08 end to end: a Jira re-parent never reaches BMO ------------


@pytest.mark.parametrize(
    "field,field_id,writes_expected",
    [
        ("Parent", "parent", False),
        ("Epic Link", "customfield_10014", False),
        ("IssueParentAssociation", "parent", False),
        # Control: the same harness does write for an execution field, so the
        # assertions above cannot pass vacuously.
        ("status", "status", True),
    ],
)
def test_reparent_in_jira_writes_nothing_to_bmo(
    jira_webhook_request_factory,
    jira_changelog_item_factory,
    action_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
    field,
    field_id,
    writes_expected,
):
    """Moving a task to another epic is a planning decision Jira owns (R-07).
    The whole inbound pipeline must leave the bug -- including its `see_also`
    link back to the issue -- exactly as it was (R-08)."""
    from jbi.errors import IgnoreInvalidRequestError
    from jbi.jira_inbound.handler import execute_jira_event
    from jbi.models import Actions

    actions = Actions(
        root=[
            action_factory(
                whiteboard_tag="devtest",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
                parameters__reverse_comment_sync_enabled=True,
            )
        ]
    )
    mocked_jira.get_issue_remote_links.return_value = [{"globalId": "654321"}]
    mocked_bugzilla.get_bug.return_value = bug_factory(
        id=654321,
        whiteboard="[devtest]",
        status="NEW",
        assigned_to="owner@mozilla.com",
        see_also=[f"{settings.jira_base_url}browse/JBI-234"],
    )
    event = jira_webhook_request_factory(
        changelog__items=[
            jira_changelog_item_factory(
                field=field, fieldId=field_id, fromString="EPIC-1", toString="EPIC-2"
            )
        ],
        issue__fields__labels=["bugzilla"],
    )

    try:
        execute_jira_event(event, actions)
    except IgnoreInvalidRequestError:
        pass  # ignoring the event outright is also correct

    assert mocked_bugzilla.update_bug.called is writes_expected
    if mocked_bugzilla.update_bug.called:
        assert "see_also" not in mocked_bugzilla.update_bug.call_args.kwargs


def test_every_metabug_gets_its_epic_on_the_first_child(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    """The parent takes the lowest id; the others still need an Epic so
    `sync_dependencies` has something to link to (plan 10.1)."""
    context = action_context_factory(
        operation=Operation.CREATE,
        jira__issue="JBI-1",
        jira__project="JBI",
        bug__blocks=[30, 20],
        bug__keywords=[],
        current_step="maybe_seed_epic_parent",
    )
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: bug_factory(
        id=bug_id, keywords=["meta"], see_also=[], summary=f"meta {bug_id}"
    )
    created = iter(["JBI-20", "JBI-30"])
    mocked_jira.create_issue.side_effect = lambda **kwargs: {"key": next(created)}

    result, _ = steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    assert result == steps.StepStatus.SUCCESS
    assert mocked_jira.create_issue.call_count == 2
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-1", fields={"parent": {"key": "JBI-20"}}
    )


# --- Every metabug is an Epic; nesting becomes links ------------------------


def _metabug_context(action_context_factory, operation, changed=(), **bug):
    from tests.fixtures.factories import WebhookEventChangeFactory

    return action_context_factory(
        operation=operation,
        jira__issue="JBI-1",
        jira__project="JBI",
        event__changes=[WebhookEventChangeFactory(field=f) for f in changed],
        current_step="maybe_seed_epic_parent",
        **{f"bug__{k}": v for k, v in bug.items()},
    )


def test_metabug_is_created_as_an_epic(
    action_context_factory, action_params_factory, mocked_jira, mocked_bugzilla
):
    context = action_context_factory(
        operation=Operation.CREATE,
        jira__issue=None,
        bug__keywords=["meta"],
        bug__type="defect",
        current_step="create_issue",
    )
    mocked_jira.create_issue.return_value = {"key": "JBI-9"}
    mocked_bugzilla.get_comments.return_value = []

    steps.create_issue(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    fields = mocked_jira.create_issue.call_args.kwargs["fields"]
    assert fields["issuetype"] == {"name": "Epic"}


def test_metabug_uses_its_bug_type_when_feature_off(
    action_context_factory, action_params_factory, mocked_jira, mocked_bugzilla
):
    context = action_context_factory(
        operation=Operation.CREATE,
        jira__issue=None,
        bug__keywords=["meta"],
        bug__type="defect",
        current_step="create_issue",
    )
    mocked_jira.create_issue.return_value = {"key": "JBI-9"}
    mocked_bugzilla.get_comments.return_value = []

    steps.create_issue(
        context,
        parameters=action_params_factory(metabug_epics_enabled=False),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    assert mocked_jira.create_issue.call_args.kwargs["fields"]["issuetype"] == {
        "name": "Bug"
    }


def test_metabug_epic_type_is_never_changed_back(
    action_context_factory, action_params_factory, mocked_jira
):
    from tests.fixtures.factories import WebhookEventChangeFactory

    context = action_context_factory(
        operation=Operation.UPDATE,
        jira__issue="JBI-9",
        bug__keywords=["meta"],
        bug__type="task",
        event__changes=[WebhookEventChangeFactory(field="type")],
        current_step="maybe_update_issue_type",
    )

    result, _ = steps.maybe_update_issue_type(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
    )

    assert result == steps.StepStatus.NOOP
    assert not mocked_jira.update_issue_field.called


def test_tagged_metabug_gets_outer_epics_but_no_parent(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
):
    context = _metabug_context(
        action_context_factory, Operation.CREATE, keywords=["meta"], blocks=[50]
    )
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: bug_factory(
        id=bug_id, keywords=["meta"], see_also=[], blocks=[]
    )
    mocked_jira.create_issue.return_value = {"key": "JBI-50"}

    result, _ = steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    assert result == steps.StepStatus.SUCCESS
    assert mocked_jira.create_issue.call_count == 1  # the outer metabug's Epic
    assert not mocked_jira.update_issue_field.called  # Epics are never parented


def _issue(parent=None, issuetype="Task"):
    fields = {"issuetype": {"name": issuetype}}
    if parent:
        fields["parent"] = {"key": parent}
    return {"fields": fields}


def test_update_adding_a_metabug_fills_an_empty_parent(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
):
    """One rule for every relationship: an issue with no parent becomes a
    child of its metabug's Epic, whenever the relationship is added."""
    context = _metabug_context(
        action_context_factory, Operation.UPDATE, changed=["blocks"], blocks=[60]
    )
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: bug_factory(
        id=bug_id, keywords=["meta"], see_also=[], blocks=[]
    )
    mocked_jira.create_issue.return_value = {"key": "JBI-60"}
    mocked_jira.get_issue.return_value = _issue()

    steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-1", fields={"parent": {"key": "JBI-60"}}
    )


def test_update_keeps_an_existing_parent(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
):
    """R-07: once placed -- by JBI or by a person -- the parent is Jira's.
    The new metabug gets a "blocks" link from sync_dependencies instead."""
    context = _metabug_context(
        action_context_factory, Operation.UPDATE, changed=["blocks"], blocks=[60]
    )
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: bug_factory(
        id=bug_id, keywords=["meta"], see_also=[], blocks=[]
    )
    mocked_jira.create_issue.return_value = {"key": "JBI-60"}
    mocked_jira.get_issue.return_value = _issue(parent="JBI-PLAN")

    steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    assert mocked_jira.create_issue.call_count == 1  # the Epic still exists
    assert not mocked_jira.update_issue_field.called


# --- A metabug's Depends On: the same relationship, from the other end -----


def _depends_context(action_context_factory, depends_on, operation=Operation.UPDATE):
    from tests.fixtures.factories import WebhookEventChangeFactory

    return action_context_factory(
        operation=operation,
        jira__issue="JBI-M",
        jira__project="JBI",
        bug__id=100,
        bug__keywords=["meta"],
        bug__blocks=[],
        bug__depends_on=depends_on,
        event__changes=[WebhookEventChangeFactory(field="depends_on")],
        current_step="maybe_seed_epic_parent",
    )


def test_depends_on_makes_unparented_issues_children_of_the_epic(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    link = lambda key: [f"{settings.jira_base_url}browse/{key}"]  # noqa: E731
    children = {
        1: bug_factory(id=1, keywords=[], see_also=link("JBI-1")),  # no parent: adopt
        2: bug_factory(id=2, keywords=[], see_also=link("JBI-2")),  # has a parent: keep
        3: bug_factory(id=3, keywords=[], see_also=[]),  # untagged, no issue: skip
        4: bug_factory(
            id=4, keywords=["meta"], see_also=link("JBI-4")
        ),  # metabug: skip
        5: bug_factory(
            id=5, keywords=[], groups=["core-security"], see_also=link("JBI-5")
        ),
    }
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: children[bug_id]
    parents = {
        "JBI-M": _issue(issuetype="Epic"),
        "JBI-1": _issue(),
        "JBI-2": _issue(parent="JBI-X"),
    }
    mocked_jira.get_issue.side_effect = lambda key, *a, **k: parents.get(key, _issue())

    result, _ = steps.maybe_seed_epic_parent(
        _depends_context(action_context_factory, [1, 2, 3, 4, 5]),
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    assert result == steps.StepStatus.SUCCESS
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-1", fields={"parent": {"key": "JBI-M"}}
    )


def test_depends_on_needs_the_metabug_to_be_an_epic(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    """A metabug mirrored as a Task before Phase 2 cannot hold children."""
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: bug_factory(
        id=bug_id, keywords=[], see_also=[f"{settings.jira_base_url}browse/JBI-1"]
    )
    mocked_jira.get_issue.side_effect = lambda key, *a, **k: (
        _issue(issuetype="Task") if key == "JBI-M" else _issue()
    )

    steps.maybe_seed_epic_parent(
        _depends_context(action_context_factory, [1]),
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    assert not mocked_jira.update_issue_field.called


# --- No link between an issue and its own parent ----------------------------


@pytest.mark.parametrize("field", ["blocks", "depends_on"])
def test_no_blocks_link_to_ones_own_parent(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
    field,
):
    """The parent already says it; a link would duplicate it. A link an earlier
    run made is removed."""
    from tests.fixtures.factories import WebhookEventChangeFactory

    if field == "blocks":
        # Child JBI-1 blocks metabug 10 (its parent JBI-10) and metabug 20.
        bug = dict(
            bug__id=1, bug__blocks=[10, 20], bug__depends_on=[], bug__keywords=[]
        )
        issue, parents = "JBI-1", {"JBI-1": "JBI-10"}
        others = {10: "JBI-10", 20: "JBI-20"}
    else:
        # Metabug JBI-10 depends on bug 1 (its child) and bug 2 (parented elsewhere).
        bug = dict(
            bug__id=10, bug__blocks=[], bug__depends_on=[1, 2], bug__keywords=["meta"]
        )
        issue, parents = "JBI-10", {"JBI-1": "JBI-10", "JBI-2": "JBI-99"}
        others = {1: "JBI-1", 2: "JBI-2"}
    context = action_context_factory(
        operation=Operation.UPDATE,
        jira__issue=issue,
        jira__project="JBI",
        event__changes=[WebhookEventChangeFactory(field=field, removed="", added="")],
        current_step="sync_dependencies",
        **bug,
    )
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: bug_factory(
        id=bug_id, see_also=[f"{settings.jira_base_url}browse/{others[bug_id]}"]
    )
    mocked_jira.get_issue.side_effect = lambda key, *a, **k: _issue(
        parent=parents.get(key)
    )

    steps.sync_dependencies(
        context,
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
        parameters=action_params_factory(metabug_epics_enabled=True),
    )

    created = [
        (
            c.kwargs["data"]["inwardIssue"]["key"],
            c.kwargs["data"]["outwardIssue"]["key"],
        )
        for c in mocked_jira.create_issue_link.call_args_list
    ]
    if field == "blocks":
        assert created == [("JBI-1", "JBI-20")]
    else:
        assert created == [("JBI-2", "JBI-10")]


def test_unrelated_update_does_no_metabug_work(
    action_context_factory, action_params_factory, mocked_jira, mocked_bugzilla
):
    context = _metabug_context(
        action_context_factory, Operation.UPDATE, changed=["summary"], blocks=[60]
    )

    result, _ = steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    assert result == steps.StepStatus.NOOP
    assert not mocked_bugzilla.get_bug.called


def test_nested_chain_is_linked_inner_to_outer(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
):
    """C blocks I, I blocks O: I's Epic blocks O's Epic."""
    context = _metabug_context(action_context_factory, Operation.CREATE, blocks=[20])
    metabugs = {
        20: bug_factory(id=20, keywords=["meta"], see_also=[], blocks=[10]),
        10: bug_factory(id=10, keywords=["meta"], see_also=[], blocks=[]),
    }
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: metabugs[bug_id]
    created = iter(["JBI-20", "JBI-10"])
    mocked_jira.create_issue.side_effect = lambda **kwargs: {"key": next(created)}

    steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    link = mocked_jira.create_issue_link.call_args.kwargs["data"]
    # Same direction `create_issue_link_blocks` uses for a child: the inner
    # metabug's Epic blocks the outer one's.
    assert link["type"] == {"name": "Blocks"}
    assert link["inwardIssue"] == {"key": "JBI-20"}
    assert link["outwardIssue"] == {"key": "JBI-10"}
    mocked_jira.update_issue_field.assert_called_once_with(
        key="JBI-1", fields={"parent": {"key": "JBI-20"}}
    )


def test_a_nesting_cycle_terminates(
    action_context_factory,
    action_params_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
):
    context = _metabug_context(action_context_factory, Operation.CREATE, blocks=[20])
    metabugs = {
        20: bug_factory(id=20, keywords=["meta"], see_also=[], blocks=[10]),
        10: bug_factory(id=10, keywords=["meta"], see_also=[], blocks=[20]),
    }
    mocked_bugzilla.get_bug.side_effect = lambda bug_id: metabugs[bug_id]
    created = iter(["JBI-20", "JBI-10", "JBI-X"])
    mocked_jira.create_issue.side_effect = lambda **kwargs: {"key": next(created)}

    steps.maybe_seed_epic_parent(
        context,
        parameters=action_params_factory(metabug_epics_enabled=True),
        jira_service=JiraService(mocked_jira),
        bugzilla_service=BugzillaService(mocked_bugzilla),
    )

    assert mocked_jira.create_issue.call_count == 2
