import pydantic
import pytest

from jbi.models import ActionParams, Actions, ActionSteps


def test_bidirectional_sync_scaffolding_fields_default_to_noop():
    """The Phase 1 scaffolding fields added for the BMO<->Jira bidirectional
    sync plan (docs/bmo-jira-bidirectional-integration-plan.md, D1) must all
    default to values that reproduce today's (one-directional) behavior,
    since no existing action configures them."""
    params = ActionParams(jira_project_key="JBI")
    assert params.sync_products_components is None
    assert params.min_priority is None
    assert params.min_severity is None
    assert params.identity_map_enabled is False
    assert params.jira_inbound_enabled is False


def test_bidirectional_sync_scaffolding_fields_are_configurable():
    """Once consumed (D2/D3/D6/D8), these fields should accept the values
    an action's configuration would set them to."""
    params = ActionParams(
        jira_project_key="JBI",
        sync_products_components=["Core::Machine Learning: On-Device"],
        min_priority="P2",
        min_severity="S2",
        identity_map_enabled=True,
        jira_inbound_enabled=True,
    )
    assert params.sync_products_components == ["Core::Machine Learning: On-Device"]
    assert params.min_priority == "P2"
    assert params.min_severity == "S2"
    assert params.identity_map_enabled is True
    assert params.jira_inbound_enabled is True


@pytest.mark.parametrize("value", [123456, [123456], [12345, 67890], "tbd"])
def test_valid_bugzilla_user_ids(action_factory, value):
    action = action_factory(bugzilla_user_id=value)
    assert action.bugzilla_user_id == value


@pytest.mark.parametrize("value", [None, "foobar@example.com"])
def test_invalid_bugzilla_user_ids(action_factory, value):
    with pytest.raises(pydantic.ValidationError):
        action_factory(bugzilla_user_id=value)


def test_no_actions_fails():
    with pytest.raises(ValueError) as exc_info:
        Actions(root=[])
    assert "List should have at least 1 item after validation, not 0" in str(
        exc_info.value
    )


def test_default_invalid_step():
    with pytest.raises(pydantic.ValidationError) as exc:
        ActionSteps(new=["BOOM", "POW"], comment=["BAM"])
    error_message = str(exc.value)

    assert "BOOM" in error_message
    assert "POW" in error_message
    assert "BAM" in error_message


def test_duplicated_whiteboard_tag_fails(action_factory):
    with pytest.raises(ValueError) as exc_info:
        Actions(
            root=[
                action_factory(whiteboard_tag="x"),
                action_factory(whiteboard_tag="y"),
                action_factory(whiteboard_tag="x"),
            ]
        )
    assert "actions have duplicated lookup tags: ['x']" in str(exc_info.value)


def test_override_step_configuration_for_single_action_type():
    default_steps = ActionSteps()
    params = ActionParams(
        jira_project_key="JBI", steps=ActionSteps(new=["create_issue"])
    )
    assert params.steps.new == ["create_issue"]
    assert params.steps.new != default_steps.new
    assert params.steps.existing == default_steps.existing
    assert params.steps.comment == default_steps.comment


@pytest.mark.parametrize(
    "see_also,expected",
    [
        (None, None),
        ([], None),
        (["foo"], None),
        (["fail:/format"], None),
        # Non-matching project keys should return None
        (["foo", "http://jira.net/123"], None),
        (["http://org/123"], None),
        (["http://jira.com"], None),
        (["http://mozilla.jira.com/"], None),
        (["http://mozilla.jira.com/123"], None),
        (["http://mozilla.jira.com/123/"], None),
        (["http://mozilla.jira.com/ticket/123"], None),
        (["http://atlassian.com/ticket/123"], None),
        (["http://mozilla.jira.com/123", "http://mozilla.jira.com/456"], None),
        # Multiple Jira issues from different projects should return None if none match
        (
            ["http://mozilla.jira.com/FOO-123", "http://mozilla.jira.com/BAR-456"],
            None,
        ),
        # Issue keys that don't match the project format should return None
        (
            ["http://mozilla.jira.com/FOO-123", "http://mozilla.jira.com/JBI456"],
            None,
        ),
        # Only return issue key if it matches the specified project
        (
            ["http://mozilla.jira.com/FOO-123", "http://mozilla.jira.com/JBI-456"],
            "JBI-456",
        ),
        # Test the specific scenario: BZFFX issue shouldn't prevent GENAI creation
        (
            ["http://mozilla.jira.com/BZFFX-123"],
            None,
        ),
    ],
)
def test_extract_see_also(see_also, expected, bug_factory):
    bug = bug_factory(see_also=see_also)
    assert bug.extract_from_see_also("JBI") == expected


