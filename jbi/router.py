"""
Core FastAPI app (setup, middleware)
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from jbi.configuration import get_actions
from jbi.environment import Settings, get_settings, get_version
from jbi.models import Actions, BugzillaWebhookRequest
from jbi.runner import IgnoreInvalidRequestError, execute_action
from jbi.services import jbi_service_health_map, jira_visible_projects

router = APIRouter()


@router.get("/", include_in_schema=False)
def root(request: Request, settings: Settings = Depends(get_settings)):
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
def heartbeat(response: Response, actions: Actions = Depends(get_actions)):
    """Return status of backing services, as required by Dockerflow."""
    health_map = jbi_service_health_map(actions)
    health_checks = []
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
def version(version_json=Depends(get_version)):
    """Return version.json, as required by Dockerflow."""
    return version_json


@router.post("/bugzilla_webhook")
def bugzilla_webhook(
    request: BugzillaWebhookRequest = Body(..., embed=False),
    actions: Actions = Depends(get_actions),
    settings: Settings = Depends(get_settings),
):
    """API endpoint that Bugzilla Webhook Events request"""
    try:
        result = execute_action(request, actions, settings)
        return result
    except IgnoreInvalidRequestError as exception:
        return {"error": str(exception)}


@router.get("/whiteboard_tags/")
def get_whiteboard_tags(
    whiteboard_tag: Optional[str] = None,
    actions: Actions = Depends(get_actions),
):
    """API for viewing whiteboard_tags and associated data"""
    if existing := actions.get(whiteboard_tag):
        return {whiteboard_tag: existing}
    return actions.by_tag


@router.get("/jira_projects/")
def get_jira_projects():
    """API for viewing projects that are currently accessible by API"""
    visible_projects: list[dict] = jira_visible_projects()
    return [project["key"] for project in visible_projects]


SRC_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=SRC_DIR / "templates")


@router.get("/powered_by_jbi/", response_class=HTMLResponse)
def powered_by_jbi(
    request: Request,
    enabled: Optional[bool] = None,
    actions: Actions = Depends(get_actions),
):
    """API for `Powered By` endpoint"""
    context = {
        "request": request,
        "title": "Powered by JBI",
        "actions": jsonable_encoder(actions),
        "enable_query": enabled,
    }
    return templates.TemplateResponse("powered_by_template.html", context)
