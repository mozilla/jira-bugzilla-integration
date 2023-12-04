"""
Core FastAPI app (setup, middleware)
"""
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from jbi.configuration import get_actions
from jbi.environment import Settings, get_settings, get_version
from jbi.models import Actions, BugzillaWebhookRequest
from jbi.runner import IgnoreInvalidRequestError, execute_action
from jbi.services import bugzilla, jira

SettingsDep = Annotated[Settings, Depends(get_settings)]
ActionsDep = Annotated[Actions, Depends(get_actions)]
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
    actions: ActionsDep,
    bugzilla_service: BugzillaServiceDep,
    jira_service: JiraServiceDep,
):
    """Return status of backing services, as required by Dockerflow."""
    health_map = {
        "bugzilla": bugzilla_service.check_health(),
        "jira": jira_service.check_health(actions),
    }
    health_checks: list = []
    for health in health_map.values():
        health_checks.extend(health.values())
    if not all(health_checks):
        response.status_code = 503
    return health_map


@router.get("/__lbheartbeat__")
@router.head("/__lbheartbeat__")
def lbheartbeat():
    """Dockerflow API for lbheartbeat: HEAD"""
    return {"status": "OK"}


@router.get("/__version__")
def version(version_json: VersionDep):
    """Return version.json, as required by Dockerflow."""
    return version_json


@router.post("/bugzilla_webhook")
def bugzilla_webhook(
    request: Request,
    actions: ActionsDep,
    webhook_request: BugzillaWebhookRequest = Body(..., embed=False),
):
    """API endpoint that Bugzilla Webhook Events request"""
    try:
        result = execute_action(webhook_request, actions)
        return result
    except IgnoreInvalidRequestError as exception:
        return {"error": str(exception)}


@router.get("/whiteboard_tags/")
def get_whiteboard_tags(
    actions: ActionsDep,
    whiteboard_tag: Optional[str] = None,
):
    """API for viewing whiteboard_tags and associated data"""
    if existing := actions.get(whiteboard_tag):
        return {whiteboard_tag: existing}
    return actions.by_tag


@router.get("/bugzilla_webhooks/")
def get_bugzilla_webhooks(bugzilla_service: BugzillaServiceDep):
    """API for viewing webhooks details"""
    return bugzilla_service.list_webhooks()


@router.get("/jira_projects/")
def get_jira_projects(jira_service: JiraServiceDep):
    """API for viewing projects that are currently accessible by API"""
    visible_projects: list[dict] = jira_service.fetch_visible_projects()
    return [project["key"] for project in visible_projects]


SRC_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=SRC_DIR / "templates")


@router.get("/powered_by_jbi/", response_class=HTMLResponse)
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
