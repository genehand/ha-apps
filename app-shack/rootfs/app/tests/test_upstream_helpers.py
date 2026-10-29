"""Upstream helpers adopted verbatim from the Home Assistant wheel.

``helpers.storage``, ``.dispatcher``, ``.start``, ``.instance_id``, the
floor/label/category/issue registries and ``.json`` are no longer stubs: the
patcher installs ``shim.core`` as ``homeassistant.core`` first and then imports
the real files, so their ``from homeassistant.core import ...`` binds the shim's
classes. See docs/upstream-tier.md for the technique.

The tests here cover the *runtime* half of that bet — ``test_upstream_modules.py``
covers the import boundary and the prune keep-list. Imports can be made to work
by topping up ``shim/core.py``; these modules only actually function if the
shim's ``hass`` provides what an upstream helper expects -- a ``CoreState``, a
bus it can fire internal events on, ``async_run_hass_job``, loop-thread
verification and task creation.
"""

import asyncio
import json
import threading
from pathlib import Path

import pytest

from shim.core import CoreState, HassJob, HassJobType, HomeAssistant, State


@pytest.fixture
async def hass(tmp_path):
    """A shim hass with a throwaway config dir."""
    return HomeAssistant(config_dir=tmp_path)


# --- the hass surface the helpers call ------------------------------------


def test_hass_reports_running_state(hass):
    """helpers.storage/start key their behaviour off hass.state.

    A shim instance is always 'running': integrations are set up against an
    already-booted Home Assistant, so at_start callbacks must run immediately
    and stores must write now instead of deferring to a final-write event that
    never fires.
    """
    assert hass.state is CoreState.running
    assert hass.is_running is True
    assert hass.is_stopping is False


async def test_async_run_hass_job_runs_callbacks_inline(hass):
    """Callback jobs run synchronously and return None (upstream contract)."""
    calls = []

    job = HassJob(lambda *args: calls.append(args), job_type=HassJobType.Callback)
    result = hass.async_run_hass_job(job, 1, 2)

    assert result is None
    assert calls == [(1, 2)]


async def test_async_run_hass_job_schedules_coroutines(hass):
    """Coroutine jobs are scheduled as tasks and awaited by the caller."""
    done = asyncio.Event()

    async def target(value):
        assert value == "x"
        done.set()

    hass.async_run_hass_job(HassJob(target), "x")
    await asyncio.wait_for(done.wait(), timeout=1)


async def test_async_run_hass_job_runs_executor_jobs(hass):
    """Executor jobs go to the default executor, not the event loop."""
    loop_thread = threading.get_ident()

    def target():
        return threading.get_ident()

    job = HassJob(target, job_type=HassJobType.Executor)
    result = await hass.async_run_hass_job(job)

    assert result != loop_thread


async def test_verify_event_loop_thread_warns_off_loop(hass, caplog):
    """The upstream registries call this; the shim warns instead of raising.

    Upstream reports through helpers.frame and raises. Raising from, say, an
    MQTT callback thread would kill a listener that upstream would only have
    complained about, so this stays a warning.
    """
    hass.verify_event_loop_thread("unit.test")  # on the loop: quiet
    assert "unit.test" not in caplog.text

    await asyncio.to_thread(hass.verify_event_loop_thread, "unit.test.thread")
    assert "unit.test.thread" in caplog.text


async def test_bus_async_fire_internal_reaches_listeners(hass):
    """Upstream registries announce changes with async_fire_internal."""
    seen = []
    hass.bus.async_listen("test_event", seen.append)

    hass.bus.async_fire_internal("test_event", {"action": "create"})

    assert seen == [{"action": "create"}]


async def test_helpers_start_runs_immediately_when_running(hass):
    """helpers.start (new capability) assumes it is called after startup."""
    from homeassistant.helpers.start import async_at_start, async_at_started

    calls = []

    async def at_start(hass):
        calls.append("start")

    async def at_started(hass):
        calls.append("started")

    async_at_start(hass, at_start)
    async_at_started(hass, at_started)
    await asyncio.sleep(0)

    assert calls == ["start", "started"]


