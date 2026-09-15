"""Shared RestoreEntity mixin for entity state persistence.

Provides the RestoreEntity class used across platform shims and the
homeassistant.helpers.restore_state stub.  Saves entity state to persistent
storage and restores it when the entity is re-added after a restart.
"""

from __future__ import annotations

from typing import Any, Optional

from .logging import get_logger

_LOGGER = get_logger(__name__)


class ExtraStoredData:
    """Base class for extra stored data."""

    def as_dict(self) -> dict:
        """Return a dict representation of the data."""
        raise NotImplementedError


class RestoredExtraData(ExtraStoredData):
    """Wraps extra stored data restored from storage."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def as_dict(self) -> dict:
        return self._data


class RestoreEntity:
    """Mixin class for restoring entity state after restart.

    Saves entity state to persistent storage and restores it when
    the entity is re-added to Home Assistant after a restart.

    Usage:
        class MySwitch(SwitchEntity, RestoreEntity):
            ...
    """

    async def async_get_last_state(self):
        """Return last state from storage.

        Returns a State-like object with a 'state' attribute, or None if
        no previous state is found.
        """
        entity_id = getattr(self, 'entity_id', None)
        hass = getattr(self, 'hass', None)

        if not hass or not entity_id:
            return None

        from .storage import Storage
        from .models import State

        shim_dir = getattr(hass, 'shim_dir', None)
        if not shim_dir:
            return None

        storage = Storage(shim_dir)
        saved = await storage.async_load_entity_state(entity_id)

        if saved is None:
            return None

        return State(
            entity_id=entity_id,
            state=saved.get("state", ""),
            attributes=saved.get("attributes", {}),
        )

    async def async_get_last_extra_data(self) -> ExtraStoredData | None:
        """Return last extra data from storage."""
        entity_id = getattr(self, 'entity_id', None)
        hass = getattr(self, 'hass', None)

        if not hass or not entity_id:
            return None

        from .storage import Storage

        shim_dir = getattr(hass, 'shim_dir', None)
        if not shim_dir:
            return None

        storage = Storage(shim_dir)
        saved = await storage.async_load_entity_state(entity_id)

        if saved is None or "extra_data" not in saved:
            return None

        return RestoredExtraData(saved["extra_data"])

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """Return extra state data for restore."""
        return None

    def _save_state_for_restore(self) -> None:
        """Save the current entity state to storage for later restoration.

        This should be called when the entity state changes to ensure
        the latest state is available after a restart.
        """
        entity_id = getattr(self, 'entity_id', None)
        hass = getattr(self, 'hass', None)

        if not hass or not entity_id:
            return

        from .storage import Storage

        shim_dir = getattr(hass, 'shim_dir', None)
        if not shim_dir:
            return

        storage = Storage(shim_dir)
        # Use the safe accessor: the state property can raise for malformed
        # integration payloads, and failing to persist a value must not abort
        # the state write.
        state_value = self._safe_state() if hasattr(self, "_safe_state") else None
        extra_data = None
        if hasattr(self, 'extra_restore_state_data'):
            extra = self.extra_restore_state_data
            if extra is not None:
                extra_data = extra.as_dict()
        # Don't overwrite a previously good value with 'unavailable'.
        if state_value is not None and str(state_value) != "unavailable":
            storage.save_entity_state(
                entity_id, str(state_value), extra_data=extra_data
            )
