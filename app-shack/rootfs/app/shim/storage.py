"""Storage layer for Home Assistant Shim.

Manages JSON file storage for config entries, devices, and entity registry.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime

from .logging import get_logger

_LOGGER = get_logger(__name__)


class Storage:
    """Manages persistent storage for shim data."""

    def __init__(self, shim_dir: Path):
        self._shim_dir = Path(shim_dir)
        self._shim_dir.mkdir(parents=True, exist_ok=True)

        # Storage files
        self._entries_file = self._shim_dir / "entries.json"
        self._devices_file = self._shim_dir / "devices.json"
        self._entities_file = self._shim_dir / "entities.json"
        self._integrations_file = self._shim_dir / "integrations.json"
        self._custom_repos_file = self._shim_dir / "custom_repos.json"

        # Static unsupported repos file (read-only)
        # Located at /app/metadata/unsupported_repos.json in the container
        self._unsupported_repos_file = (
            Path(__file__).parent.parent / "metadata" / "unsupported_repos.json"
        )

        _LOGGER.debug(f"Storage initialized at {self._shim_dir}")

    def _load_json(self, filepath: Path) -> dict:
        """Load JSON from file."""
        if not filepath.exists():
            return {}

        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            _LOGGER.error(f"Error loading {filepath}: {e}")
            # Backup corrupted file
            backup_path = filepath.with_suffix(".json.backup")
            filepath.rename(backup_path)
            _LOGGER.warning(f"Corrupted file backed up to {backup_path}")
            return {}
        except Exception as e:
            _LOGGER.error(f"Error loading {filepath}: {e}")
            return {}

    def _save_json(self, filepath: Path, data: dict) -> None:
        """Save JSON to file atomically."""
        try:
            # Write to temp file first
            temp_path = filepath.with_suffix(".json.tmp")
            with open(temp_path, "w") as f:
                json.dump(data, f, indent=2, default=str)

            # Atomic rename
            temp_path.rename(filepath)
        except Exception as e:
            _LOGGER.error(f"Error saving {filepath}: {e}")
            raise

    # Config Entries
    def load_entries(self) -> Dict[str, list]:
        """Load config entries from storage."""
        return self._load_json(self._entries_file)

    def save_entries(self, entries: Dict[str, list]) -> None:
        """Save config entries to storage."""
        self._save_json(self._entries_file, entries)
        _LOGGER.debug(f"Saved {sum(len(v) for v in entries.values())} config entries")

    # Device Registry
    def load_devices(self) -> Dict[str, dict]:
        """Load devices from storage."""
        return self._load_json(self._devices_file)

    def save_devices(self, devices: Dict[str, dict]) -> None:
        """Save devices to storage."""
        self._save_json(self._devices_file, devices)
        _LOGGER.debug(f"Saved {len(devices)} devices")

    def add_device(self, device_id: str, device_info: dict) -> None:
        """Add or update a device."""
        devices = self.load_devices()
        devices[device_id] = {
            **device_info,
            "id": device_id,
            "modified_at": datetime.now().isoformat(),
        }
        self.save_devices(devices)

    def remove_device(self, device_id: str) -> None:
        """Remove a device."""
        devices = self.load_devices()
        if device_id in devices:
            del devices[device_id]
            self.save_devices(devices)

    # Entity Registry
    def load_entities(self) -> Dict[str, dict]:
        """Load entity registry from storage."""
        return self._load_json(self._entities_file)

    def save_entities(self, entities: Dict[str, dict]) -> None:
        """Save entity registry to storage."""
        self._save_json(self._entities_file, entities)
        _LOGGER.debug(f"Saved {len(entities)} entities")

    def register_entity(
        self,
        entity_id: str,
        unique_id: str,
        platform: str,
        device_id: Optional[str] = None,
        area_id: Optional[str] = None,
    ) -> None:
        """Register an entity in the registry."""
        entities = self.load_entities()
        entities[entity_id] = {
            "entity_id": entity_id,
            "unique_id": unique_id,
            "platform": platform,
            "device_id": device_id,
            "area_id": area_id,
            "registered_at": datetime.now().isoformat(),
        }
        self.save_entities(entities)
        _LOGGER.debug(f"Registered entity {entity_id}")

    def unregister_entity(self, entity_id: str) -> None:
        """Unregister an entity."""
        entities = self.load_entities()
        if entity_id in entities:
            del entities[entity_id]
            self.save_entities(entities)
            _LOGGER.debug(f"Unregistered entity {entity_id}")

    def get_entity_by_unique_id(self, unique_id: str) -> Optional[dict]:
        """Find entity by unique_id."""
        entities = self.load_entities()
        for entity in entities.values():
            if entity.get("unique_id") == unique_id:
                return entity
        return None

    # Integration Registry
    def load_integrations(self) -> Dict[str, dict]:
        """Load integration registry from storage."""
        return self._load_json(self._integrations_file)

    def save_integrations(self, integrations: Dict[str, dict]) -> None:
        """Save integration registry to storage."""
        self._save_json(self._integrations_file, integrations)

    def register_integration(
        self,
        domain: str,
        version: str,
        source: str = "hacs_default",
        enabled: bool = False,
    ) -> None:
        """Register an integration."""
        integrations = self.load_integrations()
        integrations[domain] = {
            "domain": domain,
            "version": version,
            "source": source,
            "enabled": enabled,
            "installed_at": datetime.now().isoformat(),
            "last_checked": None,
            "latest_version": version,
        }
        self.save_integrations(integrations)
        _LOGGER.info(f"Registered integration {domain}@{version}")

    def update_integration(self, domain: str, **kwargs) -> None:
        """Update integration metadata."""
        integrations = self.load_integrations()
        if domain in integrations:
            integrations[domain].update(kwargs)
            self.save_integrations(integrations)

    def remove_integration(self, domain: str) -> None:
        """Remove an integration from registry."""
        integrations = self.load_integrations()
        if domain in integrations:
            del integrations[domain]
            self.save_integrations(integrations)

    def is_integration_enabled(self, domain: str) -> bool:
        """Check if integration is enabled."""
        integrations = self.load_integrations()
        return integrations.get(domain, {}).get("enabled", False)

    def get_enabled_integrations(self) -> list:
        """Get list of enabled integration domains."""
        integrations = self.load_integrations()
        return [
            domain
            for domain, info in integrations.items()
            if info.get("enabled", False)
        ]

    # Integration file storage
    def get_integration_dir(self, domain: str) -> Path:
        """Get path to integration directory."""
        custom_components = self._shim_dir / "custom_components"
        custom_components.mkdir(exist_ok=True)
        return custom_components / domain

    def integration_exists(self, domain: str) -> bool:
        """Check if integration files exist."""
        integration_dir = self.get_integration_dir(domain)
        manifest = integration_dir / "manifest.json"
        return manifest.exists()

    # Custom Repositories
    def load_custom_repos(self) -> Dict[str, dict]:
        """Load custom repositories from storage."""
        return self._load_json(self._custom_repos_file)

    def save_custom_repos(self, repos: Dict[str, dict]) -> None:
        """Save custom repositories to storage."""
        self._save_json(self._custom_repos_file, repos)
        _LOGGER.debug(f"Saved {len(repos)} custom repositories")

    # Unsupported Repositories (read-only static file)
    def load_unsupported_repos(self) -> Dict[str, dict]:
        """Load unsupported repositories from static file.

        This is a read-only static file that lists repositories known to be
        incompatible with the shim. Returns empty dict if file doesn't exist.
        """
        return self._load_json(self._unsupported_repos_file)

    def is_unsupported_repo(self, full_name: str) -> Optional[dict]:
        """Check if a repository is unsupported. Returns the entry if unsupported."""
        repos = self.load_unsupported_repos()
        return repos.get(full_name)

    def is_unsupported_repo_by_url(self, repo_url: str) -> Optional[dict]:
        """Check if a repository URL is unsupported. Returns the entry if unsupported."""
        repos = self.load_unsupported_repos()
        for full_name, entry in repos.items():
            if full_name in repo_url or entry.get("repository_url") == repo_url:
                return entry
        return None

    # Clear all data (for testing/reset)
    def clear_all(self) -> None:
        """Clear all stored data."""
        for filepath in [
            self._entries_file,
            self._devices_file,
            self._entities_file,
            self._integrations_file,
            self._custom_repos_file,
        ]:
            if filepath.exists():
                filepath.unlink()
        _LOGGER.warning("All storage cleared")
