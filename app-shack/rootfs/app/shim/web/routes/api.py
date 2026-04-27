"""JSON API routes.

Provides REST API endpoints for programmatic access to integration,
entity, and shim status data.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

_LOGGER = logging.getLogger(__name__)


def register_routes(app: FastAPI, shim_manager, template_dir: Path) -> None:
    """Register JSON API routes."""

    @app.get("/api/integrations", response_class=JSONResponse)
    async def api_integrations():
        """API endpoint for integration list."""
        installed = (
            shim_manager.get_integration_manager().get_all_integrations()
        )
        available = shim_manager.get_integration_manager().get_available_integrations()

        return {
            "installed": [
                {
                    "domain": i.domain,
                    "name": i.name,
                    "version": i.version,
                    "enabled": i.enabled,
                    "update_available": i.update_available,
                    "latest_version": i.latest_version,
                }
                for i in installed
            ],
            "available": [
                {
                    "full_name": a.get("full_name"),
                    "domain": a["domain"],
                    "name": a["name"],
                    "description": a.get("description", ""),
                }
                for a in available
            ],
        }

    @app.get("/api/entities", response_class=JSONResponse)
    async def api_entities():
        """API endpoint for all entities."""
        entities = []
        for (
            domain
        ) in shim_manager.get_integration_loader().get_loaded_integrations():
            for entity in shim_manager.get_integration_loader().get_entities(
                integration_domain=domain
            ):
                entities.append(
                    {
                        "entity_id": entity.entity_id,
                        "name": entity.name,
                        "state": entity.state,
                        "available": entity.available,
                    }
                )

        return {"entities": entities}

    @app.get("/api/status", response_class=JSONResponse)
    async def api_status():
        """API endpoint for shim status."""
        mqtt_bridge = shim_manager.get_mqtt_bridge()
        mqtt_status = (
            mqtt_bridge.connection_status
            if mqtt_bridge
            else {"connected": False, "error": "MQTT bridge not available"}
        )

        return {
            "running": True,
            "loaded_integrations": shim_manager.get_integration_loader().get_loaded_integrations(),
            "total_entities": len(
                shim_manager.get_integration_loader().get_entities()
            ),
            "mqtt": mqtt_status,
        }

    @app.get("/api/mqtt-status", response_class=JSONResponse)
    async def api_mqtt_status():
        """API endpoint for MQTT connection status."""
        mqtt_bridge = shim_manager.get_mqtt_bridge()
        if mqtt_bridge:
            return mqtt_bridge.connection_status
        return {"connected": False, "error": "MQTT bridge not available"}

    @app.get("/api/custom-repos", response_class=JSONResponse)
    async def api_custom_repos():
        """API endpoint for custom repositories."""
        repos = (
            shim_manager.get_integration_manager().get_custom_repositories()
        )
        return {"repositories": repos}

    @app.get("/api/unsupported-repos", response_class=JSONResponse)
    async def api_unsupported_repos():
        """API endpoint for listing unsupported repositories."""
        repos = shim_manager.get_integration_manager().get_unsupported_repos()
        return {"repositories": repos}

    @app.get("/api/verified-repos", response_class=JSONResponse)
    async def api_verified_repos():
        """API endpoint for listing verified repositories."""
        repos = shim_manager.get_integration_manager().get_verified_repos()
        return {"repositories": repos}
