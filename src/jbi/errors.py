"""Custom exceptions for JBI"""


class IgnoreInvalidRequestError(Exception):
    """Error thrown when requests are invalid and ignored"""


class ActionError(Exception):
    """Error occurred during Action handling"""
