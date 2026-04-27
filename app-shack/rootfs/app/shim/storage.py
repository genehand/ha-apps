"""Storage layer for Home Assistant Shim.

Manages JSON file storage for config entries, devices, and entity registry.
"""

import json
from pathlib import Path
from typing import Dict, Optional
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
        self._entities_file = self._shim_dir / "entities.json"
        self._integrations_file = self._shim_dir / "integrations.json"
        self._custom_repos_file = self._shim_dir / "custom_repos.json"
        self._entity_states_file = self._shim_dir / "entity_states.json"

        # Static repository status file (read-only)
        # Located at /app/metadata/repository_status.json in the container
        self._repository_status_file = (
            Path(__file__).parent.parent / "metadata" / "repository_status.json"
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

    # Entity State Storage (for RestoreEntity)
    def load_entity_states(self) -> Dict[str, dict]:
        """Load saved entity states from storage."""
        return self._load_json(self._entity_states_file)

    def save_entity_states(self, states: Dict[str, dict]) -> None:
        """Save entity states to storage."""
        self._save_json(self._entity_states_file, states)
        _LOGGER.debug(f"Saved {len(states)} entity states")

    def save_entity_state(self, entity_id: str, state: str, attributes: Optional[dict] = None, extra_data: Optional[dict] = None) -> None:
        """Save the state of a single entity.

        Args:
            entity_id: The entity ID (e.g., 'text.flightradar24_airport_track')
            state: The state value to save
            attributes: Optional attributes dict to save with the state
            extra_data: Optional extra restore data dict to save with the state
        """
        states = self.load_entity_states()
        entry = {
            "state": state,
            "attributes": attributes or {},
            "last_updated": datetime.now().isoformat(),
        }
        if extra_data is not None:
            entry["extra_data"] = extra_data
        states[entity_id] = entry
        self.save_entity_states(states)
        _LOGGER.debug(f"Saved state for {entity_id}: {state}")

    def load_entity_state(self, entity_id: str) -> Optional[dict]:
        """Load the saved state for a specific entity.

        Args:
            entity_id: The entity ID to look up

        Returns:
            Dict with 'state', 'attributes', and 'last_updated' keys, or None if not found
        """
        states = self.load_entity_states()
        return states.get(entity_id)

    def remove_entity_state(self, entity_id: str) -> None:
        """Remove a saved entity state."""
        states = self.load_entity_states()
        if entity_id in states:
            del states[entity_id]
            self.save_entity_states(states)
            _LOGGER.debug(f"Removed saved state for {entity_id}")

    # Integration Registry
    def load_integrations(self) -> Dict[str, dict]:
        """Load integration registry from storage."""
        return self._load_json(self._integrations_file)

    def save_integrations(self, integrations: Dict[str, dict]) -> None:
        """Save integration registry to storage."""
        self._save_json(self._integrations_file, integrations)

    def remove_integration(self, domain: str) -> None:
        """Remove an integration from registry."""
        integrations = self.load_integrations()
        if domain in integrations:
            del integrations[domain]
            self.save_integrations(integrations)

    def get_enabled_integrations(self) -> list:
        """Get list of enabled integration domains."""
        integrations = self.load_integrations()
        return [
            domain
            for domain, info in integrations.items()
            if info.get("enabled", False)
        ]

    # Custom Repositories
    def load_custom_repos(self) -> Dict[str, dict]:
        """Load custom repositories from storage."""
        return self._load_json(self._custom_repos_file)

    def save_custom_repos(self, repos: Dict[str, dict]) -> None:
        """Save custom repositories to storage."""
        self._save_json(self._custom_repos_file, repos)
        _LOGGER.debug(f"Saved {len(repos)} custom repositories")

    # Repository Status (read-only static file)
    def load_repository_status(self) -> Dict[str, dict]:
        """Load repository status from static file.

        This is a read-only static file that lists repositories known to be
        unsupported or verified compatible with the shim.
        Returns empty dict if file doesn't exist.
        """
        return self._load_json(self._repository_status_file)

    def load_unsupported_repos(self) -> Dict[str, dict]:
        """Load unsupported repositories from static file.

        Returns a dict keyed by full_name.
        """
        status = self.load_repository_status()
        return status.get("unsupported", {})

    def load_verified_repos(self) -> Dict[str, dict]:
        """Load verified repositories from static file.

        Returns a dict keyed by full_name.
        """
        status = self.load_repository_status()
        return status.get("verified", {})

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

    def is_verified_repo(self, full_name: str) -> Optional[dict]:
        """Check if a repository is verified. Returns the entry if verified."""
        repos = self.load_verified_repos()
        return repos.get(full_name)

    def is_verified_repo_by_url(self, repo_url: str) -> Optional[dict]:
        """Check if a repository URL is verified. Returns the entry if verified."""
        repos = self.load_verified_repos()
        for full_name, entry in repos.items():
            if full_name in repo_url or entry.get("repository_url") == repo_url:
                return entry
        return None


