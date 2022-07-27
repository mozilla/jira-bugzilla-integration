import uvicorn

from src.app.environment import get_settings

settings = get_settings()


if __name__ == "__main__":
    uvicorn.run(
        "src.app.api:app",
        host=settings.host,
        port=settings.port,
        reload=settings.app_reload,
        log_level=settings.log_level,
    )