# --- storage --------------------------------------------------------------


async def test_store_writes_to_dot_storage(hass):
    """The real Store keeps the layout the shim's stub used: <config>/.storage.

    That is what makes the swap free: integration data written by the old stub
    is read back by the real Store, and the shim's own state (data/shim/*.json,
    via shim/storage.py) lives somewhere else entirely and is untouched.
    """
    from homeassistant.helpers.storage import Store

    store = Store(hass, 1, "upstream_store_test")
    await store.async_save({"hello": "world"})

    on_disk = Path(hass.config.path(".storage", "upstream_store_test"))
    assert json.loads(on_disk.read_text()) == {
        "version": 1,
        "minor_version": 1,
        "key": "upstream_store_test",
        "data": {"hello": "world"},
    }
    assert not (Path(hass.config_dir) / "shim" / "upstream_store_test").exists()
    assert await Store(hass, 1, "upstream_store_test").async_load() == {"hello": "world"}


async def test_store_delayed_save_uses_create_task_internal(hass):
    """async_delay_save schedules through hass.async_create_task_internal."""
    from homeassistant.helpers.storage import Store

    store = Store(hass, 1, "upstream_delayed_store")
    store.async_delay_save(lambda: {"delayed": True}, delay=0.01)
    await asyncio.sleep(0.1)

    assert Path(hass.config.path(".storage", "upstream_delayed_store")).exists()
    assert await store.async_load() == {"delayed": True}


# --- dispatcher -----------------------------------------------------------


async def test_dispatcher_connect_send_disconnect(hass):
    """helpers.dispatcher (sibling of event.py, no registries needed)."""
    from homeassistant.helpers.dispatcher import (
        async_dispatcher_connect,
        async_dispatcher_send,
    )

    seen = []

    unsub = async_dispatcher_connect(hass, "upstream_signal", seen.append)
    async_dispatcher_send(hass, "upstream_signal", "payload")
    assert seen == ["payload"]

    unsub()
    async_dispatcher_send(hass, "upstream_signal", "dropped")
    assert seen == ["payload"]


# --- registries (new capability) -----------------------------------------


async def test_floor_registry_round_trips(hass):
    """A registry the shim never had: floors, loaded from .storage."""
    from homeassistant.helpers.floor_registry import (
        async_get,
        async_load,
    )

    await async_load(hass)
    registry = async_get(hass)
    entry = registry.async_create("Ground floor", level=0)
    registry.async_update(entry.floor_id, name="Ground")

    # Schedule the write the way upstream does, then flush it.
    await registry._store.async_save(registry._data_to_save())

    reloaded = json.loads(Path(hass.config.path(".storage", "core.floor_registry")).read_text())
    assert reloaded["data"]["floors"][0]["name"] == "Ground"
    assert reloaded["data"]["floors"][0]["floor_id"] == entry.floor_id


async def test_label_and_category_registry_round_trip(hass):
    """Labels and categories (created through upstream's registry API)."""
    from homeassistant.helpers import category_registry, label_registry

    await label_registry.async_load(hass)
    label = label_registry.async_get(hass).async_create("Bright")
    assert label.name == "Bright"

    await category_registry.async_load(hass)
    category = category_registry.async_get(hass).async_create(scope="light", name="Mood")
    assert category.name == "Mood"
    assert category.category_id


async def test_issue_registry_create_and_delete(hass):
    """Repairs issues: the shim's stub silently did nothing here."""
    from homeassistant.helpers import issue_registry

    await issue_registry.async_load(hass)
    registry = issue_registry.async_get(hass)

    issue_registry.async_create_issue(
        hass,
        domain="upstream_test",
        issue_id="needs_attention",
        is_fixable=False,
        is_persistent=False,
        severity=issue_registry.IssueSeverity.WARNING,
        translation_key="needs_attention",
    )
    assert ("upstream_test", "needs_attention") in registry.issues

    issue_registry.async_delete_issue(hass, "upstream_test", "needs_attention")
    assert not registry.issues


