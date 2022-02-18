# Default actions below
from src.jbi import services


def default_action(data, context):
    print(services.bugzilla)
    print(services.jira)


def default_helper():
    pass
