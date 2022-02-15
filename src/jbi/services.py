# import bugzilla
# import jira


def get_service(param=None):
    return param


def bugzilla_check_health(settings):
    health = {"up": False}
    return health


def jira_check_health(settings):
    health = {"up": False}
    return health


def jbi_service_health_map(settings):
    return {
        "bugzilla": bugzilla_check_health(settings),
        "jira": jira_check_health(settings),
    }
