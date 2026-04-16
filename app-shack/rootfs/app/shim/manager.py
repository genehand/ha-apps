"""Main Shim Manager for Home Assistant Integration Bridge.

Orchestrates the shim, MQTT bridge, and integrations.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from paho.mqtt.client import Client, MQTTMessage

from config import get_addon_slug
from .logging import get_logger
from .core import HomeAssistant, ConfigEntry
from .storage import Storage
from .import_patch import setup_import_patching
from .entity import EntityRegistry, get_mqtt_object_id
from .integrations.manager import IntegrationManager
from .integrations.loader import IntegrationLoader

if TYPE_CHECKING:
    from ..mqtt_bridge import MqttBridge

_LOGGER = get_logger(__name__)


class ShimManager:
    """Main manager for the HA shim."""

    def __init__(
        self,
        config_dir: Path,
        mqtt_bridge: "MqttBridge",
        mqtt_base_topic: str = "shim",
    ):
        self._config_dir = Path(config_dir)
        self._mqtt_bridge = mqtt_bridge
        self._mqtt_client = mqtt_bridge.client if mqtt_bridge else None
        self._mqtt_base_topic = mqtt_base_topic

        # Initialize core HA shim
        _LOGGER.debug("Initializing Home Assistant shim")
        self._hass = HomeAssistant(self._config_dir)
        self._hass._mqtt_client = self._mqtt_client  # Give hass access to MQTT

        # Initialize storage
        self._storage = self._hass._storage

        # Initialize integration management
        self._integration_manager = IntegrationManager(
            self._storage,
            self._hass.shim_dir,
        )

        # Initialize integration loader
        self._integration_loader = IntegrationLoader(
            self._hass, self._integration_manager
        )

        # Store loader reference for FlowManager access
        self._hass.data["integration_loader"] = self._integration_loader

        # State tracking
        self._running = False
        self._update_check_task = None

        # Semaphore to limit concurrent integration setups (max 5 parallel)
        self._setup_semaphore = asyncio.Semaphore(5)

        # Loading state tracking
        self._loading_complete = False
        self._loading_event = asyncio.Event()
        self._loading_task = None

    async def start(self) -> None:
        """Start the shim manager (phase 1 - fast setup).

        This performs the fast initialization that must complete before
        the web server starts:
        - MQTT subscriptions
        - HACS repository fetch
        - Basic state setup

        Integration loading happens separately via start_integration_loading().
        """
        _LOGGER.debug("Starting Shim Manager (phase 1 - fast setup)")
        self._running = True

        # Setup MQTT subscriptions
        self._setup_mqtt_subscriptions()

        # Fetch HACS repositories (fast, cached)
        _LOGGER.debug("Checking HACS repository list")
        await self._integration_manager.fetch_hacs_repositories()

        # Start integration manager background tasks
        # Set callback so periodic updates trigger MQTT publish
        self._integration_manager.set_updates_found_callback(
            self._on_updates_found_callback
        )
        await self._integration_manager.start_background_tasks()

        _LOGGER.debug("Shim Manager phase 1 complete (ready for web server)")

    async def start_integration_loading(self) -> None:
        """Start loading integrations in the background (phase 2).

        This runs after the web server is started, allowing the UI to be
        accessible while integrations are being set up.
        """
        _LOGGER.info("Starting integration loading in background (phase 2)")

        # Show helpful message for meross_lan users about expected warnings
        enabled_domains = {
            info.domain for info in self._integration_manager.get_enabled_integrations()
        }
        if "meross_lan" in enabled_domains:
            _LOGGER.info(
                "Note: 'Unable to identify abilities' and "
                "'KeyError in attach_mqtt' warnings are usually for devices "
                "in your Meross cloud account that are currently unreachable."
            )

        # Load and setup enabled integrations in the background
        self._loading_task = asyncio.create_task(self._load_integrations_background())

        # Initial update check and notify (in background, don't block startup)
        asyncio.create_task(self._initial_update_check())

        # Start periodic update checks
        self._update_check_task = asyncio.create_task(self._periodic_update_checks())

    async def _load_integrations_background(self) -> None:
        """Background task to load all enabled integrations."""
        try:
            await self._load_enabled_integrations()
            self._loading_complete = True
            self._loading_event.set()
            _LOGGER.info("Integration loading complete")
        except Exception as e:
            _LOGGER.error(f"Error during integration loading: {e}")
            # Still mark as complete so web UI isn't stuck
            self._loading_complete = True
            self._loading_event.set()

    @property
    def is_loading(self) -> bool:
        """Check if integrations are still being loaded."""
        return not self._loading_complete

    async def wait_for_loading(self) -> None:
        """Wait for integration loading to complete."""
        await self._loading_event.wait()

    async def stop(self) -> None:
        """Stop the shim manager."""
        _LOGGER.info("Stopping Shim Manager")
        self._running = False

        # Cancel update check task
        if self._update_check_task:
            self._update_check_task.cancel()
            try:
                await self._update_check_task
            except asyncio.CancelledError:
                pass

        # Stop integration manager background tasks
        await self._integration_manager.stop_background_tasks()

        # Unload all integrations without cleaning up MQTT topics
        # (preserve topics for reconnection on restart)
        _LOGGER.info("Starting graceful shutdown of integrations...")
        for domain in self._integration_loader.get_loaded_integrations():
            entries = self._hass.config_entries.async_entries(domain)
            for entry in entries:
                _LOGGER.info(
                    f"Unloading {domain} entry {entry.entry_id} (cleanup_mqtt=False)"
                )
                try:
                    await self._integration_loader.unload_integration(
                        entry, cleanup_mqtt=False
                    )
                except Exception as e:
                    # Log but don't fail shutdown for external integration errors
                    _LOGGER.warning(
                        f"Error unloading {domain} entry {entry.entry_id} during shutdown: {e}"
                    )

        # Close all cached aiohttp client sessions to prevent
        # 'Unclosed client session' warnings
        try:
            from homeassistant.helpers.aiohttp_client import _async_close_clientsessions

            await _async_close_clientsessions()
            _LOGGER.debug("Closed aiohttp client sessions")
        except Exception as e:
            _LOGGER.debug(f"Error closing aiohttp sessions: {e}")

        _LOGGER.debug("Shim Manager stopped")

    def _setup_mqtt_subscriptions(self) -> None:
        """Setup MQTT command subscriptions."""

        # Add a catch-all handler for debugging
        def on_message(client, userdata, msg):
            _LOGGER.debug(
                f"MQTT message received: {msg.topic} = {msg.payload.decode()[:100]}"
            )

        if self._mqtt_client:
            self._mqtt_client.on_message = on_message

        # Subscribe to command topics for all entity types
        # Use # wildcard to match all command topics (set, percentage_set, preset_mode_set, etc.)
        topics = [
            ("homeassistant/+/+/set", self._on_entity_command),
            ("homeassistant/+/+/+/set", self._on_entity_command),
            ("homeassistant/+/+/percentage_set", self._on_entity_command),
            ("homeassistant/+/+/preset_mode_set", self._on_entity_command),
            ("homeassistant/+/+/oscillation_set", self._on_entity_command),
            ("homeassistant/+/+/brightness_set", self._on_entity_command),
            ("homeassistant/+/+/temperature_set", self._on_entity_command),
            ("homeassistant/+/+/mode_set", self._on_entity_command),
            (
                f"{self._mqtt_base_topic}/integrations/+/enable",
                self._on_enable_integration,
            ),
            (
                f"{self._mqtt_base_topic}/integrations/+/disable",
                self._on_disable_integration,
            ),
            (
                f"{self._mqtt_base_topic}/integrations/+/update",
                self._on_update_integration,
            ),
        ]

        for topic, callback in topics:
            result, mid = self._mqtt_bridge.subscribe(topic, callback)
            _LOGGER.debug(
                f"Subscribed to MQTT topic: {topic} (result={result}, mid={mid})"
            )

    def _on_entity_command(
        self, client: Client, userdata: Any, message: MQTTMessage
    ) -> None:
        """Handle entity command from MQTT."""
        try:
            topic = message.topic
            payload = message.payload.decode()
            _LOGGER.debug(f"Received MQTT command: topic={topic}, payload={payload}")

            # Parse topic: homeassistant/<domain>/<entity_id>/<command>
            # Command can be: set, percentage_set, preset_mode_set, oscillation_set, etc.
            parts = topic.split("/")
            if len(parts) < 4:
                _LOGGER.warning(f"Invalid topic format: {topic}")
                return

            domain = parts[1]
            object_id = parts[2]
            # Convert dash-separated object_id back to underscore format
            # MQTT topics use dashes, but entity IDs use underscores
            object_id = object_id.replace("-", "_")
            entity_id = f"{domain}.{object_id}"
            # Last part is the command type (e.g., "set", "percentage_set", "preset_mode_set")
            command_type = parts[-1]
            _LOGGER.debug(
                f"Parsed command: domain={domain}, entity_id={entity_id}, command_type={command_type}"
            )

            # Find entity - try both reconstructed entity_id and MQTT-safe object_id
            entity = self._find_entity(entity_id, object_id=parts[2])
            if not entity:
                _LOGGER.warning(
                    f"Entity {entity_id} (object_id: {parts[2]}) not found for command"
                )
                return

            _LOGGER.debug(f"Found entity {entity_id}, routing command")
            _LOGGER.debug(f"  Command type: {command_type}, payload: '{payload}'")
            # Route command to entity (thread-safe)
            self._hass.async_run_job(self._route_command, entity, command_type, payload)

        except Exception as e:
            _LOGGER.error(f"Error handling entity command: {e}")

    async def _route_command(
        self, entity: Any, command_type: str, payload: str
    ) -> None:
        """Route a command to the appropriate entity method."""
        try:
            _LOGGER.debug(
                f"Routing command: entity={entity.entity_id}, type={command_type}, payload={payload}"
            )
            if command_type == "set":
                # Check for text entity first (has async_set_value but not turn_on)
                if hasattr(entity, "async_set_value"):
                    # Text entity set value
                    _LOGGER.debug(
                        f"Setting text value for {entity.entity_id} to '{payload}'"
                    )
                    await entity.async_set_value(payload)
                elif payload.upper() == "PRESS":
                    # Button press command
                    _LOGGER.debug(f"Pressing button {entity.entity_id}")
                    if hasattr(entity, "async_press"):
                        await entity.async_press()
                    elif hasattr(entity, "press"):
                        entity.press()
                    else:
                        _LOGGER.warning(
                            f"Entity {entity.entity_id} has no press method"
                        )
                elif payload.upper() == "ON":
                    _LOGGER.debug(f"Turning ON entity {entity.entity_id}")
                    if hasattr(entity, "async_turn_on"):
                        await entity.async_turn_on()
                    elif hasattr(entity, "turn_on"):
                        entity.turn_on()
                    else:
                        _LOGGER.warning(
                            f"Entity {entity.entity_id} has no turn_on method"
                        )
                elif payload.upper() == "OFF":
                    _LOGGER.debug(f"Turning OFF entity {entity.entity_id}")
                    if hasattr(entity, "async_turn_off"):
                        await entity.async_turn_off()
                    elif hasattr(entity, "turn_off"):
                        entity.turn_off()
                    else:
                        _LOGGER.warning(
                            f"Entity {entity.entity_id} has no turn_off method"
                        )

            elif command_type == "percentage_set":
                # Fan speed
                _LOGGER.debug(
                    f"  Routing percentage_set: payload='{payload}', int={int(payload)}"
                )
                if hasattr(entity, "async_set_percentage"):
                    _LOGGER.debug(f"  Calling async_set_percentage with {int(payload)}")
                    await entity.async_set_percentage(int(payload))
                elif hasattr(entity, "set_percentage"):
                    _LOGGER.debug(f"  Calling set_percentage with {int(payload)}")
                    entity.set_percentage(int(payload))

            elif command_type == "preset_mode_set":
                # Fan preset mode
                if hasattr(entity, "async_set_preset_mode"):
                    await entity.async_set_preset_mode(payload)
                elif hasattr(entity, "set_preset_mode"):
                    entity.set_preset_mode(payload)

            elif command_type == "oscillation_set":
                # Fan oscillation
                if hasattr(entity, "async_oscillate"):
                    await entity.async_oscillate(payload.upper() == "ON")
                elif hasattr(entity, "oscillate"):
                    entity.oscillate(payload.upper() == "ON")

            elif command_type == "brightness_set":
                # Light brightness
                if hasattr(entity, "async_turn_on"):
                    await entity.async_turn_on(brightness=int(payload))
                elif hasattr(entity, "turn_on"):
                    entity.turn_on(brightness=int(payload))

            elif command_type == "temperature_set":
                # Climate temperature
                if hasattr(entity, "async_set_temperature"):
                    await entity.async_set_temperature(temperature=float(payload))
                elif hasattr(entity, "set_temperature"):
                    entity.set_temperature(temperature=float(payload))

            elif command_type == "mode_set":
                # Climate HVAC mode or water heater operation mode
                if hasattr(entity, "async_set_hvac_mode"):
                    # Climate entity
                    await entity.async_set_hvac_mode(payload)
                elif hasattr(entity, "set_hvac_mode"):
                    entity.set_hvac_mode(payload)
                elif hasattr(entity, "async_set_operation_mode"):
                    # Water heater entity
                    await entity.async_set_operation_mode(payload)
                elif hasattr(entity, "set_operation_mode"):
                    entity.set_operation_mode(payload)

        except Exception as e:
            _LOGGER.error(
                f"Error routing command to {getattr(entity, 'entity_id', 'unknown')}: {e}"
            )
            _LOGGER.exception("Full traceback:")

    def _find_entity(
        self, entity_id: str, object_id: Optional[str] = None
    ) -> Optional[Any]:
        """Find an entity by ID.

        Args:
            entity_id: The full entity ID (e.g., 'switch.living_room').
            object_id: Optional MQTT-safe object_id for matching entities with
                      complex unique_ids that have dots (e.g., from flightradar24).
        """
        domain = entity_id.split(".")[0]
        _LOGGER.debug(f"_find_entity: looking for {entity_id} in domain {domain}")

        entities = self._integration_loader.get_entities(domain)
        _LOGGER.debug(
            f"_find_entity: got {len(entities) if entities else 0} entities for domain {domain}"
        )
        if entities:
            for entity in entities:
                entity_entity_id = getattr(entity, "entity_id", None)
                _LOGGER.debug(f"  Checking entity: {entity_entity_id}")

                # Check exact entity_id match
                if entity_entity_id == entity_id:
                    return entity

                # Check MQTT-safe object_id match if provided
                if object_id and hasattr(entity, "mqtt_object_id"):
                    if entity.mqtt_object_id == object_id:
                        _LOGGER.debug(f"    Matched by mqtt_object_id: {object_id}")
                        return entity

                # For Dyson and other integrations that use the original object_id format,
                # check if the entity_id ends with the object_id (case-insensitive)
                if object_id and entity_entity_id:
                    entity_object_id = entity_entity_id.split(".")[-1]
                    # Normalize both: replace dashes with underscores for comparison
                    normalized_entity = entity_object_id.replace("-", "_").lower()
                    normalized_object = object_id.replace("-", "_").lower()
                    if normalized_entity == normalized_object:
                        _LOGGER.debug(
                            f"    Matched by normalized object_id: {object_id}"
                        )
                        return entity

        return None

    def _on_enable_integration(
        self, client: Client, userdata: Any, message: MQTTMessage
    ) -> None:
        """Handle integration enable command."""
        try:
            topic = message.topic
            domain = topic.split("/")[-2]
            self._hass.async_run_job(
                self._integration_manager.enable_integration, domain
            )
        except Exception as e:
            _LOGGER.error(f"Error enabling integration: {e}")

    def _on_disable_integration(
        self, client: Client, userdata: Any, message: MQTTMessage
    ) -> None:
        """Handle integration disable command."""
        try:
            topic = message.topic
            domain = topic.split("/")[-2]
            self._hass.async_run_job(self._disable_integration, domain)
        except Exception as e:
            _LOGGER.error(f"Error disabling integration: {e}")

    async def _disable_integration(self, domain: str) -> None:
        """Disable and unload an integration."""
        # First unload all config entries to stop coordinators
        entries = self._hass.config_entries.async_entries(domain)
        if entries:
            _LOGGER.info(
                f"Unloading {len(entries)} config entries for disabled integration {domain}"
            )
            for entry in entries:
                await self._integration_loader.unload_integration(entry)

        # Then disable in the manager
        await self._integration_manager.disable_integration(domain)

    def _on_update_integration(
        self, client: Client, userdata: Any, message: MQTTMessage
    ) -> None:
        """Handle integration update command."""
        try:
            topic = message.topic
            domain = topic.split("/")[-2]
            self._hass.async_run_job(self._update_integration, domain)
        except Exception as e:
            _LOGGER.error(f"Error updating integration: {e}")

    async def _update_integration(self, domain: str) -> None:
        """Update an integration to the latest version."""
        info = self._integration_manager.get_integration(domain)
        if not info or not info.update_available:
            return

        # Unload first
        entries = self._hass.config_entries.async_entries(domain)
        for entry in entries:
            await self._integration_loader.unload_integration(entry)

        # Determine the correct identifier for installation
        # For HACS default repos, we need the full_name (owner/repo)
        # For custom repos, we can use the domain
        full_name = info.full_name

        # If full_name is missing (existing integration from before this field was added),
        # try to resolve it by matching repository_url
        if info.source == "hacs_default" and not full_name:
            _LOGGER.debug(f"Resolving full_name for {domain} from repository_url")
            full_name = self._integration_manager.resolve_full_name_by_url(
                info.repository_url
            )
            if full_name:
                # Store it for future updates
                self._integration_manager.update_integration_field(
                    domain, full_name=full_name
                )
                _LOGGER.info(f"Resolved and stored full_name for {domain}: {full_name}")

        if info.source == "hacs_default" and full_name:
            install_target = full_name
            source = "hacs_default"
        elif info.source == "custom":
            install_target = domain
            source = "custom"
        else:
            _LOGGER.error(
                f"Cannot update {domain}: unable to resolve repository (source={info.source}, full_name={full_name})"
            )
            return

        # Install new version (blocking wait for update completion)
        success = await self._integration_manager.install_integration(
            install_target, version=info.latest_version, source=source, wait=True
        )

        if success:
            # Clear update status for this integration
            info.update_available = False
            info.latest_version = None
            self._integration_manager._save_integrations()

            # Reload
            for entry in entries:
                await self._integration_loader.setup_integration(entry)

            _LOGGER.info(f"Successfully updated {domain}")

            # Re-check updates and republish to clear the MQTT topic
            updates = await self._integration_manager.check_for_updates()
            await self._publish_update_notification(updates)
        else:
            _LOGGER.error(f"Failed to update {domain} to {info.latest_version}")

    async def _load_enabled_integrations(self) -> None:
        """Load all enabled integrations in parallel with limited concurrency."""
        # Install requirements for ALL integrations first (even disabled)
        # This allows config flows to work for disabled integrations
        all_integrations = self._integration_manager.get_all_integrations()
        _LOGGER.info(
            f"Installing requirements for {len(all_integrations)} integrations"
        )
        for info in all_integrations:
            _LOGGER.info(f"Installing requirements for {info.domain}")
            requirements_success = await self._integration_manager.install_requirements(
                info.domain
            )
            if not requirements_success:
                _LOGGER.error(f"Failed to install requirements for {info.domain}")

        # Now load only the enabled integrations in parallel
        enabled = self._integration_manager.get_enabled_integrations()
        _LOGGER.info(f"Loading {len(enabled)} enabled integrations in parallel")

        # Collect all setup tasks
        setup_tasks = []
        task_info = []  # Track (domain, entry_id) for logging

        for info in enabled:
            _LOGGER.debug(f"Preparing to load enabled integration: {info.domain}")

            # Get config entries for this integration
            entries = self._hass.config_entries.async_entries(info.domain)

            if not entries and not info.config_flow:
                # Create a default entry if none exists and integration doesn't require config
                entry = ConfigEntry(
                    entry_id=f"{info.domain}_default",
                    version=1,
                    domain=info.domain,
                    title=info.name,
                    data={},
                )
                await self._hass.config_entries.async_add(entry)
                entries = [entry]

            # Create a setup task for each entry (will be run in parallel with limited concurrency)
            for entry in entries:
                setup_tasks.append(self._setup_integration_with_semaphore(entry))
                task_info.append((info.domain, entry.entry_id))

        # Run all setups in parallel (semaphore limits concurrency to 5)
        if setup_tasks:
            _LOGGER.info(
                f"Starting parallel setup of {len(setup_tasks)} config entries "
                f"(max 5 concurrent)"
            )
            results = await asyncio.gather(*setup_tasks, return_exceptions=True)

            # Log results
            success_count = 0
            for (domain, entry_id), result in zip(task_info, results):
                if isinstance(result, Exception):
                    _LOGGER.error(f"Failed to setup {domain} ({entry_id}): {result}")
                elif result:
                    success_count += 1
                    _LOGGER.debug(f"Successfully setup {domain} ({entry_id})")
                else:
                    _LOGGER.error(
                        f"Failed to setup {domain} ({entry_id}) - returned False"
                    )

            _LOGGER.info(
                f"Integration loading complete: {success_count}/{len(setup_tasks)} "
                f"config entries loaded successfully"
            )

    async def _setup_integration_with_semaphore(self, entry: ConfigEntry) -> bool:
        """Setup an integration with semaphore-controlled concurrency.

        Args:
            entry: The config entry to setup.

        Returns:
            True if setup succeeded, False otherwise.
        """
        async with self._setup_semaphore:
            return await self._integration_loader.setup_integration(entry)

    async def _initial_update_check(self) -> None:
        """Check for updates on startup and send notification if any found.

        This runs in the background immediately after startup to notify
        users of available updates without waiting for the periodic check.
        """
        try:
            # Give integrations a moment to fully initialize
            await asyncio.sleep(5)

            if not self._running:
                return

            _LOGGER.info("Running initial update check")
            updates = await self._integration_manager.check_for_updates()

            if updates:
                _LOGGER.info(f"Found {len(updates)} available updates on startup")
                # Publish update entities to HA's Settings > Updates panel
                await self._publish_update_notification(updates)
            else:
                _LOGGER.debug("No updates available on startup")

        except Exception as e:
            _LOGGER.debug(f"Initial update check failed: {e}")

    async def _periodic_update_checks(self) -> None:
        """Periodically check for integration updates."""
        while self._running:
            try:
                await asyncio.sleep(86400)  # Check once per day

                if not self._running:
                    break

                _LOGGER.info("Checking for integration updates")
                updates = await self._integration_manager.check_for_updates()

                if updates:
                    _LOGGER.info(f"Found {len(updates)} available updates")
                    # Publish update notification to MQTT
                    await self._publish_update_notification(updates)

            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Error in update check: {e}")

    async def _publish_update_notification(self, updates: List) -> None:
        """Publish update notification to MQTT.

        Creates a single consolidated MQTT update entity that shows in
        HA's Settings > Updates panel with information about all available updates.
        """
        if not self._mqtt_client:
            return

        await self._publish_consolidated_update_entity(updates)

    async def _publish_consolidated_update_entity(self, updates: List) -> None:
        """Publish a single consolidated MQTT update entity for all Shack updates.

        This creates one native HA update entity that shows in Settings > Updates
        with information about all available Shack integration updates combined.
        """
        if not self._mqtt_client:
            return

        # Get all installed integrations to count them
        all_integrations = self._integration_manager.get_enabled_integrations()
        total_integrations = len(all_integrations)

        # Build update info
        update_count = len(updates)
        has_updates = update_count > 0

        # Build release summary listing all updates
        if has_updates:
            integration_list = "\n\n".join(
                [f"• {u.name}: {u.version} → {u.latest_version}" for u in updates[:10]]
            )
            if update_count > 10:
                integration_list += f"\n\n... and {update_count - 10} more"
            release_summary = integration_list
            title = f"Shack: {update_count} update{'s' if update_count > 1 else ''} available"
        else:
            release_summary = "All Shack integrations are up to date"
            title = "Shack"

        entity_id = "shack_updates"
        discovery_topic = f"homeassistant/update/{entity_id}/config"
        state_topic = f"homeassistant/update/{entity_id}/state"
        command_topic = f"{self._mqtt_base_topic}/updates/install"

        # Get add-on slug for ingress URL (falls back to hardcoded if not available)
        addon_slug = get_addon_slug()
        ingress_path = f"/app/{addon_slug}" if addon_slug else "/app/df3bd192_shack"

        # Discovery config for MQTT update platform
        # Use single state_topic with JSON payload - HA auto-parses JSON
        # Add origin to identify this as an MQTT Discovery entity
        config = {
            "name": "Integration Updates",
            "unique_id": entity_id,
            "state_topic": state_topic,
            "command_topic": command_topic,
            "payload_install": "install",
            "release_url": ingress_path,
            "force_update": True,
            "enabled_by_default": True,
            "origin": {
                "name": "Shack",
                "sw_version": "0.5.20",
                "support_url": "https://github.com/genehand/ha-apps",
            },
            "device": {
                "identifiers": ["shack"],
                "name": "Shack",
                "manufacturer": "Custom",
                "model": "Integration Bridge",
                "suggested_area": "None",
            },
        }

        # Publish discovery config (retained)
        self._mqtt_client.publish(
            discovery_topic, json.dumps(config), qos=0, retain=True
        )

        # Publish state as JSON with all update info
        # installed_version and latest_version must differ for badge to show
        # Use actual version from first update, or placeholder if none
        if has_updates:
            first_update = updates[0]
            installed_version = first_update.version
            latest_ver = first_update.latest_version
        else:
            installed_version = "0.0.0"
            latest_ver = "0.0.0"

        state_payload = {
            "installed_version": installed_version,
            "latest_version": latest_ver,
            "title": title,
            "release_summary": release_summary,
        }

        self._mqtt_client.publish(
            state_topic,
            json.dumps(state_payload),
            qos=0,
            retain=True,
        )

        if has_updates:
            _LOGGER.debug(
                "Published consolidated update entity: %s Shack integration updates available",
                update_count,
            )
        else:
            _LOGGER.debug("Published consolidated update entity: all up to date")

    # Public API for web UI
    def get_hass(self) -> HomeAssistant:
        """Get the HomeAssistant shim instance."""
        return self._hass

    def get_mqtt_bridge(self) -> "MqttBridge":
        """Get the MQTT bridge."""
        return self._mqtt_bridge

    def get_integration_manager(self) -> IntegrationManager:
        """Get the integration manager."""
        return self._integration_manager

    def get_integration_loader(self) -> IntegrationLoader:
        """Get the integration loader."""
        return self._integration_loader

    async def install_integration(self, full_name_or_domain: str, **kwargs):
        """Install an integration.

        Returns InstallTask for async installs or bool for legacy blocking installs.
        """
        result = await self._integration_manager.install_integration(
            full_name_or_domain, **kwargs
        )

        # If it's a task (async install), add a callback to refresh updates when complete
        if hasattr(result, "add_done_callback"):

            async def refresh_on_complete(task):
                try:
                    await task
                    _LOGGER.info("Install completed, refreshing update entity")
                    await self._refresh_update_entity()
                except Exception as e:
                    _LOGGER.error(f"Error refreshing update entity after install: {e}")

            # Wrap in try/except since we can't directly await here
            asyncio.create_task(refresh_on_complete(result))
        else:
            # Synchronous/legacy result - refresh immediately
            if result:
                await self._refresh_update_entity()

        return result

    async def _refresh_update_entity(self) -> None:
        """Re-check for updates and re-publish the update entity."""
        _LOGGER.debug("Refreshing update entity after install")
        updates = await self._integration_manager.check_for_updates()
        await self._publish_update_notification(updates)

    async def _on_updates_found_callback(self, updates: List) -> None:
        """Callback for when periodic update check finds updates.

        This is called by IntegrationManager when it finds updates during
        its periodic check, allowing us to publish to MQTT.
        """
        _LOGGER.info(
            f"Periodic check found {len(updates)} updates, publishing notification"
        )
        await self._publish_update_notification(updates)

    async def create_config_entry(
        self, domain: str, data: dict, options: Optional[dict] = None
    ) -> Optional[ConfigEntry]:
        """Create a new config entry for an integration."""
        info = self._integration_manager.get_integration(domain)
        if not info:
            return None

        # Get unique_id from data if present (config flows often set this)
        unique_id = data.get("unique_id")

        # Domain in IntegrationInfo is already the actual domain from manifest
        # Use timestamp with underscore separator to avoid CSS selector issues
        # (dots and colons in IDs break querySelector)
        import time

        entry_id = f"{domain}_{int(time.time() * 1000)}"

        entry = ConfigEntry(
            entry_id=entry_id,
            version=1,
            domain=domain,
            title=data.get("name", info.name),
            data=data,
            options=options or {},
        )

        # Set unique_id if available (meross_lan and other integrations depend on this)
        if unique_id:
            entry.unique_id = unique_id

        await self._hass.config_entries.async_add(entry)

        # If integration is already enabled, set up the new entry immediately
        if info.enabled:
            await self._integration_loader.setup_integration(entry)

        return entry
