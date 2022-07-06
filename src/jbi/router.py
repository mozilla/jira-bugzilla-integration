"""
Router dedicated to Jira Bugzilla Integration APIs
"""
import logging
from typing import List, Mapping, Optional, Tuple

from src.app.environment import Settings
from src.jbi.bugzilla import BugzillaBug, BugzillaWebhookRequest
from src.jbi.errors import IgnoreInvalidRequestError
from src.jbi.models import Action
from src.jbi.services import getbug_as_bugzilla_object

logger = logging.getLogger(__name__)


class Operations:
    """Track status of incoming requests in log entries."""

    HANDLE = "handle"
    EXECUTE = "execute"
    IGNORE = "ignore"
    SUCCESS = "success"


def extract_current_action(
    bug_obj: BugzillaBug, action_map: Mapping[str, Action]
) -> Optional[Tuple[str, Action]]:
    """Find first matching action from bug's whiteboard list"""
    potential_configuration_tags: List[
        str
    ] = bug_obj.get_potential_whiteboard_config_list()

    for tag in potential_configuration_tags:
        if action := action_map.get(tag.lower()):
            return tag.lower(), action
    return None


def execute_action(
    request: BugzillaWebhookRequest,
    action_map: Mapping[str, Action],
    settings: Settings,
):
    """Execute action"""
    log_context = {
        "bug": {
            "id": request.bug.id if request.bug else None,
        },
        "request": request.json(),
    }
    try:
        logger.debug(
            "Handling incoming request",
            extra={"operation": Operations.HANDLE, **log_context},
        )
        if not request.bug:
            raise IgnoreInvalidRequestError("no bug data received")

        bug_obj = getbug_as_bugzilla_object(request=request)
        log_context["bug"] = bug_obj.json()

        action_item = extract_current_action(bug_obj, action_map)
        if not action_item:
            raise IgnoreInvalidRequestError(
                "whiteboard tag not found in configured actions"
            )

        action_name, current_action = action_item
        log_context["action"] = current_action.dict()

        if bug_obj.is_private and not current_action.allow_private:
            raise IgnoreInvalidRequestError(
                f"private bugs are not valid for action {action_name!r}"
            )

        logger.info(
            "Execute action %r for Bug %s",
            action_name,
            bug_obj.id,
            extra={"operation": Operations.EXECUTE, **log_context},
        )

        content = current_action.callable(payload=request)

        logger.info(
            "Action %r executed successfully for Bug %s",
            action_name,
            bug_obj.id,
            extra={"operation": Operations.SUCCESS, **log_context},
        )
        return content
    except IgnoreInvalidRequestError as exception:
        logger.debug(
            "Ignore incoming request: %s",
            exception,
            extra={"operation": Operations.IGNORE, **log_context},
        )
        raise
