"""Configuration loader with JSON/YAML dual-mode support."""

import json
import logging
import os
import socket
from dataclasses import dataclass, field
from typing import List, Optional

import yaml

logger = logging.getLogger("shack.config")


def _get_supervisor_token() -> Optional[str]:
    """Get the Supervisor API token from environment."""
    return os.environ.get("SUPERVISOR_TOKEN")


def _query_supervisor_api(path: str) -> Optional[dict]:
    """Query the Home Assistant Supervisor API.

    Returns parsed JSON response or None on failure.
    """
    token = _get_supervisor_token()
    if not token:
        logger.debug("SUPERVISOR_TOKEN not set, not running as add-on")
        return None

    try:
        import urllib.request
        import urllib.error

        url = f"http://supervisor{path}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    except Exception as e:
        logger.debug("Supervisor API query failed for %s: %s", path, e)
        return None


def get_addon_slug() -> Optional[str]:
    """Get the add-on slug from the Supervisor API.

    Returns the slug (e.g., 'df3bd192_shack') or None if not running as add-on.
    """
    info = _query_supervisor_api("/addons/self/info")
    if info and "data" in info:
        slug = info["data"].get("slug")
        if slug:
            logger.debug("Add-on slug: %s", slug)
            return slug
    logger.debug("Could not determine add-on slug")
    return None


@dataclass
class Config:
    """Add-on configuration."""

    # MQTT settings
    mqtt_host: str = "core-mosquitto"
    mqtt_port: int = 1883
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None

    # Logging
    log_level: str = "INFO"
    integration_log_levels: dict = field(default_factory=dict)

    @classmethod
    def load(cls, config_path: str, dev_config_path: str) -> "Config":
        """Load configuration from JSON (production) or YAML (dev)."""
        if os.path.exists(config_path):
            # Production: load from /data/options.json
            with open(config_path, "r") as f:
                data = json.load(f)
            return cls.from_dict(data)
        elif os.path.exists(dev_config_path):
            # Development: load from local YAML
            with open(dev_config_path, "r") as f:
                data = yaml.safe_load(f)
            return cls.from_dict(data)
        else:
            # Create dev config with defaults
            config = cls()
            config._save_dev_config(dev_config_path)
            return config

    @classmethod
    def _load_mqtt_services(cls) -> Optional[dict]:
        """Load MQTT credentials from HA Supervisor API or services file.

        Returns dict with host, port, username, password or None if not available.
        """
        # First try the Supervisor API (more reliable)
        response = _query_supervisor_api("/services/mqtt")
        if response and "data" in response:
            data = response["data"]
            if data and data.get("username") and data.get("password"):
                logger.info("Using MQTT credentials from Supervisor API")
                return {
                    "host": data.get("host", "core-mosquitto"),
                    "port": data.get("port", 1883),
                    "username": data.get("username"),
                    "password": data.get("password"),
                }

        # Fallback to local services file
        services_path = "/data/services/mqtt/config.json"
        if not os.path.exists(services_path):
            logger.debug("MQTT services file not found at %s", services_path)
            return None

        try:
            with open(services_path, "r") as f:
                data = json.load(f)
            if data and data.get("username") and data.get("password"):
                logger.info("Using MQTT credentials from services file")
                return data
            else:
                logger.warning("MQTT services file exists but missing credentials")
                return None
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to read MQTT services file: %s", e)
            return None

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Create Config from dictionary."""
        integration_log_levels = data.get("integration_log_levels", {})
        # Convert list format [{name: str, level: str}, ...] to dict format
        if isinstance(integration_log_levels, list):
            integration_log_levels = {
                item["name"]: item["level"]
                for item in integration_log_levels
                if "name" in item
            }

        # Check for built-in MQTT service credentials first
        mqtt_services = cls._load_mqtt_services()

        if mqtt_services:
            mqtt_host = mqtt_services.get("host", "core-mosquitto")
            mqtt_port = mqtt_services.get("port", 1883)
            mqtt_username = mqtt_services.get("username")
            mqtt_password = mqtt_services.get("password")
        else:
            mqtt_host = data.get("mqtt_host", "core-mosquitto")
            mqtt_port = data.get("mqtt_port", 1883)
            mqtt_username = data.get("mqtt_username") or None
            mqtt_password = data.get("mqtt_password") or None

        if not mqtt_username or not mqtt_password:
            logger.warning(
                "MQTT credentials not configured - connection may fail if "
                "broker requires authentication"
            )

        return cls(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=mqtt_username,
            mqtt_password=mqtt_password,
            log_level=data.get("log_level", "INFO"),
            integration_log_levels=integration_log_levels,
        )

    def _save_dev_config(self, path: str):
        """Save default config for development."""
        with open(path, "w") as f:
            yaml.dump(
                {
                    "mqtt_host": self.mqtt_host,
                    "mqtt_port": self.mqtt_port,
                    "mqtt_username": self.mqtt_username or "",
                    "mqtt_password": self.mqtt_password or "",
                    "log_level": self.log_level,
                    "integration_log_levels": self.integration_log_levels,
                },
                f,
                default_flow_style=False,
            )
