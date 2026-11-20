"""Custom exceptions for JBI"""


class ActionNotFoundError(Exception):
    """No Action could be found for this bug"""


class IgnoreInvalidRequestError(Exception):
    """Error thrown when requests are invalid and ignored"""


class ActionError(Exception):
    """Error occurred during Action handling"""


class RetryLaterError(Exception):
    """The event cannot be handled yet but should be once state settles.

    Not an error in JBI: the transport is asked to redeliver, and the event is
    logged at INFO rather than as a failure.
    """
