"""HomeAssistant core class for the shim.

The main orchestrator that brings together all registries and mocks.
"""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .storage import Storage
from .logging import get_logger
from .models import State

_LOGGER = get_logger(__name__)


class HomeAssistant:
    """Mock Home Assistant core object."""

    def __init__(self, config_dir: Path):
        from .registries import StateMachine, ServiceRegistry, ConfigEntries
        from .mocks import MockConfig, MockEventBus

        self.config_dir = Path(config_dir)

        # Create config directory if it doesn't exist
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.config = MockConfig(self.config_dir)

        # Create shim directory (create parents too if needed)
        self.shim_dir = self.config_dir / "shim"
        self.shim_dir.mkdir(parents=True, exist_ok=True)

        # Initialize storage
        self._storage = Storage(self.shim_dir)

        # Core systems
        self.states = StateMachine(self)
        self.services = ServiceRegistry(self)
        self.config_entries = ConfigEntries(self, self._storage)

        # Data storage for integrations
        self.data: Dict[str, Any] = {}

        # Event bus
        self._event_listeners: Dict[str, List[Callable]] = {}

        # Bus
        self.bus = MockEventBus(self)

        # Store the event loop at initialization time
        # This is needed for integrations that use run_coroutine_threadsafe from other threads
        self._loop = asyncio.get_event_loop()

        _LOGGER.debug("HomeAssistant shim initialized")

    @property
    def loop(self):
        """Return the event loop.

        This is used by some integrations that use asyncio.run_coroutine_threadsafe().
        Returns the loop that was active when HomeAssistant was created.
        """
        return self._loop

    async def async_add_executor_job(self, target: Callable, *args) -> Any:
        """Run function in executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, target, *args)

    async def async_add_import_executor_job(self, target: Callable, *args) -> Any:
        """Run import-related function in executor.

        This is used by integrations to run import statements in a separate
        thread to avoid blocking the event loop during module imports.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, target, *args)

    def async_run_job(self, target: Callable[..., Any], *args: Any) -> Any:
        """Run a job (coroutine or function) in the event loop.

        This is thread-safe - it can be called from any thread.
        If called from the event loop thread, it schedules immediately.
        If called from another thread, it uses run_coroutine_threadsafe.
        """
        # Check if we're in the event loop thread safely (without get_event_loop())
        try:
            current_loop = asyncio.get_running_loop()
            in_event_loop = current_loop == self._loop
        except RuntimeError:
            # No running loop in this thread
            in_event_loop = False

        if asyncio.iscoroutine(target) or asyncio.iscoroutinefunction(target):
            coro = target(*args) if asyncio.iscoroutinefunction(target) else target
            if in_event_loop:
                # We're in the event loop thread
                return asyncio.ensure_future(coro)
            else:
                # We're in a different thread (e.g., paho-mqtt callback)
                return asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            # Synchronous function - run in executor
            return self._loop.run_in_executor(None, functools.partial(target, *args))

    def async_create_task(
        self,
        target: asyncio.coroutine,
        name: Optional[str] = None,
        eager_start: bool = False,
    ) -> asyncio.Task:
        """Create a task.

        Args:
            target: The coroutine to run
            name: Optional task name (for debugging)
            eager_start: If True, eagerly start the task (HA 2024.3+)

        Returns:
            The created asyncio.Task
        """
        task = asyncio.create_task(target)
        if name:
            # Set task name if provided (for debugging)
            try:
                task.set_name(name)
            except AttributeError:
                pass
        return task

    def async_create_background_task(
        self,
        target: asyncio.coroutine,
        name: Optional[str] = None,
    ) -> asyncio.Task:
        """Create a background task.

        This is similar to async_create_task but for tasks that run in the background
        and should not block shutdown.

        Args:
            target: The coroutine to run
            name: Optional task name (for debugging)

        Returns:
            The created asyncio.Task
        """
        return self.async_create_task(target, name)

    def async_add_job(self, target: Callable, *args) -> asyncio.Future:
        """Add a job."""
        return self.async_run_job(target, *args)

    def async_fire(self, event_type: str, event_data: Optional[dict] = None) -> None:
        """Fire an event."""
        listeners = self._event_listeners.get(event_type, [])
        for listener in listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    asyncio.create_task(listener(event_data))
                else:
                    listener(event_data)
            except Exception as e:
                _LOGGER.error(f"Error in event listener: {e}")

    def async_track_state_change(
        self,
        entity_ids: List[str],
        action: Callable,
    ) -> Callable:
        """Track state changes for entities."""

        def state_listener(entity_id: str, old_state: State, new_state: State):
            if entity_id in entity_ids:
                action(entity_id, old_state, new_state)

        return self.states.async_add_listener(state_listener)
