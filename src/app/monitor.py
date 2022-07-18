"""
Router dedicated to Dockerflow APIs
"""
from fastapi import APIRouter, Response

from src.app import environment
from src.jbi.services import jbi_service_health_map

api_router = APIRouter(tags=["Monitor"])


@api_router.get("/__heartbeat__")
@api_router.head("/__heartbeat__")
def heartbeat(response: Response):
    """Return status of backing services, as required by Dockerflow."""
    health_map = jbi_service_health_map()
    if not all(health["up"] for health in health_map.values()):
        response.status_code = 503
    return health_map


@api_router.get("/__lbheartbeat__")
@api_router.head("/__lbheartbeat__")
def lbheartbeat():
    """Dockerflow API for lbheartbeat: HEAD"""
    return {"status": "OK"}


@api_router.get("/__version__")
def version():
    """Return version.json, as required by Dockerflow."""
    return environment.get_version()
