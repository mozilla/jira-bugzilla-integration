"""
Core FastAPI app (setup, middleware)
"""

import secrets
from pathlib import Path
from typing import Annotated, Optional

from dockerflow import checks as dockerflow_checks
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from jbi import bugzilla, jira
from jbi.configuration import ACTIONS
from jbi.environment import Settings, get_settings, get_version
from jbi.models import Actions
from jbi.runner import IgnoreInvalidRequestError, execute_action

SettingsDep = Annotated[Settings, Depends(get_settings)]
ActionsDep = Annotated[Actions, Depends(lambda: ACTIONS)]
VersionDep = Annotated[dict, Depends(get_version)]
BugzillaServiceDep = Annotated[bugzilla.BugzillaService, Depends(bugzilla.get_service)]
JiraServiceDep = Annotated[jira.JiraService, Depends(jira.get_service)]

router = APIRouter()


@router.get("/", include_in_schema=False)
def root(request: Request, settings: SettingsDep):
    """Expose key configuration"""
    return {
        "title": request.app.title,
        "description": request.app.description,
        "version": request.app.version,
        "documentation": request.app.docs_url,
        "configuration": {
            "jira_base_url": settings.jira_base_url,
            "bugzilla_base_url": settings.bugzilla_base_url,
        },
    }


@router.get("/__heartbeat__")
@router.head("/__heartbeat__")
def heartbeat(
    response: Response,
    bugzilla_service: BugzillaServiceDep,
    jira_service: JiraServiceDep,
):
    """Return status of backing services, as required by Dockerflow."""
    check_results = dockerflow_checks.run_checks(
        dockerflow_checks.get_checks().items(),
    )

    payload = {
        "status": dockerflow_checks.level_to_text(check_results.level),
        "checks": check_results.statuses,
        "details": check_results.details,
    }

    if check_results.level < dockerflow_checks.ERROR:
        response.status_code = 200
    else:
        response.status_code = 500

    return payload


@router.get("/__lbheartbeat__")
@router.head("/__lbheartbeat__")
def lbheartbeat():
    """Dockerflow API for lbheartbeat: HEAD"""
    return {"status": "OK"}


@router.get("/__version__")
def version(version_json: VersionDep):
    """Return version.json, as required by Dockerflow."""
    return version_json


header_scheme = APIKeyHeader(name="X-Api-Key", auto_error=False)
basicauth_scheme = HTTPBasic(auto_error=False)


def api_key_auth(
    settings: SettingsDep,
    api_key: Annotated[str, Depends(header_scheme)],
    basic_auth: Annotated[HTTPBasicCredentials, Depends(basicauth_scheme)],
):
    if not api_key and basic_auth:
        api_key = basic_auth.password
    if not api_key or not secrets.compare_digest(api_key, settings.jbi_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect API Key",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.post(
    "/bugzilla_webhook",
    dependencies=[Depends(api_key_auth)],
)
def bugzilla_webhook(
    request: Request,
    actions: ActionsDep,
    webhook_request: bugzilla.WebhookRequest = Body(..., embed=False),
):
    """API endpoint that Bugzilla Webhook Events request"""
    try:
        result = execute_action(webhook_request, actions)
        return result
    except IgnoreInvalidRequestError as exception:
        return {"error": str(exception)}


@router.get(
    "/whiteboard_tags/",
    dependencies=[Depends(api_key_auth)],
)
def get_whiteboard_tags(
    actions: ActionsDep,
    whiteboard_tag: Optional[str] = None,
):
    """API for viewing whiteboard_tags and associated data"""
    if existing := actions.get(whiteboard_tag):
        return {whiteboard_tag: existing}
    return actions.by_tag


@router.get(
    "/bugzilla_webhooks/",
    dependencies=[Depends(api_key_auth)],
)
def get_bugzilla_webhooks(bugzilla_service: BugzillaServiceDep):
    """API for viewing webhooks details"""
    return bugzilla_service.list_webhooks()


@router.get(
    "/jira_projects/",
    dependencies=[Depends(api_key_auth)],
)
def get_jira_projects(jira_service: JiraServiceDep):
    """API for viewing projects that are currently accessible by API"""
    return jira_service.fetch_visible_projects()


SRC_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=SRC_DIR / "templates")


@router.get(
    "/powered_by_jbi/",
    dependencies=[Depends(api_key_auth)],
    response_class=HTMLResponse,
)
def powered_by_jbi(
    request: Request,
    actions: ActionsDep,
    enabled: Optional[bool] = None,
):
    """API for `Powered By` endpoint"""
    context = {
        "request": request,
        "title": "Powered by JBI",
        "actions": jsonable_encoder(actions),
        "enable_query": enabled,
    }
    return templates.TemplateResponse("powered_by_template.html", context)
