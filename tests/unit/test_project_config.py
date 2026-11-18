"""Per-project mapping files (`config/projects/<JIRA_KEY>.yaml`)."""

import logging
from pathlib import Path

import pytest

from jbi import configuration
from jbi.configuration import ConfigError, get_project_configs

ACTIONS = """
- whiteboard_tag: alpha
  bugzilla_user_id: tbd
  description: Alpha
  parameters:
    jira_project_key: ALPHA
    jira_inbound_enabled: true
    status_map: &shared
      ASSIGNED: In Progress
    resolution_map:
      FIXED: Done
- whiteboard_tag: alpha-two
  bugzilla_user_id: tbd
  description: Alpha, second tag
  parameters:
    jira_project_key: alpha
    status_map: *shared
- whiteboard_tag: beta
  bugzilla_user_id: tbd
  description: Beta
  parameters:
    jira_project_key: BETA
    status_map: *shared
"""


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / "config.test.yaml").write_text(ACTIONS)
    (tmp_path / "projects").mkdir()
    return tmp_path


def _write_project(config_dir: Path, name: str, body: str):
    (config_dir / "projects" / name).write_text(body)


def _load(config_dir: Path):
    return configuration.get_actions_from_file(str(config_dir / "config.test.yaml"))


def test_no_project_files_changes_nothing(config_dir):
    actions = _load(config_dir)
    assert actions["alpha"].parameters.status_map == {"ASSIGNED": "In Progress"}
    assert actions["alpha"].parameters.phabricator_review_status == "In Review"


def test_project_config_wins_for_every_matching_action(config_dir):
    _write_project(
        config_dir,
        "ALPHA.yaml",
        """
jira_project_key: ALPHA
status_map:
  ASSIGNED: Doing
phabricator_review_status: Code Review
""",
    )
    actions = _load(config_dir)

    for tag in ("alpha", "alpha-two"):
        assert actions[tag].parameters.status_map == {"ASSIGNED": "Doing"}
        assert actions[tag].parameters.phabricator_review_status == "Code Review"


def test_other_projects_are_untouched(config_dir):
    """The anchored `status_map` is shared in YAML; overriding it for ALPHA
    must not leak into BETA."""
    _write_project(
        config_dir, "ALPHA.yaml", "jira_project_key: ALPHA\nstatus_map: {NEW: X}\n"
    )
    actions = _load(config_dir)

    assert actions["beta"].parameters.status_map == {"ASSIGNED": "In Progress"}


def test_unset_keys_keep_action_and_default_values(config_dir):
    _write_project(
        config_dir, "ALPHA.yaml", "jira_project_key: ALPHA\nstatus_map: {NEW: X}\n"
    )
    params = _load(config_dir)["alpha"].parameters

    assert params.resolution_map == {"FIXED": "Done"}  # from the action
    assert params.sync_stop_label == "jbi-sync-stop"  # built-in default


def test_maps_replace_rather_than_merge(config_dir):
    _write_project(
        config_dir, "ALPHA.yaml", "jira_project_key: ALPHA\nstatus_map: {NEW: X}\n"
    )
    params = _load(config_dir)["alpha"].parameters

    assert params.status_map == {"NEW": "X"}


def test_null_turns_a_default_off(config_dir):
    _write_project(
        config_dir, "ALPHA.yaml", "jira_project_key: ALPHA\nsync_stop_label: null\n"
    )
    assert _load(config_dir)["alpha"].parameters.sync_stop_label is None


def test_unknown_key_is_rejected(config_dir):
    _write_project(
        config_dir, "ALPHA.yaml", "jira_project_key: ALPHA\nstatus_mapp: {NEW: X}\n"
    )
    with pytest.raises(ConfigError, match="ALPHA.yaml"):
        _load(config_dir)


@pytest.mark.parametrize(
    "key", ["jira_inbound_enabled", "steps", "reverse_comment_sync_enabled"]
)
def test_onboarding_and_steps_cannot_be_set_here(config_dir, key):
    _write_project(config_dir, "ALPHA.yaml", f"jira_project_key: ALPHA\n{key}: true\n")
    with pytest.raises(ConfigError):
        _load(config_dir)


def test_file_name_must_match_project_key(config_dir):
    _write_project(config_dir, "ALPHA.yaml", "jira_project_key: BETA\n")
    with pytest.raises(ConfigError, match="must be named BETA.yaml"):
        _load(config_dir)


def test_non_invertible_resolution_map_is_rejected(config_dir):
    """The action validators still run on the merged values."""
    _write_project(
        config_dir,
        "ALPHA.yaml",
        "jira_project_key: ALPHA\nresolution_map: {FIXED: Done, WONTFIX: Done}\n",
    )
    with pytest.raises(ConfigError):
        _load(config_dir)


def test_unmatched_project_is_reported(config_dir, caplog):
    _write_project(config_dir, "GAMMA.yaml", "jira_project_key: GAMMA\n")
    with caplog.at_level(logging.WARNING):
        _load(config_dir)
    assert any("GAMMA" in r.getMessage() for r in caplog.records)


def test_readme_is_not_a_project(config_dir):
    _write_project(config_dir, "README.md", "# not yaml config\n")
    _load(config_dir)


def test_checked_in_project_files_are_valid():
    """Every team file in the repo parses and is named for its project."""
    get_project_configs(Path("config/projects"))
