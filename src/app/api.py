"""
Core FastAPI app (setup, middleware)
"""
import logging
import time
from datetime import datetime

import sentry_sdk
import uvicorn  # type: ignore
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from src.app.environment import get_settings
from src.app.log import configure_logging
from src.app.monitor import api_router as monitor_router
from src.jbi.router import api_router as jbi_router

settings = get_settings()

configure_logging()

app = FastAPI(
    title="Jira Bugzilla Integration (JBI)",
    description="JBI v2 Platform",
    version="0.1.0",
)

app.include_router(monitor_router)
app.include_router(jbi_router)
app.mount("/static", StaticFiles(directory="src/static"), name="static")

sentry_sdk.init(  # pylint: disable=abstract-class-instantiated  # noqa: E0110
    dsn=settings.sentry_dsn
)
app.add_middleware(SentryAsgiMiddleware)


@app.get("/", include_in_schema=False)
def root(request: Request):
    """GET via root redirects to /docs."""
    return RedirectResponse(url="./docs")


@app.middleware("http")
async def request_summary(request: Request, call_next):
    """Middleware to log request info"""
    summary_logger = logging.getLogger("request.summary")
    previous_time = time.time()

    infos = {
        "agent": request.headers.get("User-Agent"),
        "path": request.url.path,
        "method": request.method,
        "lang": request.headers.get("Accept-Language"),
        "querystring": dict(request.query_params),
        "errno": 0,
    }

    response = await call_next(request)

    current = time.time()
    duration = int((current - previous_time) * 1000.0)
    isotimestamp = datetime.fromtimestamp(current).isoformat()
    infos = {"time": isotimestamp, "code": response.status_code, "t": duration, **infos}

    summary_logger.info("", extra=infos)

    return response


if __name__ == "__main__":
    uvicorn.run(
        "app:app", host=settings.host, port=settings.port, reload=settings.app_reload
    )
