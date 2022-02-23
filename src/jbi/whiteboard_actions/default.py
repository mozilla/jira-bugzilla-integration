# Default actions below
from src.jbi import services


def default_action(data, context):
    print(services.get_bugzilla())
    print(services.get_jira())


def default_helper():
    pass
