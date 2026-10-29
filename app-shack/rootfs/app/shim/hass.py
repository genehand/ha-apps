"""HomeAssistant core class for the shim.

The main orchestrator that brings together all registries and mocks.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from homeassistant.core import CoreState, HassJob, HassJobType

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

        # Bookkeeping upstream helpers expect from a real hass.
        #
        # ``state`` is CoreState.running rather than a startup sequence: the
        # shim has no bootstrap, integrations are set up against an already
        # running instance, and the upstream helpers key their behaviour off
        # it (``helpers.start`` runs at_start callbacks immediately,
        # ``helpers.storage`` writes without deferring to a final-write event).
        self.state: CoreState = CoreState.running
        self.loop_thread_id = threading.get_ident()
        self._tasks: set[asyncio.Future] = set()
        self._background_tasks: set[asyncio.Future] = set()

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

        if asyncio.iscoroutine(target) or inspect.iscoroutinefunction(target):
            coro = target(*args) if inspect.iscoroutinefunction(target) else target
            if in_event_loop:
                # We're in the event loop thread
                return asyncio.ensure_future(coro)
            else:
                # We're in a different thread (e.g., paho-mqtt callback)
                return asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            # Synchronous function - run in executor
            return self._loop.run_in_executor(None, functools.partial(target, *args))

    @property
    def is_running(self) -> bool:
        """Return if the shim is running.

        Upstream ``is_running`` covers ``starting`` too; the shim only ever has
        one state, but keeping the upstream shape means ``helpers.start`` and
        integration code behave the same either way.
        """
        return self.state in (CoreState.starting, CoreState.running)

    @property
    def is_stopping(self) -> bool:
        """Return if the shim is stopping (it never is, but code asks)."""
        return self.state in (CoreState.stopping, CoreState.final_write)

    def verify_event_loop_thread(self, what: str) -> None:
        """Report if we are not running in the event loop thread.

        Upstream raises through ``helpers.frame``'s usage reporter. The shim
        logs instead: a hard raise inside e.g. an MQTT callback thread would
        take down a listener that upstream would only have complained about.
        """
        if self.loop_thread_id != threading.get_ident():
            _LOGGER.warning("%s called from a thread other than the event loop", what)

    def async_run_hass_job(
        self,
        hassjob: HassJob,
        *args: Any,
        background: bool = False,
    ) -> Optional[asyncio.Future]:
        """Run a HassJob from within the event loop.

        Callback jobs run inline (that is the upstream contract); coroutine and
        executor jobs are scheduled and a future is returned.
        """
        if hassjob.job_type is HassJobType.Callback:
            hassjob.target(*args)
            return None

        return self._async_add_hass_job(hassjob, *args, background=background)

    def async_add_hass_job(
        self,
        hassjob: HassJob,
        *args: Any,
        eager_start: bool = False,
        background: bool = False,
    ) -> Optional[asyncio.Future]:
        """Add a HassJob from within the event loop.

        Deprecated upstream (``eager_start`` is accepted and ignored: the shim
        has no eager task factory), but integrations still call it.
        """
        return self._async_add_hass_job(hassjob, *args, background=background)

    def _async_add_hass_job(
        self,
        hassjob: HassJob,
        *args: Any,
        background: bool = False,
    ) -> Optional[asyncio.Future]:
        """Schedule a HassJob, hopping onto the event loop if needed."""
        job_type = hassjob.job_type

        if job_type is HassJobType.Callback:
            hassjob.target(*args)
            return None

        if job_type is HassJobType.Coroutinefunction:
            coro = hassjob.target(*args)
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self._loop:
                task: Any = self._loop.create_task(coro, name=hassjob.name)
            else:
                # Called from another thread (an MQTT callback, a web worker).
                task = asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            task = self._loop.run_in_executor(None, hassjob.target, *args)

        bucket = self._background_tasks if background else self._tasks
        bucket.add(task)
        task.add_done_callback(bucket.discard)
        return task

    def async_create_task_internal(
        self,
        target: asyncio.coroutine,
        name: Optional[str] = None,
        eager_start: bool = True,
    ) -> asyncio.Task:
        """Create a task (upstream's internal flavour).

        ``eager_start`` is accepted and ignored; the shim has no eager task
        factory. Used by ``helpers.storage`` for delayed writes.
        """
        return self.async_create_task(target, name, eager_start=False)

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
        eager_start: bool = False,
    ) -> asyncio.Task:
        """Create a background task.

        This is similar to async_create_task but for tasks that run in the background
        and should not block shutdown.

        Args:
            target: The coroutine to run
            name: Optional task name (for debugging)
            eager_start: If True, eagerly start the task (HA 2024.3+)

        Returns:
            The created asyncio.Task
        """
        return self.async_create_task(target, name, eager_start)

    def async_add_job(self, target: Callable, *args) -> asyncio.Future:
        """Add a job."""
        return self.async_run_job(target, *args)

    def async_fire(self, event_type: str, event_data: Optional[dict] = None) -> None:
        """Fire an event."""
        listeners = self._event_listeners.get(event_type, [])
        for listener in listeners:
            try:
                if inspect.iscoroutinefunction(listener):
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
