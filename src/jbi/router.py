from fastapi import APIRouter, Depends, Request

from src.app import environment

api_router = APIRouter(tags=["jbi"])


def execute_request(request, settings):
    pass


@api_router.get("/bugzilla_webhook")
def bugzilla_webhook(
    request: Request,
    settings: environment.Settings = Depends(environment.get_settings),
):
    return execute_request(request, settings)
