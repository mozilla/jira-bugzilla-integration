"""
Execute actions from Webhook requests
"""
import logging

from src.app.environment import Settings
from src.jbi.bugzilla import BugzillaBug, BugzillaWebhookRequest
from src.jbi.errors import ActionNotFoundError, IgnoreInvalidRequestError
from src.jbi.models import Actions
from src.jbi.services import getbug_as_bugzilla_object

logger = logging.getLogger(__name__)


class Operations:
    """Track status of incoming requests in log entries."""

    HANDLE = "handle"
    EXECUTE = "execute"
    IGNORE = "ignore"
    SUCCESS = "success"


def execute_action(
    request: BugzillaWebhookRequest,
    actions: Actions,
    settings: Settings,
):
    """Execute the configured action for the specified `request`.

    This will raise an `IgnoreInvalidRequestError` error if the request
    does not contain bug data or does not match any action.

    The value returned by the action call is returned.
    """
    log_context = {
        "bug": {
            "id": request.bug.id if request.bug else None,
        },
        "request": request.dict(),
    }
    try:
        logger.debug(
            "Handling incoming request",
            extra={"operation": Operations.HANDLE, **log_context},
        )
        if not request.bug:
            raise IgnoreInvalidRequestError("no bug data received")

        bug_obj: BugzillaBug = getbug_as_bugzilla_object(request=request)
        log_context["bug"] = bug_obj.dict()

        try:
            action_name, current_action = bug_obj.lookup_action(actions)
        except ActionNotFoundError as err:
            raise IgnoreInvalidRequestError(
                f"no action matching bug whiteboard tags: {err}"
            ) from err

        log_context["action"] = current_action.dict()

        if bug_obj.is_private and not current_action.allow_private:
            raise IgnoreInvalidRequestError(
                f"private bugs are not valid for action {action_name!r}"
            )

        logger.info(
            "Execute action '%s:%s' for Bug %s",
            action_name,
            current_action.action,
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
