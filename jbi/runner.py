"""
Execute actions from Webhook requests
"""
import itertools
import logging
from typing import Optional

from statsd.defaults.env import statsd

from jbi import ActionResult, Operation
from jbi import steps as steps_module
from jbi.environment import get_settings
from jbi.errors import (
    ActionNotFoundError,
    IgnoreInvalidRequestError,
    IncompleteStepError,
)
from jbi.models import (
    ActionContext,
    ActionParams,
    Actions,
    ActionSteps,
    BugzillaWebhookComment,
    BugzillaWebhookRequest,
    JiraContext,
    RunnerContext,
)
from jbi.services import bugzilla, jira

logger = logging.getLogger(__name__)

settings = get_settings()


GROUP_TO_OPERATION = {
    "new": Operation.CREATE,
    "existing": Operation.UPDATE,
    "comment": Operation.COMMENT,
}


def groups2operation(steps: ActionSteps):
    """In the configuration files, the steps are grouped by `new`, `existing`,
    and `comment`. Internally, this correspond to enums of `Operation`.
    This helper remaps the list of steps.
    """
    try:
        by_operation = {
            GROUP_TO_OPERATION[entry]: steps_list
            for entry, steps_list in steps.dict().items()
        }
    except KeyError as err:
        raise ValueError(f"Unsupported entry in `steps`: {err}") from err
    return by_operation


class Executor:
    """Callable class that runs step functions for an action."""

    def __init__(self, parameters: ActionParams):
        self.parameters = parameters
        self.steps = self._initialize_steps(parameters.steps)

    def _initialize_steps(self, steps: ActionSteps):
        steps_by_operation = groups2operation(steps)
        steps_callables = {
            group: [getattr(steps_module, step_str) for step_str in steps_list]
            for group, steps_list in steps_by_operation.items()
        }
        return steps_callables

    def __call__(self, context: ActionContext) -> ActionResult:
        """Called from `runner` when the action is used."""
        has_produced_request = False

        for step in self.steps[context.operation]:
            context = context.update(current_step=step.__name__)
            try:
                context = step(context=context, parameters=self.parameters)
            except IncompleteStepError as exc:
                # Step did not execute all its operations.
                context = exc.context
                statsd.incr(
                    f"jbi.action.{context.action.whiteboard_tag}.incomplete.count"
                )
            except Exception:
                if has_produced_request:
                    # Count the number of workflows that produced at least one request,
                    # but could not complete entirely with success.
                    statsd.incr(
                        f"jbi.action.{context.action.whiteboard_tag}.aborted.count"
                    )
                raise

            step_responses = context.responses_by_step[step.__name__]
            if step_responses:
                has_produced_request = True
            for response in step_responses:
                logger.debug(
                    "Received %s",
                    response,
                    extra={
                        "response": response,
                        **context.dict(),
                    },
                )

        # Flatten the list of all received responses.
        responses = list(
            itertools.chain.from_iterable(context.responses_by_step.values())
        )
        return True, {"responses": responses}


@statsd.timer("jbi.action.execution.timer")
def execute_action(
    request: BugzillaWebhookRequest,
    actions: Actions,
):
    """Execute the configured action for the specified `request`.

    This will raise an `IgnoreInvalidRequestError` error if the request
    does not contain bug data or does not match any action.

    The value returned by the action call is returned.
    """
    bug, event = request.bug, request.event
    webhook_comment: Optional[BugzillaWebhookComment] = bug.comment
    runner_context = RunnerContext(
        rid=request.rid,
        bug=bug,
        event=event,
        operation=Operation.HANDLE,
    )
    try:
        if bug.is_private:
            raise IgnoreInvalidRequestError("private bugs are not supported")

        logger.debug(
            "Handling incoming request",
            extra=runner_context.dict(),
        )
        try:
            bug = bugzilla.get_service().client.get_bug(
                bug.id
            )  # refresh bug data; this removes webhook specific info--but avoids duplications
            bug.comment = webhook_comment  # inject webhook data back into bug
        except Exception as err:
            logger.exception("Failed to get bug: %s", err, extra=runner_context.dict())
            raise IgnoreInvalidRequestError(
                "bug not accessible or bugzilla down"
            ) from err

        runner_context = runner_context.update(bug=bug)
        try:
            action = bug.lookup_action(actions)
        except ActionNotFoundError as err:
            raise IgnoreInvalidRequestError(
                f"no bug whiteboard matching action tags: {err}"
            ) from err
        runner_context = runner_context.update(action=action)

        linked_issue_key: Optional[str] = bug.extract_from_see_also()

        action_context = ActionContext(
            action=action,
            rid=request.rid,
            bug=bug,
            event=event,
            operation=Operation.IGNORE,
            jira=JiraContext(project=action.jira_project_key, issue=linked_issue_key),
            extra={k: str(v) for k, v in action.parameters.dict().items()},
        )

        if action_context.jira.issue is None:
            if event.target == "bug":
                action_context = action_context.update(operation=Operation.CREATE)

        else:
            # Check that issue exists (and is readable)
            if not jira.get_service().get_issue(
                action_context, action_context.jira.issue
            ):
                raise IgnoreInvalidRequestError(
                    f"ignore unreadable issue {action_context.jira.issue}"
                )

            if event.target == "bug":
                action_context = action_context.update(
                    operation=Operation.UPDATE,
                    extra={
                        "changed_fields": ", ".join(event.changed_fields()),
                        **action_context.extra,
                    },
                )

            elif event.target == "comment":
                action_context = action_context.update(operation=Operation.COMMENT)

        if action_context.operation == Operation.IGNORE:
            raise IgnoreInvalidRequestError(
                f"ignore event target {action_context.event.target!r}"
            )

        logger.info(
            "Execute action '%s' for Bug %s",
            action.whiteboard_tag,
            bug.id,
            extra=runner_context.update(operation=Operation.EXECUTE).dict(),
        )
        executor = Executor(parameters=action.parameters)
        handled, details = executor(context=action_context)

        logger.info(
            "Action %r executed successfully for Bug %s",
            action.whiteboard_tag,
            bug.id,
            extra=runner_context.update(
                operation=Operation.SUCCESS if handled else Operation.IGNORE
            ).dict(),
        )
        statsd.incr("jbi.bugzilla.processed.count")
        return details
    except IgnoreInvalidRequestError as exception:
        logger.debug(
            "Ignore incoming request: %s",
            exception,
            extra=runner_context.update(operation=Operation.IGNORE).dict(),
        )
        statsd.incr("jbi.bugzilla.ignored.count")
        raise
