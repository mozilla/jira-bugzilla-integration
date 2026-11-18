"""
Parsing and validating the YAML configuration occurs within this module
"""

import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from ruamel.yaml import YAML

from jbi import environment
from jbi.models import Actions, ProjectConfig

logger = logging.getLogger(__name__)

PROJECTS_DIR_NAME = "projects"


class ConfigError(Exception):
    """Error when an exception occurs during processing config"""


def _load_yaml(path: Path):
    with open(path, encoding="utf8") as file:
        return YAML(typ="safe", pure=True).load(file)


def get_project_configs(projects_dir: Path) -> dict[str, ProjectConfig]:
    """Load every `<JIRA_KEY>.yaml` in `projects_dir`, keyed by project key.

    A team's file is matched to actions by Jira project key, so the file name
    and the `jira_project_key` inside it must agree; a mismatch is almost
    always a copy-paste of another team's file, and is rejected.
    """
    configs: dict[str, ProjectConfig] = {}
    if not projects_dir.is_dir():
        return configs

    for path in sorted(projects_dir.glob("*.y*ml")):
        try:
            config = ProjectConfig.model_validate(_load_yaml(path) or {})
        except ValidationError as exception:
            logger.exception(exception)
            raise ConfigError(f"Errors exist in {path}.") from exception

        key = config.jira_project_key.upper()
        if path.stem.upper() != key:
            raise ConfigError(
                f"{path} sets jira_project_key {config.jira_project_key!r}; "
                f"the file must be named {key}.yaml"
            )
        if key in configs:
            raise ConfigError(f"More than one project config for {key}")
        configs[key] = config
    return configs


def _apply_project_configs(raw_actions, project_configs: dict[str, ProjectConfig]):
    """Overlay each team's project config onto its actions' parameters.

    Project config wins over the action's own values. It is applied to the
    raw data, before validation, so every validator -- the invertible
    `resolution_map` check, the bidirectional defaults -- sees the final
    values.
    """
    if not project_configs or not isinstance(raw_actions, list):
        return raw_actions

    used: set[str] = set()
    merged = []
    for raw in raw_actions:
        parameters = raw.get("parameters") if isinstance(raw, dict) else None
        if not isinstance(parameters, dict):
            merged.append(raw)
            continue
        key = str(parameters.get("jira_project_key", "")).upper()
        project = project_configs.get(key)
        if project is None:
            merged.append(raw)
            continue
        used.add(key)
        # Copy rather than mutate: YAML anchors share one dict across actions.
        merged.append({**raw, "parameters": {**parameters, **project.overrides()}})

    for key in sorted(set(project_configs) - used):
        logger.warning(
            "Project config for %s matches no configured action; it has no effect",
            key,
        )
    return merged


def get_actions_from_file(
    jbi_config_file: str, projects_dir: Optional[str] = None
) -> Actions:
    """Convert and validate YAML configuration to `Action` objects.

    Team project configs are read from `projects_dir`, which defaults to the
    `projects/` directory beside the config file.
    """
    config_path = Path(jbi_config_file)
    projects_path = (
        Path(projects_dir)
        if projects_dir is not None
        else config_path.parent / PROJECTS_DIR_NAME
    )
    try:
        raw_actions = _load_yaml(config_path)
        raw_actions = _apply_project_configs(
            raw_actions, get_project_configs(projects_path)
        )
        return Actions.model_validate(raw_actions)
    except ValidationError as exception:
        logger.exception(exception)
        raise ConfigError("Errors exist.") from exception


def get_actions(env=None) -> Actions:
    """Load actions from file determined by ENV name"""
    if env is None:
        settings = environment.get_settings()
        env = settings.env
    return get_actions_from_file(os.path.join("config", f"config.{env}.yaml"))
