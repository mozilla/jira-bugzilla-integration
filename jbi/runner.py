"""
Execute actions from Webhook requests
"""

import inspect
import itertools
import logging
import re
from typing import Optional, cast

from dockerflow.logging import request_id_context
from starlette.concurrency import run_in_threadpool
from statsd.defaults.env import statsd

from jbi import ActionResult, Operation, jira
from jbi import steps as steps_module
from jbi.bugzilla import models as bugzilla_models
from jbi.bugzilla.client import BugNotAccessibleError
from jbi.bugzilla.service import get_service as get_bugzilla_service
from jbi.environment import get_settings
from jbi.errors import ActionNotFoundError, IgnoreInvalidRequestError
from jbi.models import (
    Action,
    ActionContext,
    ActionParams,
    Actions,
    ActionSteps,
    JiraContext,
    RunnerContext,
)
from jbi.queue import DeadLetterQueue
from jbi.scope import bug_in_scope
from jbi.steps import StepStatus
from jbi.visibility import bug_restriction_reason
from jbi.writeback import sync_is_stopped

logger = logging.getLogger(__name__)

settings = get_settings()


def _tag_added_to_whiteboard(
    action: Action, event: bugzilla_models.WebhookEvent
) -> bool:
    """Return True when the whiteboard change added this action's project tag."""
    if not event.changes:
        return False
    for change in event.changes:
        if change.field == "whiteboard":
            search_string = r"\[" + action.whiteboard_tag + r"(-[^\]]*)*\]"
            removed_had_tag = bool(
                re.search(search_string, change.removed or "", re.IGNORECASE)
            )
            added_has_tag = bool(
                re.search(search_string, change.added or "", re.IGNORECASE)
            )
            return not removed_had_tag and added_has_tag
    return False


def _bug_in_action_scope(bug: bugzilla_models.Bug, action: Action) -> bool:
    """Return True when the bug is in this action's `sync_products_components`."""
    return bug_in_scope(bug, action.parameters.sync_products_components)


# R-04 thresholds. Ordered most-severe first, so a lower index means "at least
# as important as". Values outside these lists (``--``, ``""``, ``N/A``, or an
# unset field) are treated as *below* any configured threshold: BMO leaves both
# fields unset until triage, and the point of R-04 is to keep pre-triage bugs
# out of Jira.
PRIORITY_ORDER = ["P1", "P2", "P3", "P4", "P5"]
SEVERITY_ORDER = ["S1", "S2", "S3", "S4"]


def _meets_threshold(
    value: Optional[str], minimum: Optional[str], order: list[str]
) -> bool:
    """Return True when `value` is at least as important as `minimum`."""
    if not minimum:
        return True
    if value not in order:
        return False
    return order.index(value) <= order.index(minimum)


def _bug_meets_sync_thresholds(bug: bugzilla_models.Bug, action: Action) -> bool:
    """Return True when the bug is at or above the action's priority/severity bar.

    Both thresholds must be met when both are configured. Unset thresholds
    (``None``) impose no restriction, which is today's behavior.
    """
    params = action.parameters
    return _meets_threshold(
        bug.priority, params.min_priority, PRIORITY_ORDER
    ) and _meets_threshold(bug.severity, params.min_severity, SEVERITY_ORDER)


GROUP_TO_OPERATION = {
    "new": Operation.CREATE,
    "existing": Operation.UPDATE,
    "comment": Operation.COMMENT,
    "attachment": Operation.ATTACHMENT,
}


def groups2operation(steps: ActionSteps):
    """In the configuration files, the steps are grouped by `new`, `existing`,
    and `comment`. Internally, this correspond to enums of `Operation`.
    This helper remaps the list of steps.
    """
    try:
        by_operation = {
            GROUP_TO_OPERATION[entry]: steps_list
            for entry, steps_list in steps.model_dump().items()
        }
    except KeyError as err:
        raise ValueError(f"Unsupported entry in `steps`: {err}") from err
    return by_operation


