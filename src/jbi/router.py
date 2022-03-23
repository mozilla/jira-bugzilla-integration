"""
Router dedicated to Jira Bugzilla Integration APIs
"""
import importlib
import logging
from types import ModuleType
from typing import Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.app import environment
from src.jbi import configuration
from src.jbi.bugzilla_objects import BugzillaBug, BugzillaWebhookRequest
from src.jbi.service import get_bugzilla

templates = Jinja2Templates(directory="src/templates")

api_router = APIRouter(tags=["JBI"])

jbi_logger = logging.getLogger("src.jbi.router")


class ValidationError(Exception):
    """Error throw when requests are invalid and ignored"""


def extract_current_action(bug_obj: BugzillaBug, action_map, settings):
    """Find first matching action from bug's whiteboard list"""
    potential_configuration_tags: List[
        str
    ] = bug_obj.get_potential_whiteboard_config_list()
    for tag in potential_configuration_tags:
        value = action_map.get(tag.lower())
        if value:
            return value
    return {}


def execute_request(request: BugzillaWebhookRequest, action_map, settings):
    """Execute action"""
    try:
        if not request.bug:
            raise ValidationError("no bug data received")

        is_private_bug = request.bug.is_private
        if is_private_bug:
            raise ValidationError("private bugs are not valid")

        current_bug_info = get_bugzilla().getbug(request.bug.id)
        bug_obj = BugzillaBug.parse_obj(current_bug_info.__dict__)
        current_action = extract_current_action(bug_obj, action_map, settings)
        if not current_action:
            raise ValidationError("bug does not have matching config")

        action_module: ModuleType = importlib.import_module(current_action["action"])
        if not action_module:
            raise ValidationError("action not found")

        callable_action = action_module.init(  # type: ignore
            **current_action["parameters"]
        )
        return callable_action()
    except ValidationError as exception:
        return JSONResponse(content={"error": exception}, status_code=201)


@api_router.post("/bugzilla_webhook")
def bugzilla_webhook(
    request: BugzillaWebhookRequest = Body(..., embed=False),
    settings: environment.Settings = Depends(environment.get_settings),
    action_map: Dict = Depends(configuration.get_actions_dict),
):
    """API endpoint that Bugzilla Webhook Events request"""
    jbi_logger.info("(webhook-request): %s", request.json())
    return execute_request(request, action_map, settings)


@api_router.get("/whiteboard_tags/")
def get_whiteboard_tag(
    whiteboard_tag: Optional[str] = None,
):
    """API for viewing whiteboard_tags and associated data"""
    actions = configuration.get_actions_dict()
    if whiteboard_tag:
        wb_val = actions.get(whiteboard_tag)
        if wb_val:
            actions = wb_val
    return actions


@api_router.get("/actions/")
def get_actions_by_type(action_type: Optional[str] = None):
    """API for viewing actions within the config; `action_type` matched on end of action identifier"""
    actions = configuration.get_actions_dict()
    if action_type:
        return [
            a["action"] for a in actions.values() if a["action"].endswith(action_type)
        ]
    return [a["action"] for a in actions.values()]


@api_router.get("/powered_by_jbi", response_class=HTMLResponse)
def powered_by_jbi(request: Request, enabled: Optional[bool] = None):
    """API for `Powered By` endpoint"""
    actions = configuration.get_actions_dict()
    context = {
        "request": request,
        "title": "Powered by JBI",
        "num_configs": len(actions),
        "data": actions,
        "enable_query": enabled,
    }
    return templates.TemplateResponse("powered_by_template.html", context)
