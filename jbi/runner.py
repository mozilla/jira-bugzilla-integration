"""
Execute actions from Webhook requests
"""
import logging

from statsd.defaults.env import statsd

from jbi import Operation
from jbi.environment import Settings
from jbi.errors import ActionNotFoundError, IgnoreInvalidRequestError
from jbi.models import Actions, BugzillaBug, BugzillaWebhookRequest

logger = logging.getLogger(__name__)


@statsd.timer("jbi.action.execution.timer")
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
            extra={"operation": Operation.HANDLE, **log_context},
        )
        if not request.bug:
            raise IgnoreInvalidRequestError("no bug data received")

        try:
            bug_obj: BugzillaBug = request.bugzilla_object
        except Exception as ex:
            logger.exception("Failed to get bug: %s", ex, extra=log_context)
            raise IgnoreInvalidRequestError(
                "bug not accessible or bugzilla down"
            ) from ex
        log_context["bug"] = bug_obj.dict()

        try:
            action = bug_obj.lookup_action(actions)
        except ActionNotFoundError as err:
            raise IgnoreInvalidRequestError(
                f"no action matching bug whiteboard tags: {err}"
            ) from err

        log_context["action"] = action.dict()

        if bug_obj.is_private and not action.allow_private:
            raise IgnoreInvalidRequestError(
                f"private bugs are not valid for action {action.whiteboard_tag!r}"
            )

        logger.info(
            "Execute action '%s:%s' for Bug %s",
            action.whiteboard_tag,
            action.module,
            bug_obj.id,
            extra={"operation": Operation.EXECUTE, **log_context},
        )

        handled, details = action.caller(payload=request)

        logger.info(
            "Action %r executed successfully for Bug %s",
            action.whiteboard_tag,
            bug_obj.id,
            extra={
                "operation": Operation.SUCCESS if handled else Operation.IGNORE,
                **log_context,
            },
        )
        statsd.incr("jbi.bugzilla.processed.count")
        return details
    except IgnoreInvalidRequestError as exception:
        logger.debug(
            "Ignore incoming request: %s",
            exception,
            extra={"operation": Operation.IGNORE, **log_context},
        )
        statsd.incr("jbi.bugzilla.ignored.count")
        raise