async def test_instance_id_persists_between_runs(hass, tmp_path):
    """helpers.instance_id stores into .storage/core.uuid."""
    from homeassistant.helpers.instance_id import async_get, async_recreate

    first = await async_get(hass)
    assert first
    assert (tmp_path / ".storage" / "core.uuid").exists()
    assert await async_get(hass) == first

    # async_recreate() writes the new uuid to the store; the singleton keeps
    # serving the value it already cached (upstream behaviour, and the reason
    # the new id is meant for the *next* run).
    second = await async_recreate(hass)
    assert second != first
    assert second in (tmp_path / ".storage" / "core.uuid").read_text()


# --- helpers.network ------------------------------------------------------


async def test_network_get_url_answers_with_the_shims_own_url(hass):
    """get_url() used to be a stub returning http://localhost:8123.

    That is Home Assistant's port; nothing listens there. The real helper reads
    hass.config.internal_url, so the shim advertises its own UI instead -- and
    an add-on behind ingress/reverse proxy can override it.
    """
    from homeassistant.helpers.network import get_url

    assert get_url(hass) == "http://localhost:8080"


async def test_network_get_url_and_is_hass_url_follow_config(hass):
    """No URL configured is the one case that raises, as upstream documents."""
    from homeassistant.helpers.network import (
        NoURLAvailableError,
        get_url,
        is_hass_url,
    )

    hass.config.internal_url = None
    with pytest.raises(NoURLAvailableError):
        get_url(hass)

    hass.config.internal_url = "https://shack.example.com"
    assert get_url(hass) == "https://shack.example.com"
    assert is_hass_url(hass, "https://shack.example.com/lovelace") is True
    assert is_hass_url(hass, "https://example.org/") is False


# --- helpers.area_registry (against the shim's device registry) -----------


async def test_area_registry_create_assign_and_delete(hass):
    """Areas are real data now, and deleting one clears it off its devices.

    area_registry calls device_registry.async_clear_area_id() on delete; the
    shim's registry did not implement that (or even store area_id), which is
    exactly the kind of gap the old stubs hid.
    """
    from homeassistant.helpers import area_registry, device_registry

    await area_registry.async_load(hass)
    areas = area_registry.async_get(hass)
    area = areas.async_create("Kitchen")

    devices = device_registry.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id="entry_1", identifiers={("demo", "device_1")}
    )
    devices.async_update_device(device.id, area_id=area.id)
    assert devices.async_get(device.id).area_id == area.id

    areas.async_delete(area.id)
    assert areas.async_get_area(area.id) is None
    assert devices.async_get(device.id).area_id is None


async def test_suggested_area_creates_and_assigns_an_area(hass):
    """An integration's ``suggested_area`` hint becomes a real ``area_id``.

    HA applies the hint where the device row is created - it looks the area up
    by name in the area registry and creates it when it is new - so an entity
    that only knows a room *name* still leaves a device with an ``area_id``,
    which is what integrations read back from ``device_registry``.
    """
    from homeassistant.helpers import area_registry, device_registry

    await area_registry.async_load(hass)
    areas = area_registry.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    devices = device_registry.async_get(hass)

    # An existing area is reused, not duplicated.
    device = devices.async_get_or_create(
        config_entry_id="entry_1",
        identifiers={("demo", "device_1")},
        suggested_area="Kitchen",
    )
    assert device.area_id == kitchen.id
    assert device.suggested_area == "Kitchen"
    assert len(list(areas.async_list_areas())) == 1

    # An unknown name creates the area (HA's ``async_get_or_create``).
    device_2 = devices.async_get_or_create(
        config_entry_id="entry_1",
        identifiers={("demo", "device_2")},
        suggested_area="Living Room",
    )
    living_room = areas.async_get_area(device_2.area_id)
    assert living_room is not None
    assert living_room.name == "Living Room"


