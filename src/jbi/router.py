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
from src.jbi.bugzilla import BugzillaBug, BugzillaWebhookRequest
from src.jbi.errors import IgnoreInvalidRequestError
from src.jbi.services import getbug_as_bugzilla_object

templates = Jinja2Templates(directory="src/templates")

api_router = APIRouter(tags=["JBI"])

logger = logging.getLogger(__name__)
invalid_logger = logging.getLogger("ignored-requests")


def extract_current_action(  # pylint: disable=inconsistent-return-statements
    bug_obj: BugzillaBug, action_map
):
    """Find first matching action from bug's whiteboard list"""
    potential_configuration_tags: List[
        str
    ] = bug_obj.get_potential_whiteboard_config_list()

    for tag in potential_configuration_tags:
        if action := action_map.get(tag.lower()):
            action["name"] = tag.lower()
            return action


def execute_action(request: BugzillaWebhookRequest, action_map, settings):
    """Execute action"""
    try:
        logger.info("request: %s", request.json())
        if not request.bug:
            raise IgnoreInvalidRequestError("no bug data received")

        bug_obj = getbug_as_bugzilla_object(request=request)
        current_action = extract_current_action(bug_obj, action_map)  # type: ignore

        if not current_action:
            raise IgnoreInvalidRequestError(
                "whiteboard tag not found in configured actions"
            )

        if bug_obj.is_private and not current_action["allow_private"]:
            raise IgnoreInvalidRequestError(
                "private bugs are not valid for this action"
            )

        logger.info("\nrequest: %s, \naction: %s", request.json(), current_action)
        action_module: ModuleType = importlib.import_module(current_action["action"])
        callable_action = action_module.init(  # type: ignore
            **current_action["parameters"]
        )
        content = callable_action(payload=request)
        logger.info("request: %s, content: %s", request.json(), content)
        return JSONResponse(content=content, status_code=200)
    except IgnoreInvalidRequestError as exception:
        invalid_logger.debug("ignore-invalid-request: %s", exception)
        return JSONResponse(content={"error": str(exception)}, status_code=200)


@api_router.post("/bugzilla_webhook")
def bugzilla_webhook(
    request: BugzillaWebhookRequest = Body(..., embed=False),
    settings: environment.Settings = Depends(environment.get_settings),
    action_map: Dict = Depends(configuration.get_actions_dict),
):
    """API endpoint that Bugzilla Webhook Events request"""
    return execute_action(request, action_map, settings)


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
