"""Web UI for Home Assistant Shim.

FastAPI + HTMX interface for managing integrations and config flows.
"""

import asyncio
from pathlib import Path

from fastapi import FastAPI

import colorlog

from ..logging import get_logger

_LOGGER = get_logger(__name__)


class WebUI:
    """Web UI for the HA shim."""

    def __init__(self, shim_manager, host: str = "0.0.0.0", port: int = 8080):
        self._shim_manager = shim_manager
        self._host = host
        self._port = port

        # Import version locally to avoid circular import
        from .. import __version__

        # Setup FastAPI
        self._app = FastAPI(title="HA Shim", version=__version__)

        # Setup templates - store directory path for use by route modules
        self._template_dir = Path(__file__).parent / "templates"

        # Register all routes from sub-modules
        self._register_routes()

    def _register_routes(self) -> None:
        """Register all routes from sub-modules."""
        from .routes.integrations import register_routes as reg_integrations
        from .routes.config_flows import register_routes as reg_config_flows
        from .routes.credentials import register_routes as reg_credentials
        from .routes.auth import register_routes as reg_auth
        from .routes.api import register_routes as reg_api
        from .routes.fragments import register_routes as reg_fragments

        reg_integrations(self._app, self._shim_manager, self._template_dir)
        reg_config_flows(self._app, self._shim_manager, self._template_dir)
        reg_credentials(self._app, self._shim_manager, self._template_dir)
        reg_auth(self._app, self._shim_manager, self._template_dir)
        reg_api(self._app, self._shim_manager, self._template_dir)
        reg_fragments(self._app, self._shim_manager, self._template_dir)

    def get_app(self) -> FastAPI:
        """Get the FastAPI application."""
        return self._app

    async def start(self) -> None:
        """Start the web server."""
        import logging
        import uvicorn

        # Custom formatter that cleans up uvicorn logger names for display
        class UvicornFormatter(colorlog.ColoredFormatter):
            def format(self, record):
                # Show uvicorn.error as just uvicorn for cleaner logs
                if record.name == "uvicorn.error":
                    record.name = "uvicorn"
                return super().format(record)

        # Custom logging config to match main app format
        log_format = (
            "%(asctime)s %(log_color)s%(levelname)s%(reset)s: "
            "%(name)s - %(message)s"
        )
        date_format = "%Y-%m-%d %H:%M:%S"
        log_colors = {
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "red,bg_white",
        }

        log_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": UvicornFormatter,
                    "format": log_format,
                    "datefmt": date_format,
                    "log_colors": log_colors,
                },
                "access": {
                    "()": UvicornFormatter,
                    "format": log_format,
                    "datefmt": date_format,
                    "log_colors": log_colors,
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                },
                "access": {
                    "formatter": "access",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                "uvicorn": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["access"],
                    "level": "INFO",
                    "propagate": False,
                },
            },
        }

        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="info",
            log_config=log_config,
            loop="asyncio",  # Use standard asyncio loop explicitly
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        except asyncio.CancelledError:
            # Graceful shutdown
            _LOGGER.debug("Web server shutting down...")
            await server.shutdown()
            raise