def test_extract_see_also_different_projects(bug_factory):
    """Test that a bug with a BZFFX issue can still match GENAI project."""
    bug = bug_factory(see_also=["http://mozilla.jira.com/browse/BZFFX-123"])
    # When looking for GENAI project, should return None (allowing new ticket creation)
    assert bug.extract_from_see_also("GENAI") is None
    # When looking for BZFFX project, should return the issue key
    assert bug.extract_from_see_also("BZFFX") == "BZFFX-123"


def test_extract_see_also_multiple_projects(bug_factory):
    """Test that extract_from_see_also correctly handles bugs linked to multiple projects."""
    bug = bug_factory(
        see_also=[
            "http://mozilla.jira.com/browse/BZFFX-123",
            "http://mozilla.jira.com/browse/GENAI-456",
        ]
    )
    # Each project should only match its own issue
    assert bug.extract_from_see_also("BZFFX") == "BZFFX-123"
    assert bug.extract_from_see_also("GENAI") == "GENAI-456"
    # Non-matching project should return None
    assert bug.extract_from_see_also("FOOBAR") is None


@pytest.mark.parametrize(
    "product,component,expected",
    [
        (None, None, ""),
        (None, "General", "General"),
        ("Product", None, "Product::"),
        ("Product", "General", "Product::General"),
    ],
)
def test_product_component(product, component, expected, bug_factory):
    bug = bug_factory(product=product, component=component)
    assert bug.product_component == expected


def test_payload_empty_changes_list(webhook_event_factory):
    event = webhook_event_factory(routing_key="bug.modify", changes=None)
    assert event.changed_fields() == []


def test_payload_changes_list(webhook_event_change_factory, webhook_event_factory):
    changes = [
        webhook_event_change_factory(field="status", removed="OPEN", added="FIXED"),
        webhook_event_change_factory(
            field="assignee", removed="nobody@mozilla.org", added="mathieu@mozilla.com"
        ),
    ]
    event = webhook_event_factory(routing_key="bug.modify", changes=changes)
    assert event.changed_fields() == [
        "status",
        "assignee",
    ]


def test_payload_changes_coerces_numbers_to_strings(
    webhook_event_change_factory, webhook_event_factory
):
    changes = [
        webhook_event_change_factory(field="is_confirmed", removed="1", added=0),
    ]
    event = webhook_event_factory(routing_key="bug.modify", changes=changes)
    assert event.changed_fields() == ["is_confirmed"]
    assert event.changes[0].added == "0"


def test_max_configured_projects_raises_error(action_factory):
    actions = [action_factory(whiteboard_tag=str(i)) for i in range(51)]
    with pytest.raises(pydantic.ValidationError):
        Actions(root=actions)


def test_bug_accepts_depends_on_and_blocks_fields(bug_factory):
    """Test that Bug model accepts depends_on and blocks fields."""
    bug = bug_factory(depends_on=[123, 456], blocks=[789])
    assert bug.depends_on == [123, 456]
    assert bug.blocks == [789]


def test_bug_handles_empty_dependencies(bug_factory):
    """Test that Bug model handles empty/None dependencies."""
    bug_none = bug_factory(depends_on=None, blocks=None)
    assert bug_none.depends_on is None
    assert bug_none.blocks is None

    bug_empty = bug_factory(depends_on=[], blocks=[])
    assert bug_empty.depends_on == []
    assert bug_empty.blocks == []


def test_extract_from_see_also_with_project_key_returns_single_issue(bug_factory):
    """Test that extract_from_see_also returns single issue for specific project."""
    bug = bug_factory(
        see_also=[
            "http://mozilla.jira.com/browse/JBI-123",
            "http://mozilla.jira.com/browse/FIDEFE-456",
        ]
    )
    # Should return only the matching project
    assert bug.extract_from_see_also("JBI") == "JBI-123"
    assert bug.extract_from_see_also("FIDEFE") == "FIDEFE-456"
    assert bug.extract_from_see_also("GENAI") is None


