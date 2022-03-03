"""
Default actions is listed below.
`init` is required; and requires at minimum the
`whiteboard_tag` and `jira_project_key`.

`init` should return a __call__able
"""


def init(whiteboard_tag, jira_project_key, **kwargs):
    """
    Function that takes required and optional params and returns a callable object
    :param whiteboard_tag: Required param
    :param jira_project_key: Required param
    :param kwargs: Optional params
    :return: DefaultExecutor
    """
    return DefaultExecutor(
        whiteboard_tag=whiteboard_tag, jira_project_key=jira_project_key, **kwargs
    )


class DefaultExecutor:
    """
    Callable class that encapsulates the default action
    """

    def __init__(self, **kwargs):
        """Initialize DefaultExecutor Object"""
        self.parameters = kwargs
        self.whiteboard_tag = kwargs.get("whiteboard_tag")
        self.jira_project_key = kwargs.get("jira_project_key")

    def __call__(self, payload):
        """
        Called from BZ webhook
        Call Jira SDK with project key etc.
        """
        print(payload)
        print(self.parameters)
