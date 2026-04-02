"""Configuration loader with JSON/YAML dual-mode support."""

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

import yaml


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
        return cls(
            mqtt_host=data.get("mqtt_host", "core-mosquitto"),
            mqtt_port=data.get("mqtt_port", 1883),
            mqtt_username=data.get("mqtt_username") or None,
            mqtt_password=data.get("mqtt_password") or None,
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
