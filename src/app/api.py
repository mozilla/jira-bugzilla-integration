import logging
import logging.config
import sys
import time
from datetime import datetime

import uvicorn  # type: ignore
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from src.app.environment import get_settings
from src.app.monitor import api_router as monitor_router
from src.jbi.router import api_router as jbi_router

settings = get_settings()

logging_config = {
    "version": 1,
    "formatters": {
        "json": {
            "()": "dockerflow.logging.JsonLogFormatter",
            "logger_name": "jbi",
        },
    },
    "handlers": {
        "console": {
            "level": settings.log_level.upper(),
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": sys.stdout,
        }
    },
    "loggers": {
        "request.summary": {"handlers": ["console"], "level": "INFO"},
    },
}

logging.config.dictConfig(logging_config)

app = FastAPI(
    title="Jira Bugzilla Integration (JBI)",
    description="JBI v2 Platform",
    version="0.1.0",
)

app.include_router(monitor_router)
app.include_router(jbi_router)

summary_logger = logging.getLogger("request.summary")


@app.get("/", include_in_schema=False)
def root(request: Request):
    """GET via root redirects to /docs.
    - Args:
    - Returns:
        - **redirect**: Redirects call to ./docs
    """
    return RedirectResponse(url="./docs")


@app.middleware("http")
async def request_summary(request: Request, call_next):
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
        "app:app", host="0.0.0.0", port=settings.port, reload=settings.app_reload
    )
