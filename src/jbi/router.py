"""
Router dedicated to Jira Bugzilla Integration APIs
"""
import importlib
import logging
from types import ModuleType
from typing import Dict, List, Optional, Tuple

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


def extract_current_action(  # pylint: disable=inconsistent-return-statements
    bug_obj: BugzillaBug, action_map
) -> Optional[Tuple(str, Dict)]:
    """Find first matching action from bug's whiteboard list"""
    potential_configuration_tags: List[
        str
    ] = bug_obj.get_potential_whiteboard_config_list()

    for tag in potential_configuration_tags:
        if action := action_map.get(tag.lower()):
            return tag.lower(), action


def execute_action(request: BugzillaWebhookRequest, action_map, settings):
    """Execute action"""
    log_context = {
        "bug": {
            "id": request.bug.id if request.bug else None,
        },
        "request": request.json(),
    }
    try:
        logger.debug(
            "Handling incoming request", extra={"operation": "handle", **log_context}
        )
        if not request.bug:
            raise IgnoreInvalidRequestError("no bug data received")

        bug_obj = getbug_as_bugzilla_object(request=request)
        log_context["bug"] = bug_obj.json()

        action_item = extract_current_action(bug_obj, action_map)  # type: ignore
        if not action_item:
            raise IgnoreInvalidRequestError(
                "whiteboard tag not found in configured actions"
            )

        action_name, current_action = action_item
        log_context["action"] = current_action

        if bug_obj.is_private and not current_action["allow_private"]:
            raise IgnoreInvalidRequestError(
                f"private bugs are not valid for action {action_name!r}"
            )

        logger.info(
            "Execute action %r for Bug %s",
            action_name,
            bug_obj.id,
            extra={"operation": "execute", **log_context},
        )
        action_module: ModuleType = importlib.import_module(current_action["action"])
        callable_action = action_module.init(  # type: ignore
            **current_action["parameters"]
        )
        content = callable_action(payload=request)
        logger.info(
            "Action %r executed successfully for Bug %s",
            action_name,
            bug_obj.id,
            extra={"operation": "success", **log_context},
        )
        return JSONResponse(content=content, status_code=200)
    except IgnoreInvalidRequestError as exception:
        logger.debug(
            "Ignore incoming request: %s",
            exception,
            extra={"operation": "ignore", **log_context},
        )
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
