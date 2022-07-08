"""Custom exceptions for JBI"""


class ActionNotFoundError(Exception):
    """No Action could be found for this bug"""


class IgnoreInvalidRequestError(Exception):
    """Error thrown when requests are invalid and ignored"""


class ActionError(Exception):
    """Error occurred during Action handling"""