async def test_suggested_area_does_not_move_an_existing_device(hass):
    """The hint is applied on creation only, so it cannot undo a user's move.

    HA keeps sending the hint on every re-registration (it is deprecated, not
    removed) while the area the *user* assigned wins; the shim has to match
    that or a reload would silently move every device back.
    """
    from homeassistant.helpers import area_registry, device_registry

    await area_registry.async_load(hass)
    areas = area_registry.async_get(hass)
    devices = device_registry.async_get(hass)

    device = devices.async_get_or_create(
        config_entry_id="entry_1",
        identifiers={("demo", "device_1")},
        suggested_area="Kitchen",
    )
    attic = areas.async_create("Attic")
    devices.async_update_device(device.id, area_id=attic.id)

    same = devices.async_get_or_create(
        config_entry_id="entry_1",
        identifiers={("demo", "device_1")},
        suggested_area="Basement",
    )

    assert same.id == device.id
    assert same.area_id == attic.id
    assert same.suggested_area == "Basement"
    # ... and an unmapped hint does not create a stray area either.
    assert areas.async_get_area_by_name("Basement") is None


# --- the pure leaves that were never stubbed ------------------------------


async def test_debouncer_runs_and_defers(hass):
    """Debouncer drives hass.async_run_hass_job/async_create_task and cooldown."""
    from homeassistant.helpers.debounce import Debouncer

    calls = []
    debouncer = Debouncer(
        hass,
        logger=__import__("logging").getLogger(__name__),
        cooldown=0.05,
        immediate=True,
        function=lambda: calls.append(len(calls)),
    )

    await debouncer.async_call()  # immediate: runs now
    assert calls == [0]

    await debouncer.async_call()  # inside cooldown: deferred, then run
    assert calls == [0]
    await asyncio.sleep(0.15)
    assert calls == [0, 1]

    debouncer.async_shutdown()


def test_sun_helper_uses_the_configured_location(hass):
    """helpers.sun wraps astral; it only needs hass.config lat/long/elevation."""
    from homeassistant.helpers.sun import get_astral_event_next
    from homeassistant.util import dt as dt_util

    hass.config.latitude = 52.5
    hass.config.longitude = 13.4

    next_sunrise = get_astral_event_next(hass, "sunrise", dt_util.utcnow())
    assert next_sunrise > dt_util.utcnow()


async def test_ratelimit_schedules_a_deferred_action(hass):
    """KeyedRateLimit schedules through hass.loop."""
    from homeassistant.helpers.ratelimit import KeyedRateLimit

    calls = []
    rate_limit = KeyedRateLimit(hass)
    rate_limit.async_triggered("key", now=1000.0)

    when = rate_limit.async_schedule_action(
        "key", 0.01, 1000.0, lambda: calls.append("ran")
    )
    assert when == pytest.approx(1000.01)
    assert calls == []

    await asyncio.sleep(0.05)
    assert calls == ["ran"]
    rate_limit.async_remove()


def test_location_helpers_read_state_coordinates(hass):
    """helpers.location works on the shim's own State objects."""
    from homeassistant.helpers.location import closest, has_location

    near = State("device_tracker.near", "home", {"latitude": 52.5, "longitude": 13.4})
    far = State("device_tracker.far", "home", {"latitude": 40.0, "longitude": -74.0})
    elsewhere = State("device_tracker.unknown", "home", {})

    assert has_location(near) is True
    assert has_location(elsewhere) is False
    assert closest(52.5, 13.4, [far, near]) is near


def test_trace_helpers_keep_a_context_local_trace_id():
    """helpers.trace is pure ContextVar bookkeeping (used by scripts)."""
    from homeassistant.helpers import trace

    assert trace.trace_id_get() is None
    trace.trace_id_set(("script", "run_1"))
    assert trace.trace_id_get() == ("script", "run_1")