def lookup_actions(bug: bugzilla_models.Bug, actions: Actions) -> list[Action]:
    """
    Find matching actions from bug's whiteboard field.

    Tags are strings between brackets and can have prefixes/suffixes
    using dashes (eg. ``[project]``, ``[project-moco]``, ``[project-moco-sprint1]``).
    """

    if bug.whiteboard:
        relevant_actions = []
        for tag, action in actions.by_tag.items():
            # [tag-word], [tag-], [tag], but not [word-tag] or [tagword]
            search_string = r"\[" + tag + r"(-[^\]]*)*\]"
            if re.search(search_string, bug.whiteboard, flags=re.IGNORECASE):
                relevant_actions.append(action)
        if len(relevant_actions):
            return relevant_actions

    raise ActionNotFoundError(", ".join(actions.by_tag.keys()))


class Executor:
    """Callable class that runs step functions for an action."""

    def __init__(
        self, parameters: ActionParams, bugzilla_service=None, jira_service=None
    ):
        self.parameters = parameters
        if not bugzilla_service:
            self.bugzilla_service = get_bugzilla_service()
        if not jira_service:
            self.jira_service = jira.get_service()
        self.steps = self._initialize_steps(parameters.steps)
        self.step_func_params = {
            "parameters": self.parameters,
            "bugzilla_service": self.bugzilla_service,
            "jira_service": self.jira_service,
        }

    def _initialize_steps(self, steps: ActionSteps):
        steps_by_operation = groups2operation(steps)
        steps_callables = {
            group: [getattr(steps_module, step_str) for step_str in steps_list]
            for group, steps_list in steps_by_operation.items()
        }
        return steps_callables

    def build_step_kwargs(self, func) -> dict:
        """Builds a dictionary of keyword arguments (kwargs) to be passed to the given `step` function.

        Args:
            func: The step function for which the kwargs are being built.

        Returns:
            A dictionary containing the kwargs that match the parameters of the function.
        """
        function_params = inspect.signature(func).parameters
        return {
            key: value
            for key, value in self.step_func_params.items()
            if key in function_params.keys()
        }

    def __call__(self, context: ActionContext) -> ActionResult:
        """Called from `runner` when the action is used."""
        has_produced_request = False

        for step in self.steps[context.operation]:
            context = context.update(current_step=step.__name__)
            step_kwargs = self.build_step_kwargs(step)
            try:
                result, context = step(context=context, **step_kwargs)
                if result == StepStatus.SUCCESS:
                    statsd.incr(f"jbi.steps.{step.__name__}.count")
                elif result == StepStatus.INCOMPLETE:
                    # Step did not execute all its operations.
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
                logger.info(
                    "Received %s",
                    response,
                    extra={
                        "response": response,
                        **context.model_dump(),
                    },
                )

        # Flatten the list of all received responses.
        responses = list(
            itertools.chain.from_iterable(context.responses_by_step.values())
        )
        return True, {"responses": responses}


async def execute_or_queue(
    request: bugzilla_models.WebhookRequest, queue: DeadLetterQueue, actions: Actions
):
    request_id = request_id_context.get()

    if await queue.is_blocked(request):
        # If it's blocked, store it and wait for it to be processed later.
        await queue.postpone(request, rid=request_id)
        logger.info(
            "%r event on Bug %s was put in queue for later processing.",
            request.event.action,
            request.bug.id,
            extra={"payload": request.model_dump()},
        )
        return {"status": "skipped"}

    try:
        # `execute_action` performs blocking I/O (Bugzilla/Jira HTTP calls,
        # pandoc subprocess calls). This process runs a single asyncio event
        # loop with no other workers, so calling it directly here would
        # freeze the whole pod - including its own /__lbheartbeat__ health
        # check - for the duration of a slow event, tripping the liveness
        # probe. Run it on a thread instead so the event loop stays free.
        return await run_in_threadpool(execute_action, request, actions)
    except IgnoreInvalidRequestError as exc:
        return {"status": "invalid", "error": str(exc)}
    except Exception as exc:
        item = await queue.track_failed(request, exc, rid=request_id)
        logger.exception(
            "Failed to process %r event on Bug %s. %s was put in queue.",
            request.event.action,
            request.bug.id,
            item.identifier,
            extra={
                "payload": request.model_dump(),
                "item": item.model_dump(),
            },
        )
        return {"status": "failed", "error": str(exc)}


