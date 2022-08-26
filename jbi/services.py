"""Services and functions that can be used to create custom actions"""
from __future__ import annotations

import concurrent.futures
import logging
from typing import TYPE_CHECKING

import backoff
import bugzilla as rh_bugzilla
from atlassian import Jira, errors
from statsd.defaults.env import statsd

from jbi import environment
from jbi.models import BugzillaBug, BugzillaComment

if TYPE_CHECKING:
    from jbi.models import Actions

settings = environment.get_settings()

logger = logging.getLogger(__name__)


ServiceHealth = dict[str, bool]


class InstrumentedClient:
    """This class wraps an object and increments a counter every time
    the specified methods are called, and times their execution.
    It retries the methods if the specified exceptions are raised.
    """

    def __init__(self, wrapped, prefix, methods, exceptions):
        self.wrapped = wrapped
        self.prefix = prefix
        self.methods = methods
        self.exceptions = exceptions

    def __getattr__(self, attr):
        if attr not in self.methods:
            return getattr(self.wrapped, attr)

        @backoff.on_exception(
            backoff.expo,
            self.exceptions,
            max_tries=settings.max_retries + 1,
        )
        def wrapped_func(*args, **kwargs):
            # Increment the call counter.
            statsd.incr(f"jbi.{self.prefix}.methods.{attr}.count")
            # Time its execution.
            with statsd.timer(f"jbi.{self.prefix}.methods.{attr}.timer"):
                return getattr(self.wrapped, attr)(*args, **kwargs)

        # The method was not called yet.
        return wrapped_func


def get_jira():
    """Get atlassian Jira Service"""
    jira_client = Jira(
        url=settings.jira_base_url,
        username=settings.jira_username,
        password=settings.jira_api_key,  # package calls this param 'password' but actually expects an api key
        cloud=True,  # we run against an instance of Jira cloud
    )
    instrumented_methods = (
        "update_issue_field",
        "set_issue_status",
        "issue_add_comment",
        "create_issue",
    )
    return InstrumentedClient(
        wrapped=jira_client,
        prefix="jira",
        methods=instrumented_methods,
        exceptions=(errors.ApiError,),
    )


def jira_visible_projects(jira=None) -> list[dict]:
    """Return list of projects that are visible with the configured Jira credentials"""
    jira = jira or get_jira()
    projects: list[dict] = jira.projects(included_archived=None)
    return projects


class BugzillaClient:
    """
    Wrapper around the Bugzilla client to turn responses into our models instances.
    """

    def __init__(self, base_url: str, api_key: str):
        """Constructor"""
        self._client = rh_bugzilla.Bugzilla(base_url, api_key=api_key)

    @property
    def logged_in(self):
        """Return `true` if credentials are valid"""
        return self._client.logged_in

    def get_bug(self, bugid) -> BugzillaBug:
        """Return the Bugzilla object with all attributes"""
        response = self._client.getbug(bugid).__dict__
        bug = BugzillaBug.parse_obj(response)
        # If comment is private, then webhook does not have comment, fetch it from server
        if bug.comment and bug.comment.is_private:
            comment_list = self.get_comments(bugid)
            matching_comments = [c for c in comment_list if c.id == bug.comment.id]
            found = matching_comments[0] if matching_comments else None
            bug = bug.copy(update={"comment": found})
        return bug

    def get_comments(self, bugid) -> list[BugzillaComment]:
        """Return the list of comments for the specified bug ID"""
        response = self._client.get_comments(idlist=[bugid])
        comments = response["bugs"][str(bugid)]["comments"]
        return [BugzillaComment.parse_obj(comment) for comment in comments]

    def update_bug(self, bugid, **attrs):
        """Update the specified bug with the specified attributes"""
        update = self._client.build_update(**attrs)
        return self._client.update_bugs([bugid], update)


def get_bugzilla():
    """Get bugzilla service"""
    bugzilla_client = BugzillaClient(
        settings.bugzilla_base_url, api_key=str(settings.bugzilla_api_key)
    )
    instrumented_methods = (
        "get_bug",
        "get_comments",
        "update_bug",
    )
    return InstrumentedClient(
        wrapped=bugzilla_client,
        prefix="bugzilla",
        methods=instrumented_methods,
        exceptions=(rh_bugzilla.BugzillaError,),
    )


def _bugzilla_check_health() -> ServiceHealth:
    """Check health for Bugzilla Service"""
    bugzilla = get_bugzilla()
    health: ServiceHealth = {"up": bugzilla.logged_in}
    return health


def _jira_check_health(actions: Actions) -> ServiceHealth:
    """Check health for Jira Service"""
    jira = get_jira()
    server_info = jira.get_server_info(True)
    is_up = server_info is not None
    health: ServiceHealth = {
        "up": is_up,
        "all_projects_are_visible": is_up and _all_jira_projects_visible(jira, actions),
        "all_projects_have_permissions": _all_jira_projects_permissions(jira, actions),
    }
    return health


def _all_jira_projects_visible(jira, actions: Actions) -> bool:
    visible_projects = {project["key"] for project in jira_visible_projects(jira)}
    missing_projects = actions.configured_jira_projects_keys - visible_projects
    if missing_projects:
        logger.error(
            "Jira projects %s are not visible with configured credentials",
            missing_projects,
        )
    return not missing_projects


def _all_jira_projects_permissions(jira, actions: Actions):
    """Fetches and validates that required permissions exist for the configured projects"""
    all_projects_perms = _fetch_jira_project_permissions(actions, jira)
    return _validate_jira_permissions(all_projects_perms)


def _fetch_jira_project_permissions(actions, jira):
    """Fetches permissions for the configured projects"""
    required_perms_by_project = {
        action.parameters["jira_project_key"]: action.required_jira_permissions
        for action in actions
        if "jira_project_key" in action.parameters
    }

    all_projects_perms = {}
    # Query permissions for all configured projects in parallel threads.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures_to_projects = {
            executor.submit(
                jira.get_permissions,
                project_key=project_key,
                permissions=",".join(required_permissions),
            ): project_key
            for project_key, required_permissions in required_perms_by_project.items()
        }
        # Obtain futures' results unordered.
        for future in concurrent.futures.as_completed(futures_to_projects):
            project_key = futures_to_projects[future]
            response = future.result()
            all_projects_perms[project_key] = (
                required_perms_by_project[project_key],
                response["permissions"],
            )
    return all_projects_perms


def _validate_jira_permissions(all_projects_perms):
    """Validates permissions for the configured projects"""
    misconfigured = []
    for project_key, (required_perms, obtained_perms) in all_projects_perms.items():
        missing = required_perms - set(obtained_perms.keys())
        not_given = set(
            entry["key"]
            for entry in obtained_perms.values()
            if not entry["havePermission"]
        )
        if missing | not_given:
            misconfigured.append((project_key, missing | not_given))
    for project_key, missing in misconfigured:
        logger.error(
            "Configured credentials don't have permissions %s on Jira project %s",
            ",".join(missing),
            project_key,
            extra={
                "jira": {
                    "project": project_key,
                }
            },
        )
    return not misconfigured


def jbi_service_health_map(actions: Actions):
    """Returns dictionary of health check's for Bugzilla and Jira Services"""
    return {
        "bugzilla": _bugzilla_check_health(),
        "jira": _jira_check_health(actions),
    }
