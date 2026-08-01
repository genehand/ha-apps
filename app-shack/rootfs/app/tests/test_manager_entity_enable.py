"""Tests for per-entity runtime MQTT enable/disable + refresh (shim.manager)."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.entity import EntityRegistry


@pytest.fixture(autouse=True)
def _reset_registry():
    EntityRegistry._reset_for_test()
    yield
    EntityRegistry._reset_for_test()


def _create_manager():
    from mqtt_bridge import MqttBridge
    from shim.manager import ShimManager

    mock_bridge = MagicMock(spec=MqttBridge)
    mock_bridge.client = MagicMock()

    import unittest.mock as _mock

    with _mock.patch("shim.manager.HomeAssistant") as MockHass, \
         _mock.patch("shim.manager.IntegrationManager"), \
         _mock.patch("shim.manager.IntegrationLoader"):
        mock_hass = MagicMock()
        mock_hass.shim_dir = Path("/tmp/test_shim")
        mock_hass._storage = MagicMock()
        # Storage must return a real empty dict for load_entity_overrides,
        # otherwise MagicMock returns a truthy MagicMock that register()'s
        # override-lookup mistakes for a persisted override.
        mock_hass._storage.load_entity_overrides.return_value = {}
        # async_run_job should actually await the coroutine so we can test it.
        mock_hass.async_run_job = AsyncMock(side_effect=lambda coro, *a: coro)
        MockHass.return_value = mock_hass
        manager = ShimManager(Path("/tmp/test_config"), mock_bridge)
        return manager


def _make_entity(entity_id="sensor.dev_odometer", enabled_default=False):
    entity = SimpleNamespace(
        entity_id=entity_id,
        unique_id="dev_odometer",
        _attr_config_entry_id="ce",
        entity_registry_enabled_default=enabled_default,
        coordinator=MagicMock(async_request_refresh=AsyncMock()),
        _publish_mqtt_discovery=AsyncMock(),
    )
    return entity


class TestApplyEntityEnabled:
    @pytest.mark.asyncio
    async def test_enable_flips_registry_refreshes_and_republishes(self):
        manager = _create_manager()
        entity = _make_entity(enabled_default=False)
        registry = EntityRegistry()
        registry.setup(manager._hass)
        registry.register(entity)
        assert registry.get_registry_entry(entity.entity_id).disabled is True

        await manager.apply_entity_enabled(entity, True)

        entry = registry.get_registry_entry(entity.entity_id)
        assert entry.disabled is False
        assert entry.disabled_by is None
        entity.coordinator.async_request_refresh.assert_awaited_once()
        entity._publish_mqtt_discovery.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enable_refresh_false_skips_coordinator_refresh(self):
        manager = _create_manager()
        entity = _make_entity(enabled_default=False)
        registry = EntityRegistry()
        registry.setup(manager._hass)
        registry.register(entity)

        await manager.apply_entity_enabled(entity, True, refresh=False)

        entry = registry.get_registry_entry(entity.entity_id)
        assert entry.disabled is False
        entity.coordinator.async_request_refresh.assert_not_awaited()
        # Discovery is still republished regardless of refresh.
        entity._publish_mqtt_discovery.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disable_flips_registry_republishes_no_refresh(self):
        manager = _create_manager()
        entity = _make_entity(enabled_default=True)
        entity._publish_mqtt_discovery = AsyncMock()
        registry = EntityRegistry()
        registry.setup(manager._hass)
        registry.register(entity)
        assert registry.get_registry_entry(entity.entity_id).disabled is False

        await manager.apply_entity_enabled(entity, False)

        entry = registry.get_registry_entry(entity.entity_id)
        assert entry.disabled is True
        assert entry.disabled_by == "user"
        entity.coordinator.async_request_refresh.assert_not_awaited()
        entity._publish_mqtt_discovery.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enable_without_publish_method_is_ok(self):
        manager = _create_manager()
        entity = _make_entity(enabled_default=False)
        del entity._publish_mqtt_discovery  # base entity without discovery
        registry = EntityRegistry()
        registry.setup(manager._hass)
        registry.register(entity)

        await manager.apply_entity_enabled(entity, True)
        assert registry.get_registry_entry(entity.entity_id).disabled is False

    @pytest.mark.asyncio
    async def test_enable_without_coordinator_is_noop_on_refresh(self):
        manager = _create_manager()
        entity = _make_entity(enabled_default=False)
        entity.coordinator = None
        registry = EntityRegistry()
        registry.setup(manager._hass)
        registry.register(entity)

        await manager.apply_entity_enabled(entity, True)
        assert registry.get_registry_entry(entity.entity_id).disabled is False

    @pytest.mark.asyncio
    async def test_republish_failure_does_not_raise(self):
        manager = _create_manager()
        entity = _make_entity(enabled_default=False)
        entity._publish_mqtt_discovery = AsyncMock(
            side_effect=RuntimeError("mqtt down")
        )
        registry = EntityRegistry()
        registry.setup(manager._hass)
        registry.register(entity)

        # Should not raise even if discovery publish fails.
        await manager.apply_entity_enabled(entity, True)
        assert registry.get_registry_entry(entity.entity_id).disabled is False


class TestOnEntityEnableTopicParsing:
    def test_enable_topic_routes_to_apply_enabled_true(self, monkeypatch):
        manager = _create_manager()
        entity = _make_entity()
        monkeypatch.setattr(manager, "_find_entity",
                            lambda eid, object_id=None: entity)

        captured = []
        manager._hass.async_run_job = lambda fn, *a: captured.append((fn, a))

        msg = MagicMock()
        msg.topic = "homeassistant/sensor/dev-odometer/enable"

        manager._on_entity_enable(MagicMock(), None, msg)

        assert captured, "async_run_job was not invoked"
        fn, args = captured[0]
        assert "apply_entity_enabled" in fn.__qualname__
        assert args == (entity, True)

    def test_disable_topic_routes_to_apply_enabled_false(self, monkeypatch):
        manager = _create_manager()
        entity = _make_entity()
        monkeypatch.setattr(manager, "_find_entity",
                            lambda eid, object_id=None: entity)

        captured = []
        manager._hass.async_run_job = lambda fn, *a: captured.append((fn, a))

        msg = MagicMock()
        msg.topic = "homeassistant/sensor/dev-odometer/disable"

        manager._on_entity_disable(MagicMock(), None, msg)
        assert captured, "async_run_job was not invoked"
        fn, args = captured[0]
        assert "apply_entity_enabled" in fn.__qualname__
        assert args == (entity, False)

    def test_enable_topic_missing_entity_is_noop(self, monkeypatch, caplog):
        manager = _create_manager()
        monkeypatch.setattr(manager, "_find_entity",
                            lambda eid, object_id=None: None)
        manager._hass.async_run_job = MagicMock()

        msg = MagicMock()
        msg.topic = "homeassistant/sensor/dev-odometer/enable"
        manager._on_entity_enable(MagicMock(), None, msg)
        manager._hass.async_run_job.assert_not_called()

    def test_subscriptions_include_enable_disable_and_refresh_topics(self):
        manager = _create_manager()
        manager._mqtt_client = MagicMock()
        sub_calls = []

        def fake_subscribe(topic, cb):
            sub_calls.append(topic)
            return (0, 0)

        manager._mqtt_bridge.subscribe = fake_subscribe
        manager._setup_mqtt_subscriptions()
        assert "homeassistant/+/+/enable" in sub_calls
        assert "homeassistant/+/+/disable" in sub_calls
        assert "homeassistant/+/+/refresh" in sub_calls


class TestEntityRefresh:
    """Tests for the per-entity refresh MQTT handler."""

    def _wire(self, manager):
        """Make async_run_job capture coroutines for the test to await."""
        captured = []
        manager._hass.async_run_job = lambda fn, *a: captured.append(
            fn(*a) if a else fn()
        )
        return captured

    @pytest.mark.asyncio
    async def test_refresh_batches_specific_entity_then_refreshes(self, monkeypatch):
        manager = _create_manager()
        entity = _make_entity(enabled_default=True)
        entity.entity_description = SimpleNamespace(key="odometer")
        entity.coordinator = MagicMock(
            async_request_refresh=AsyncMock(),
            batch_sensor=MagicMock(),
            is_scope_enabled=MagicMock(return_value=True),
        )
        monkeypatch.setattr(manager, "_find_entity",
                            lambda eid, object_id=None: entity)
        captured = self._wire(manager)

        msg = MagicMock()
        msg.topic = "homeassistant/sensor/dev-odometer/refresh"
        manager._on_entity_refresh(MagicMock(), None, msg)
        for coro in captured:
            await coro

        entity.coordinator.batch_sensor.assert_called_once_with(entity)
        entity.coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_scope_disabled_skips_batch(self, monkeypatch):
        manager = _create_manager()
        entity = _make_entity(enabled_default=True)
        entity.entity_description = SimpleNamespace(key="odometer")
        entity.coordinator = MagicMock(
            async_request_refresh=AsyncMock(),
            batch_sensor=MagicMock(),
            is_scope_enabled=MagicMock(return_value=False),
        )
        monkeypatch.setattr(manager, "_find_entity",
                            lambda eid, object_id=None: entity)
        captured = self._wire(manager)

        msg = MagicMock()
        msg.topic = "homeassistant/sensor/dev-odometer/refresh"
        manager._on_entity_refresh(MagicMock(), None, msg)
        for coro in captured:
            await coro

        entity.coordinator.batch_sensor.assert_not_called()
        entity.coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refresh_without_batch_sensor_uses_plain_refresh(self, monkeypatch):
        manager = _create_manager()
        entity = _make_entity(enabled_default=True)
        entity.coordinator = MagicMock(async_request_refresh=AsyncMock())
        del entity.coordinator.batch_sensor
        monkeypatch.setattr(manager, "_find_entity",
                            lambda eid, object_id=None: entity)
        captured = self._wire(manager)

        msg = MagicMock()
        msg.topic = "homeassistant/sensor/dev-odometer/refresh"
        manager._on_entity_refresh(MagicMock(), None, msg)
        for coro in captured:
            await coro

        entity.coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_no_coordinator_is_noop(self, monkeypatch):
        manager = _create_manager()
        entity = _make_entity(enabled_default=True)
        entity.coordinator = None
        monkeypatch.setattr(manager, "_find_entity",
                            lambda eid, object_id=None: entity)
        captured = self._wire(manager)

        msg = MagicMock()
        msg.topic = "homeassistant/sensor/dev-odometer/refresh"
        manager._on_entity_refresh(MagicMock(), None, msg)
        for coro in captured:
            await coro

    @pytest.mark.asyncio
    async def test_refresh_batch_sensor_raises_falls_back_to_plain_refresh(
        self, monkeypatch
    ):
        manager = _create_manager()
        entity = _make_entity(enabled_default=True)
        entity.entity_description = SimpleNamespace(key="odometer")
        entity.coordinator = MagicMock(
            async_request_refresh=AsyncMock(),
            batch_sensor=MagicMock(side_effect=AssertionError("scope")),
        )
        del entity.coordinator.is_scope_enabled
        monkeypatch.setattr(manager, "_find_entity",
                            lambda eid, object_id=None: entity)
        captured = self._wire(manager)

        msg = MagicMock()
        msg.topic = "homeassistant/sensor/dev-odometer/refresh"
        manager._on_entity_refresh(MagicMock(), None, msg)
        for coro in captured:
            await coro

        entity.coordinator.batch_sensor.assert_called_once_with(entity)
        entity.coordinator.async_request_refresh.assert_awaited_once()


class TestManagerInitNoForceEnableParam:
    """ShimManager.__init__ no longer accepts force_enable_entities."""

    def test_init_signature_unaffected_old_kwarg_rejected(self):
        from mqtt_bridge import MqttBridge
        from shim.manager import ShimManager
        import unittest.mock as _mock
        import inspect

        mock_bridge = MagicMock(spec=MqttBridge)
        with _mock.patch("shim.manager.HomeAssistant"), \
             _mock.patch("shim.manager.IntegrationManager"), \
             _mock.patch("shim.manager.IntegrationLoader"):
            with pytest.raises(TypeError):
                ShimManager(
                    Path("/tmp/test_config"),
                    mock_bridge,
                    force_enable_entities=["odometer"],
                )