@statsd.timer("jbi.action.execution.timer")
def execute_action(
    request: bugzilla_models.WebhookRequest,
    actions: Actions,
):
    """Execute the configured actions for the specified `request`.

    If multiple actions are configured for a given request, all of them
    are executed.

    This will raise an `IgnoreInvalidRequestError` error if the request
    does not contain bug data or does not match any action.

    A dictionary containing the values returned by the actions calls
    is returned. The action tag is used to index the responses in the
    dictionary.
    """
    bug, event = request.bug, request.event
    runner_context = RunnerContext(
        bug=bug,
        event=event,
        operation=Operation.HANDLE,
    )
    try:
        # Security/embargoed bugs never reach Jira. `is_private` alone is not
        # enough: it is an optional payload field, and BMO expresses
        # confidentiality through `groups`. Checked here on the payload as an
        # early exit, and again after the refresh below, because a bug can
        # gain a group between the webhook firing and JBI processing it --
        # a window the dead-letter queue can widen to days.
        if reason := bug_restriction_reason(bug):
            raise IgnoreInvalidRequestError(
                f"restricted bugs are not supported: {reason}"
            )

        # Invariant C, BMO side: a reverse write into Bugzilla fires this same
        # webhook, so without this gate every Jira -> BMO write would bounce
        # straight back into Jira. Handled here rather than in the router so
        # a suppressed event is logged and counted like any other ignored one.
        #
        # `event.user` is optional in BMO payloads, so an actor-less event
        # cannot be matched and is allowed through (fail-open). D7's
        # read-before-write is what stops that case from oscillating: a write
        # of an unchanged value issues no request.
        if (
            settings.bugzilla_bot_login
            and event.user
            and event.user.login == settings.bugzilla_bot_login
        ):
            raise IgnoreInvalidRequestError(
                f"ignore event authored by JBI itself ({event.user.login})"
            )

        try:
            relevant_actions = lookup_actions(bug, actions)
        except ActionNotFoundError as err:
            raise IgnoreInvalidRequestError(
                f"no bug whiteboard matching action tags: {err}"
            ) from err

        logger.info(
            "Handling incoming request",
            extra=runner_context.model_dump(),
        )
        try:
            bug = get_bugzilla_service().refresh_bug_data(bug)
        except BugNotAccessibleError as err:
            # This can happen if the bug is made private after the webhook
            # is processed (eg. if it spent some time in the DL queue)
            raise IgnoreInvalidRequestError(str(err)) from err

        # Re-check on the refreshed bug. `BugNotAccessibleError` above only
        # catches bugs JBI *cannot read*; a bug restricted to a group JBI
        # belongs to stays readable, and would otherwise sync.
        if reason := bug_restriction_reason(bug):
            raise IgnoreInvalidRequestError(
                f"restricted bugs are not supported: {reason}"
            )

        # R-01: drop actions whose configured Product/Component scope does not
        # cover this bug. This is evaluated after `refresh_bug_data` so we scope
        # on the bug's current product/component rather than a stale payload.
        in_scope_actions = [
            action for action in relevant_actions if _bug_in_action_scope(bug, action)
        ]
        if not in_scope_actions:
            raise IgnoreInvalidRequestError(
                f"bug {bug.product_component!r} is out of scope for matching "
                f"actions: {', '.join(a.whiteboard_tag for a in relevant_actions)}"
            )

        runner_context = runner_context.update(bug=bug, actions=in_scope_actions)

        return do_execute_actions(runner_context, bug, in_scope_actions)
    except IgnoreInvalidRequestError as exception:
        logger.info(
            "Ignore incoming request: %s",
            exception,
            extra=runner_context.update(operation=Operation.IGNORE).model_dump(),
        )
        statsd.incr("jbi.bugzilla.ignored.count")
        raise


