import uvicorn

from src.app.environment import get_settings
from src.app.log import CONFIG as LOGGING_CONFIG

settings = get_settings()


if __name__ == "__main__":
    server = uvicorn.Server(
        uvicorn.Config(
            "src.app.main:app",
            host=settings.host,
            port=settings.port,
            reload=settings.app_reload,
            log_config=LOGGING_CONFIG,
        )
    )
    server.run()