def test_extract_from_see_also_with_no_project_key_returns_all_issues(bug_factory):
    """Test that extract_from_see_also returns list of all Jira issues when project_key is None."""
    bug = bug_factory(
        see_also=[
            "http://mozilla.jira.com/browse/JBI-123",
            "http://mozilla.jira.com/browse/FIDEFE-456",
            "http://mozilla.jira.com/browse/GENAI-789",
        ]
    )
    # Should return all Jira issue keys
    result = bug.extract_from_see_also(project_key=None)
    assert result == ["JBI-123", "FIDEFE-456", "GENAI-789"]


def test_extract_from_see_also_none_returns_empty_list_when_no_see_also(bug_factory):
    """Test that extract_from_see_also returns empty list when no see_also and project_key is None."""
    bug_none = bug_factory(see_also=None)
    assert bug_none.extract_from_see_also(project_key=None) == []

    bug_empty = bug_factory(see_also=[])
    assert bug_empty.extract_from_see_also(project_key=None) == []


def test_extract_from_see_also_none_filters_non_jira_urls(bug_factory):
    """Test that extract_from_see_also filters out non-Jira URLs when project_key is None."""
    bug = bug_factory(
        see_also=[
            "http://example.com/bug/123",
            "http://mozilla.jira.com/browse/JBI-123",
            "fail:/format",
            "http://mozilla.jira.com/browse/FIDEFE-456",
        ]
    )
    result = bug.extract_from_see_also(project_key=None)
    assert result == ["JBI-123", "FIDEFE-456"]


@pytest.mark.parametrize("value", ["P0", "p1", "high", "S1", ""])
def test_invalid_min_priority_is_rejected(value):
    """R-04 thresholds are compared against BMO's fixed priority vocabulary,
    so a typo in config must fail at load rather than silently never match."""
    with pytest.raises(ValueError) as exc_info:
        ActionParams(jira_project_key="JBI", min_priority=value)
    assert "min_priority" in str(exc_info.value)


@pytest.mark.parametrize("value", ["S0", "s1", "critical", "P1", ""])
def test_invalid_min_severity_is_rejected(value):
    with pytest.raises(ValueError) as exc_info:
        ActionParams(jira_project_key="JBI", min_severity=value)
    assert "min_severity" in str(exc_info.value)


def test_non_invertible_resolution_map_is_rejected():
    """The reverse direction recovers a BMO resolution by inverting this map,
    so a many-to-one map must fail at load rather than silently write the
    wrong resolution onto a bug (plan section 4.1)."""
    with pytest.raises(ValueError) as exc_info:
        ActionParams(
            jira_project_key="JBI",
            resolution_map={"WONTFIX": "Done", "FIXED": "Done"},
        )

    assert "invertible" in str(exc_info.value)


def test_injective_resolution_map_is_accepted():
    params = ActionParams(
        jira_project_key="JBI",
        resolution_map={"FIXED": "Done", "WONTFIX": "Won't Do"},
    )

    assert params.resolution_map["WONTFIX"] == "Won't Do"


def test_every_real_config_resolution_map_is_invertible():
    """All 17 prod resolution maps are injective today; this keeps them that
    way, which is the premise the reverse resolution mapping rests on."""
    from jbi import configuration

    for config_file in (
        "config/config.local.yaml",
        "config/config.nonprod.yaml",
        "config/config.prod.yaml",
    ):
        # Loading is the assertion: a non-invertible map raises.
        configuration.get_actions_from_file(jbi_config_file=config_file)


@pytest.mark.parametrize("key", ["Done", "done ", "in progress"])
def test_reverse_status_overrides_reject_status_names(key):
    """Overrides are keyed by status *category*, not by a project's status
    names -- keying on names is exactly the per-project sprawl this avoids."""
    with pytest.raises(ValueError) as exc_info:
        ActionParams(jira_project_key="JBI", reverse_status_overrides={key: "NEW"})

    assert "status" in str(exc_info.value)
