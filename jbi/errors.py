"""Custom exceptions for JBI"""


class ActionNotFoundError(Exception):
    """No Action could be found for this bug"""


class IgnoreInvalidRequestError(Exception):
    """Error thrown when requests are invalid and ignored"""


class ActionError(Exception):
    """Error occurred during Action handling"""


class IncompleteStepError(Exception):
    """Raised when a step could not complete successfully."""

    def __init__(self, context, *args: object) -> None:
        super().__init__(*args)
        self.context = context