@statsd.timer("jbi.action.execution.timer")
def do_execute_actions(
    runner_context: RunnerContext,
    bug: bugzilla_models.Bug,
    actions: Actions,
):
    """Execute the provided actions on the bug, within the provided context.

    This will raise an `IgnoreInvalidRequestError` error if the request
    does not contain bug data or does not match any action.

    A dictionary containing the values returned by the actions calls
    is returned. The action tag is used to index the responses in the
    dictionary.
    """
    runner_context = runner_context.update(bug=bug)

    runner_context = runner_context.update(actions=actions)

    event = runner_context.event

    details = {}
    for action in actions:
        # When project_key is provided, extract_from_see_also returns Optional[str], not list
        linked_issue_key: Optional[str] = cast(
            Optional[str],
            bug.extract_from_see_also(project_key=action.jira_project_key),
        )

        action_context = ActionContext(
            action=action,
            bug=bug,
            event=event,
            operation=Operation.IGNORE,
            jira=JiraContext(project=action.jira_project_key, issue=linked_issue_key),
            extra={k: str(v) for k, v in action.parameters.model_dump().items()},
        )

        if action_context.jira.issue is None:
            if event.target == "bug":
                # R-04: only gate *creation*. Once a bug has a linked issue we
                # keep syncing it even if it later drops below the threshold,
                # otherwise an existing Jira issue would silently stop tracking
                # its bug (worse than never having been created).
                if not _bug_meets_sync_thresholds(bug, action):
                    logger.info(
                        "Bug %s is below the sync threshold of action %r "
                        "(priority=%r, severity=%r)",
                        bug.id,
                        action.whiteboard_tag,
                        bug.priority,
                        bug.severity,
                        extra=action_context.update(
                            operation=Operation.IGNORE
                        ).model_dump(),
                    )
                    statsd.incr("jbi.bugzilla.ignored.count")
                    continue

                action_context = action_context.update(operation=Operation.CREATE)

        else:
            # Check that issue exists (and is readable)
            jira_issue = jira.get_service().get_issue(
                action_context.update(operation=Operation.HANDLE),
                action_context.jira.issue,
            )
            if not jira_issue:
                raise IgnoreInvalidRequestError(
                    f"ignore unreadable issue {action_context.jira.issue}"
                )

            # The stop label lives on the Jira issue, and that issue has just
            # been fetched for the project check below -- so this costs no
            # extra API call.
            if sync_is_stopped(
                jira_issue["fields"].get("labels") or [],
                action.parameters.sync_stop_label,
            ):
                logger.info(
                    "Sync stopped by the %r label on %s; skipping action %r for Bug %s",
                    action.parameters.sync_stop_label,
                    action_context.jira.issue,
                    action.whiteboard_tag,
                    bug.id,
                    extra=action_context.update(
                        operation=Operation.IGNORE
                    ).model_dump(),
                )
                statsd.incr("jbi.sync_stopped.count")
                continue

            # Make sure that associated project in configuration matches the
            # project of the linked Jira issue (see #635)
            if (
                project_key := jira_issue["fields"]["project"]["key"]
            ) != action_context.jira.project:
                # TODO: We're now executing multiple actions for a given bug, we
                # should probably either not fail and instead report which actions
                # failed to apply, or execute all the changes as a "transaction" and
                # roll them back if one of them fails.
                raise IgnoreInvalidRequestError(
                    f"ignore linked project {project_key!r} (!={action_context.jira.project!r})"
                )

            if event.target == "bug":
                if _tag_added_to_whiteboard(action, event):
                    # Tag was added to a bug that already has a linked Jira issue.
                    # Use CREATE so that steps sync all current field values
                    # unconditionally, rather than only reacting to the fields
                    # that changed in this single event. create_issue detects
                    # the existing issue and updates the summary instead.
                    action_context = action_context.update(operation=Operation.CREATE)
                else:
                    action_context = action_context.update(
                        operation=Operation.UPDATE,
                        extra={
                            "changed_fields": ", ".join(event.changed_fields()),
                            **action_context.extra,
                        },
                    )

            elif event.target == "comment":
                action_context = action_context.update(operation=Operation.COMMENT)

            elif event.target == "attachment":
                action_context = action_context.update(operation=Operation.ATTACHMENT)

        if action_context.operation == Operation.IGNORE:
            raise IgnoreInvalidRequestError(
                f"ignore event target {action_context.event.target!r}"
            )

        logger.info(
            "Execute action '%s' for Bug %s",
            action.whiteboard_tag,
            bug.id,
            extra=runner_context.update(operation=Operation.EXECUTE).model_dump(),
        )
        executor = Executor(parameters=action.parameters)
        handled, action_details = executor(context=action_context)
        details[action.whiteboard_tag] = action_details
        statsd.incr(f"jbi.operation.{action_context.operation.lower()}.count")
        logger.info(
            "Action %r executed successfully for Bug %s",
            action.whiteboard_tag,
            bug.id,
            extra=runner_context.update(
                operation=Operation.SUCCESS if handled else Operation.IGNORE
            ).model_dump(),
        )
        statsd.incr("jbi.bugzilla.processed.count")
    return details
