"""
Conf file adapted from:
https://github.com/tiangolo/uvicorn-gunicorn-docker/blob/2daa3e3873c837d5781feb4ff6a40a89f791f81b/docker-images/gunicorn_conf.py
"""

import multiprocessing
from typing import Optional

from pydantic import BaseSettings, conint, root_validator, validator


class GunicornSettings(BaseSettings):
    keep_alive: int = 5
    timeout: int = 120
    graceful_timeout: int = 120
    log_level: str = "info"
    host: str = "0.0.0.0"
    port: str = "8000"
    bind: Optional[str]
    access_log: str = "-"
    error_log: str = "-"
    workers_per_core: float = 1.0
    worker_tmp_dir: str = "/dev/shm"
    max_workers: Optional[int]
    web_concurrency: Optional[conint(gt=0)]
    workers: Optional[int]

    @validator("bind")
    def set_bind(cls, bind, values):
        return bind if bind else f"{values['host']}:{values['port']}"

    @root_validator(skip_on_failure=True)
    def set_workers(cls, values):
        if values["workers"] is not None:
            return values
        elif values["web_concurrency"]:
            values["workers"] = values["web_concurrency"]
        else:
            cores = multiprocessing.cpu_count()
            default_workers = values["workers_per_core"] * cores
            workers = max(int(default_workers), 2)
            if values["max_workers"]:
                workers = min(workers, values["max_workers"])
            values["workers"] = workers
        return values


gunicorn_settings = GunicornSettings()

# Gunicorn config variables
# https://docs.gunicorn.org/en/stable/settings.html#settings
loglevel = gunicorn_settings.log_level
workers = gunicorn_settings.workers
bind = gunicorn_settings.bind
errorlog = gunicorn_settings.error_log
worker_tmp_dir = gunicorn_settings.worker_tmp_dir
accesslog = gunicorn_settings.access_log
graceful_timeout = gunicorn_settings.graceful_timeout
timeout = gunicorn_settings.timeout
keepalive = gunicorn_settings.keep_alive


# For debugging and testing
log_data = {
    "loglevel": loglevel,
    "workers": workers,
    "bind": bind,
    "graceful_timeout": graceful_timeout,
    "timeout": timeout,
    "keepalive": keepalive,
    "errorlog": errorlog,
    "accesslog": accesslog,
    # Additional, non-gunicorn variables
    "workers_per_core": gunicorn_settings.workers_per_core,
    "use_max_workers": gunicorn_settings.max_workers,
    "host": gunicorn_settings.host,
    "port": gunicorn_settings.port,
}
