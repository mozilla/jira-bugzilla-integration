"""
Core FastAPI app (setup, middleware)
"""
import logging
from pathlib import Path
from secrets import token_hex
from typing import Any

import sentry_sdk
from asgi_correlation_id import CorrelationIdMiddleware
from dockerflow.version import get_version
from dockerflow.fastapi import router as dockerflow_router
from dockerflow.fastapi.middleware import MozlogRequestSummaryLogger
from fastapi import FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from jbi.environment import get_settings
from jbi.log import CONFIG
from jbi.router import router

SRC_DIR = Path(__file__).parent
APP_DIR = Path(__file__).parents[1]

settings = get_settings()
version_info = get_version(APP_DIR)

logging.config.dictConfig(CONFIG)

logger = logging.getLogger(__name__)


def traces_sampler(sampling_context: dict[str, Any]) -> float:
    """Function to dynamically set Sentry sampling rates"""

    request_path = sampling_context.get("asgi_scope", {}).get("path")
    if request_path == "/__lbheartbeat__":
        # Drop all __lbheartbeat__ requests
        return 0
    return settings.sentry_traces_sample_rate


sentry_sdk.init(
    dsn=str(settings.sentry_dsn) if settings.sentry_dsn else None,
    traces_sampler=traces_sampler,
    release=version_info["version"],
)


app = FastAPI(
    title="Jira Bugzilla Integration (JBI)",
    description="Platform providing synchronization of Bugzilla bugs to Jira issues.",
    version=version_info["version"],
    debug=settings.app_debug,
)

app.state.APP_DIR = APP_DIR
app.include_router(router)
app.include_router(dockerflow_router)
app.add_middleware(MozlogRequestSummaryLogger)

app.mount("/static", StaticFiles(directory=SRC_DIR / "static"), name="static")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    """
    Override the default exception handler for validation
    errors in order to log some information about malformed
    requests.
    """
    logger.error(
        "invalid incoming request: %s",
        exc,
        extra={
            "errors": exc.errors(),
        },
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(exc.errors())},
    )


app.add_middleware(
    CorrelationIdMiddleware,
    header_name="X-Request-Id",
    generator=lambda: token_hex(16),
    validator=None,
)
