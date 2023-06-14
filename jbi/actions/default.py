"""
The `default` action takes a list of steps from configuration and executes them
in chain.

The `runner` will call this action with an initialized context. When a Bugzilla ticket
is created or updated, its `operation` attribute will be `Operation.CREATE` or `Operation.UPDATE`,
and when a comment is posted, it will be set to `Operation.COMMENT`.
"""
import logging

from jbi import ActionResult, Operation
from jbi.actions import steps as steps_module
from jbi.environment import get_settings
from jbi.models import ActionContext, ActionParams, ActionSteps

settings = get_settings()

logger = logging.getLogger(__name__)

JIRA_REQUIRED_PERMISSIONS = {
    "ADD_COMMENTS",
    "CREATE_ISSUES",
    "DELETE_ISSUES",
    "EDIT_ISSUES",
}


def groups2operation(steps: ActionSteps):
    """In the configuration files, the steps are grouped by `new`, `existing`,
    and `comment`. Internally, this correspond to enums of `Operation`.
    This helper remaps the list of steps.
    """
    group_to_operation = {
        "new": Operation.CREATE,
        "existing": Operation.UPDATE,
        "comment": Operation.COMMENT,
    }
    try:
        by_operation = {
            group_to_operation[entry]: steps_list
            for entry, steps_list in steps.dict().items()
        }
    except KeyError as err:
        raise ValueError(f"Unsupported entry in `steps`: {err}") from err
    return by_operation


def init(parameters: ActionParams):
    return Executor(parameters)


class Executor:
    """Callable class that encapsulates the default action."""

    def __init__(self, parameters: ActionParams):
        """Initialize Executor Object"""
        self.parameters = parameters
        self.steps = self._initialize_steps(parameters.steps)

    def _initialize_steps(self, steps: ActionSteps):
        steps_by_operation = groups2operation(steps)

        # Turn the steps strings into references to functions of the `jbi.actions.steps` module.
        steps_callables = {
            group: [getattr(steps_module, step_str) for step_str in steps_list]
            for group, steps_list in steps_by_operation.items()
        }
        return steps_callables

    def __call__(self, context: ActionContext) -> ActionResult:
        """Called from `runner` when the action is used."""

        responses = tuple()  # type: ignore

        for step in self.steps[context.operation]:
            context, step_responses = step(context=context, parameters=self.parameters)
            for response in step_responses:
                logger.debug(
                    "Received %s",
                    response,
                    extra={
                        "response": response,
                        **context.dict(),
                    },
                )
            responses += step_responses

        return True, {"responses": responses}
