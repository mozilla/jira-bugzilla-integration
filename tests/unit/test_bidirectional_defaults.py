"""Defaults for actions onboarded to bidirectional sync (D4, D5, D15).

An action with `jira_inbound_enabled: true` gets the Phabricator review
statuses, the review steps and the sync-stop label without per-team config.
Every other action must be exactly as it was.
"""

import pytest

from jbi import configuration
from jbi.models import (
    BIDIRECTIONAL_ATTACHMENT_STEPS,
    BIDIRECTIONAL_DEFAULTS,
    ActionParams,
    ActionSteps,
)

DEFAULT_ATTACHMENT_STEPS = ActionSteps().attachment


def _params(**kwargs) -> ActionParams:
    return ActionParams.model_validate({"jira_project_key": "JBI", **kwargs})


def test_onboarded_action_gets_defaults():
    params = _params(jira_inbound_enabled=True)

    assert params.phabricator_review_status == "In Review"
    assert params.phabricator_changes_requested_status == "In Progress"
    assert params.sync_stop_label == "jbi-sync-stop"
    assert params.reverse_comment_sync_enabled is True
    assert params.steps.attachment == [
        *DEFAULT_ATTACHMENT_STEPS,
        *BIDIRECTIONAL_ATTACHMENT_STEPS,
    ]


def test_action_not_onboarded_is_unchanged():
    params = _params()

    assert params.phabricator_review_status is None
    assert params.phabricator_changes_requested_status is None
    assert params.sync_stop_label is None
    assert params.reverse_comment_sync_enabled is False
    assert params.steps.attachment == DEFAULT_ATTACHMENT_STEPS


def test_explicit_values_win():
    params = _params(
        jira_inbound_enabled=True,
        phabricator_review_status="Code Review",
        phabricator_changes_requested_status="Rework",
        sync_stop_label="no-sync",
    )

    assert params.phabricator_review_status == "Code Review"
    assert params.phabricator_changes_requested_status == "Rework"
    assert params.sync_stop_label == "no-sync"


@pytest.mark.parametrize("key", sorted(BIDIRECTIONAL_DEFAULTS))
def test_explicit_off_opts_out(key):
    off = False if isinstance(BIDIRECTIONAL_DEFAULTS[key], bool) else None
    params = _params(jira_inbound_enabled=True, **{key: off})

    assert getattr(params, key) is off


def test_custom_attachment_steps_are_kept():
    params = _params(
        jira_inbound_enabled=True, steps={"attachment": ["create_comment"]}
    )

    assert params.steps.attachment == ["create_comment"]


def test_other_step_groups_are_kept():
    params = _params(jira_inbound_enabled=True, steps={"new": ["create_issue"]})

    assert params.steps.new == ["create_issue"]
    assert params.steps.existing == ActionSteps().existing
    assert params.steps.attachment[-2:] == BIDIRECTIONAL_ATTACHMENT_STEPS


def test_steps_given_as_model_instance():
    params = _params(jira_inbound_enabled=True, steps=ActionSteps(new=["create_issue"]))
    assert params.steps.new == ["create_issue"]
    assert params.steps.attachment[-2:] == BIDIRECTIONAL_ATTACHMENT_STEPS

    custom = _params(
        jira_inbound_enabled=True, steps=ActionSteps(attachment=["create_comment"])
    )
    assert custom.steps.attachment == ["create_comment"]


@pytest.mark.parametrize(
    "config_file",
    [
        "config/config.local.yaml",
        "config/config.nonprod.yaml",
        "config/config.prod.yaml",
    ],
)
def test_real_configs(config_file):
    """Onboarded actions get the defaults; every other action is untouched."""
    actions = configuration.get_actions_from_file(jbi_config_file=config_file)
    for action in actions:
        params = action.parameters
        if params.jira_inbound_enabled:
            assert params.phabricator_review_status == "In Review"
            assert params.sync_stop_label == "jbi-sync-stop"
            assert params.reverse_comment_sync_enabled is True
            for step in BIDIRECTIONAL_ATTACHMENT_STEPS:
                assert step in params.steps.attachment
        else:
            assert params.phabricator_review_status is None
            assert params.phabricator_changes_requested_status is None
            assert params.sync_stop_label is None
            assert params.reverse_comment_sync_enabled is False
            for step in BIDIRECTIONAL_ATTACHMENT_STEPS:
                assert step not in params.steps.attachment


# --- Through the runner: an onboarded action with no per-team settings ------


@pytest.fixture
def onboarded_actions(action_factory):
    from jbi.models import Actions

    return Actions(
        root=[
            action_factory(
                whiteboard_tag="devtest",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
            )
        ]
    )


def _patch_attached(webhook_request_factory, settings):
    return webhook_request_factory(
        event__target="attachment",
        event__action="create",
        bug__see_also=[f"{settings.jira_base_url}browse/JBI-234"],
        bug__with_attachment=True,
        bug__attachment__is_patch=True,
        bug__attachment__is_obsolete=False,
        bug__attachment__content_type="text/x-phabricator-request",
        bug__attachment__file_name="phabricator-D1234-url.txt",
    )


def _linked_issue(labels):
    return {
        "fields": {
            "project": {"key": "JBI"},
            "labels": labels,
            "status": {"statusCategory": {"key": "indeterminate"}},
        }
    }


def test_patch_moves_onboarded_issue_to_review(
    webhook_request_factory, onboarded_actions, mocked_jira, mocked_bugzilla, settings
):
    from jbi.runner import execute_action

    webhook = _patch_attached(webhook_request_factory, settings)
    mocked_bugzilla.get_bug.return_value = webhook.bug
    mocked_jira.get_issue.return_value = _linked_issue(["bugzilla"])

    execute_action(request=webhook, actions=onboarded_actions)

    mocked_jira.set_issue_status.assert_called_once_with("JBI-234", "In Review")


def test_default_stop_label_halts_onboarded_issue(
    webhook_request_factory, onboarded_actions, mocked_jira, mocked_bugzilla, settings
):
    from jbi.runner import execute_action

    webhook = _patch_attached(webhook_request_factory, settings)
    mocked_bugzilla.get_bug.return_value = webhook.bug
    mocked_jira.get_issue.return_value = _linked_issue(["bugzilla", "jbi-sync-stop"])

    execute_action(request=webhook, actions=onboarded_actions)

    assert not mocked_jira.set_issue_status.called
