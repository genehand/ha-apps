"""Integration Loader for Home Assistant Shim.

Dynamically loads and runs HACS integrations with import patching.
"""

import sys
import asyncio
import importlib
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from ..logging import get_logger, set_current_integration
from ..core import HomeAssistant, ConfigEntry
from ..entity import EntityRegistry
from ..import_patch import setup_import_patching
from ..options_map import patch_select_descriptions
from .manager import IntegrationManager, IntegrationInfo

_LOGGER = get_logger(__name__)


class IntegrationLoader:
    """Loads and manages running integrations."""

    def __init__(self, hass: HomeAssistant, integration_manager: IntegrationManager):
        self._hass = hass
        self._integration_manager = integration_manager
        self._loaded_integrations: Dict[str, Any] = {}  # domain -> module
        self._entities: Dict[str, List] = {}  # domain -> [entities]

        # Setup import patching
        self._patcher = setup_import_patching(hass)
        self._patcher.patch()

        # Setup entity registry
        self._entity_registry = EntityRegistry()
        self._entity_registry.setup(hass)

    async def load_integration(self, domain: str) -> bool:
        """Load an integration module.

        Args:
            domain: The domain of the integration (from manifest, e.g., 'dyson_local')
        """
        # Check if already loaded
        if domain in self._loaded_integrations:
            _LOGGER.debug(f"Integration {domain} already loaded")
            return True

        info = self._integration_manager.get_integration(domain)
        if not info:
            _LOGGER.error(f"Integration {domain} not found")
            return False

        integration_path = self._integration_manager.get_integration_path(domain)
        if not integration_path:
            _LOGGER.error(f"Integration {domain} files not found")
            return False

        # custom_components is the parent directory of the integration
        custom_components_path = integration_path.parent

        try:
            set_current_integration(domain)
            _LOGGER.info(f"Loading integration {domain}")

            # Ensure persistent packages are in sys.path (for container mode)
            # This needs to happen before importing the integration
            persistent_path = None
            if (
                hasattr(self._integration_manager, "_persistent_packages_dir")
                and self._integration_manager._persistent_packages_dir
            ):
                persistent_path = str(
                    self._integration_manager._persistent_packages_dir
                )

            if persistent_path:
                # Cache invalidation is needed to ensure newly installed packages
                # (installed after manager init) are discoverable when loading
                importlib.invalidate_caches()
                _LOGGER.debug(f"Invalidated import cache for {domain}")

            # Check for __init__.py
            init_file = custom_components_path / "__init__.py"
            if not init_file.exists():
                init_file.write_text("# custom_components package\n")
                _LOGGER.info(f"Created {init_file}")

            # Add the PARENT of custom_components to Python path
            # so we can import 'custom_components.domain'
            parent_path = custom_components_path.parent
            if str(parent_path) not in sys.path:
                sys.path.insert(0, str(parent_path))
                # Invalidate import cache since we added a new path
                importlib.invalidate_caches()
                _LOGGER.debug(f"Added {parent_path} to sys.path")

            # Import the integration
            _LOGGER.debug(
                f"Importing custom_components.{domain} from path: {custom_components_path}"
            )
            module = importlib.import_module(f"custom_components.{domain}")
            self._loaded_integrations[domain] = module

            # Patch select descriptions to add options_map for display value translation
            # Pass the integration path so we can load translations
            patch_select_descriptions(domain, module, integration_path)

            _LOGGER.debug(f"Successfully loaded integration {domain}")
            return True

        except Exception as e:
            _LOGGER.error(f"Failed to load integration {domain}: {e}")
            # Log additional diagnostic information
            _LOGGER.debug(f"sys.path at time of error: {sys.path}")
            if persistent_path:
                _LOGGER.debug(f"Expected persistent packages path: {persistent_path}")
                if Path(persistent_path).exists():
                    # List some packages to verify they exist
                    try:
                        packages = list(Path(persistent_path).iterdir())[:10]
                        _LOGGER.debug(
                            f"Packages in persistent path: {[p.name for p in packages]}"
                        )
                    except Exception as list_e:
                        _LOGGER.debug(f"Could not list persistent packages: {list_e}")
                else:
                    _LOGGER.warning(
                        f"Persistent packages path does not exist: {persistent_path}"
                    )
            return False
        finally:
            set_current_integration(None)

    async def setup_integration(self, entry: ConfigEntry) -> bool:
        """Setup an integration with a config entry."""
        domain = entry.domain

        # Load the integration first
        if not await self.load_integration(domain):
            return False

        try:
            set_current_integration(domain)
            _LOGGER.info(f"Setting up {domain} with entry {entry.entry_id}")

            module = self._loaded_integrations[domain]

            # Call async_setup first (initializes hass.data[domain] and global state)
            if hasattr(module, "async_setup"):
                _LOGGER.debug(f"Calling async_setup for {domain}")
                setup_result = await module.async_setup(self._hass, {})
                if not setup_result:
                    _LOGGER.warning(f"Integration {domain} async_setup returned False")

            # Check for async_setup_entry
            if not hasattr(module, "async_setup_entry"):
                _LOGGER.error(f"Integration {domain} missing async_setup_entry")
                return False

            # Call setup with config entry context variable set
            # This allows DataUpdateCoordinator to auto-detect the config entry
            from homeassistant.config_entries import current_entry

            token = current_entry.set(entry)
            try:
                result = await module.async_setup_entry(self._hass, entry)
            finally:
                current_entry.reset(token)

            if result:
                _LOGGER.debug(f"Successfully setup {domain}")
                entry.state = "loaded"
                return True
            else:
                _LOGGER.error(f"Integration {domain} setup returned False")
                entry.state = "setup_error"
                return False

        except Exception as e:
            _LOGGER.error(f"Failed to setup integration {domain}: {e}")
            return False
        finally:
            set_current_integration(None)

    async def unload_integration(
        self, entry: ConfigEntry, cleanup_mqtt: bool = True
    ) -> bool:
        """Unload an integration.

        Args:
            entry: The config entry to unload.
            cleanup_mqtt: Whether to clean up MQTT topics. Set to False during
                         server shutdown to preserve topics for reconnection.
        """
        domain = entry.domain

        if domain not in self._loaded_integrations:
            return True

        try:
            set_current_integration(domain)
            _LOGGER.info(f"Unloading {domain}")

            module = self._loaded_integrations[domain]

            # Check for async_unload_entry
            if hasattr(module, "async_unload_entry"):
                result = await module.async_unload_entry(self._hass, entry)
                if not result:
                    _LOGGER.warning(f"Integration {domain} unload returned False")

            # Force shutdown any remaining coordinators for this domain
            # This handles integrations that don't properly clean up coordinators
            try:
                from homeassistant.helpers.update_coordinator import (
                    _shutdown_coordinators_for_domain,
                )

                _shutdown_coordinators_for_domain(domain)
            except ImportError:
                pass

            # Remove all entities for this domain
            await self._remove_domain_entities(domain, cleanup_mqtt=cleanup_mqtt)

            # Call async_unload for global cleanup if no more entries
            entries = self._hass.config_entries.async_entries(domain)
            if len(entries) <= 1 and hasattr(module, "async_unload"):
                _LOGGER.debug(f"Calling async_unload for {domain}")
                await module.async_unload(self._hass)

            # Remove from loaded
            del self._loaded_integrations[domain]

            entry.state = "not_loaded"
            _LOGGER.info(f"Successfully unloaded {domain}")
            return True

        except Exception as e:
            _LOGGER.error(f"Failed to unload integration {domain}: {e}")
            return False
        finally:
            set_current_integration(None)

    async def _remove_domain_entities(
        self, domain: str, cleanup_mqtt: bool = True
    ) -> None:
        """Remove all entities for an integration domain.

        Args:
            domain: The integration domain to remove entities for.
            cleanup_mqtt: Whether to clean up MQTT topics.
        """
        entities_to_remove = []
        total_entities = sum(len(entities) for entities in self._entities.values())
        _LOGGER.debug(
            f"_remove_domain_entities: domain={domain}, total entities tracked={total_entities}, cleanup_mqtt={cleanup_mqtt}"
        )

        for platform_domain, entities in list(self._entities.items()):
            for entity in list(entities):
                entity_domain = getattr(entity, "integration_domain", None)
                _LOGGER.debug(
                    f"  Checking entity {entity.entity_id}: integration_domain={entity_domain}"
                )
                if entity_domain == domain:
                    entities_to_remove.append((platform_domain, entity))

        _LOGGER.info(
            f"Found {len(entities_to_remove)} entities to remove for domain {domain}"
        )

        for platform_domain, entity in entities_to_remove:
            try:
                _LOGGER.debug(f"Removing entity {entity.entity_id}")
                await entity.async_remove(cleanup_mqtt=cleanup_mqtt)
                self._entities[platform_domain].remove(entity)
                _LOGGER.debug(f"Successfully removed entity {entity.entity_id}")
            except Exception as e:
                _LOGGER.warning(f"Error removing entity {entity.entity_id}: {e}")

        # Clean up empty platform lists
        for platform_domain in list(self._entities.keys()):
            if not self._entities[platform_domain]:
                del self._entities[platform_domain]
                _LOGGER.debug(f"Cleaned up empty platform list: {platform_domain}")

    async def remove_config_entry(self, entry: ConfigEntry) -> bool:
        """Remove a specific config entry and its entities."""
        domain = entry.domain

        try:
            set_current_integration(domain)
            _LOGGER.info(f"Removing config entry {entry.entry_id} for {domain}")

            # Unload the entry first
            await self.unload_integration(entry)

            # Remove the config entry from storage
            await self._hass.config_entries.async_remove(entry.entry_id)

            _LOGGER.info(f"Successfully removed config entry {entry.entry_id}")
            return True

        except Exception as e:
            _LOGGER.error(f"Failed to remove config entry {entry.entry_id}: {e}")
            return False
        finally:
            set_current_integration(None)

    def register_entity(self, domain: str, entity) -> None:
        """Register an entity created by an integration."""
        if domain not in self._entities:
            self._entities[domain] = []

        self._entities[domain].append(entity)
        _LOGGER.debug(f"Registered entity {entity.entity_id} for {domain}")

    def get_entities(
        self, domain: Optional[str] = None, integration_domain: Optional[str] = None
    ) -> List:
        """Get all entities or entities for a specific domain.

        Args:
            domain: Platform domain (e.g., 'fan', 'sensor') for MQTT routing
            integration_domain: Integration domain (e.g., 'dyson_local') for web UI filtering
        """
        if integration_domain:
            # Filter by integration domain across all platform domains
            all_entities = []
            for entities in self._entities.values():
                for entity in entities:
                    if (
                        hasattr(entity, "integration_domain")
                        and entity.integration_domain == integration_domain
                    ):
                        all_entities.append(entity)
            return all_entities

        if domain:
            return self._entities.get(domain, [])

        all_entities = []
        for entities in self._entities.values():
            all_entities.extend(entities)
        return all_entities

    def get_loaded_integrations(self) -> List[str]:
        """Get list of loaded integration domains."""
        return list(self._loaded_integrations.keys())

    async def reload_all(self) -> None:
        """Reload all enabled integrations."""
        _LOGGER.info("Reloading all integrations")

        # Unload all
        for domain in list(self._loaded_integrations.keys()):
            entry = self._hass.config_entries.async_get_entry(domain)
            if entry:
                await self.unload_integration(entry)

        # Reload all enabled
        enabled = self._integration_manager.get_enabled_integrations()
        for info in enabled:
            entries = self._hass.config_entries.async_entries(info.domain)
            for entry in entries:
                await self.setup_integration(entry)

    async def start_config_flow(self, domain: str) -> Optional[dict]:
        """Start a config flow for an integration."""
        # Load the integration first
        if not await self.load_integration(domain):
            return None

        try:
            set_current_integration(domain)

            module = self._loaded_integrations[domain]

            # Check for config flow
            if not hasattr(module, "async_setup_entry"):
                _LOGGER.error(f"Integration {domain} missing async_setup_entry")
                return None

            # Check manifest for config_flow support
            manifest = self._integration_manager._load_manifest(domain)
            if not manifest or not manifest.get("config_flow", False):
                _LOGGER.error(f"Integration {domain} has no config flow (manifest)")
                return None

            # Import config_flow module
            try:
                config_flow_module = importlib.import_module(
                    f"custom_components.{domain}.config_flow"
                )
            except ImportError as e:
                _LOGGER.error(f"Failed to import config_flow for {domain}: {e}")
                return None

            # Get the ConfigFlow class
            from ..config_entries import ConfigFlow

            config_flow_class = None

            # Look for ConfigFlow subclass in the config_flow module
            for attr_name in dir(config_flow_module):
                attr = getattr(config_flow_module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, ConfigFlow)
                    and attr is not ConfigFlow
                ):
                    config_flow_class = attr
                    break

            if not config_flow_class:
                _LOGGER.error(f"Could not find ConfigFlow class for {domain}")
                return None

            # Create flow instance
            flow = config_flow_class()
            flow.hass = self._hass
            flow.handler = domain

            # Generate flow ID
            flow_id = self._hass.config_entries.async_create_flow(domain)
            flow.flow_id = flow_id

            # Store the flow object (not just the dict) so we can continue it later
            self._hass.config_entries._flow_progress[flow_id] = flow

            # Start the flow
            result = await flow.async_step_user(None)
            result["flow_id"] = flow_id

            return result

        except Exception as e:
            _LOGGER.error(f"Failed to start config flow for {domain}: {e}")
            return None
        finally:
            set_current_integration(None)

    async def continue_config_flow(
        self, domain: str, flow_id: str, user_input: dict
    ) -> Optional[dict]:
        """Continue a config flow with user input."""
        try:
            set_current_integration(domain)

            # Get the flow from hass
            flow = self._hass.config_entries._flow_progress.get(flow_id)
            if not flow:
                _LOGGER.error(f"Flow {flow_id} not found")
                return None

            # Get the current step_id (defaults to 'user' for initial step)
            step_id = getattr(flow, "cur_step_id", "user")

            # Handle menu selection - if next_step is present, call that step method
            if user_input and "next_step" in user_input:
                step_id = user_input["next_step"]
                # Update the flow's current step
                flow.cur_step_id = step_id
                # Call the next step method with None (first call to show form)
                step_method_name = f"async_step_{step_id}"
                step_method = getattr(flow, step_method_name, None)

                if step_method is None:
                    _LOGGER.error(
                        f"Step method {step_method_name} not found for {domain}"
                    )
                    return None

                result = await step_method(None)
            else:
                # Get the step method dynamically
                step_method_name = f"async_step_{step_id}"
                step_method = getattr(flow, step_method_name, None)

                if step_method is None:
                    _LOGGER.error(
                        f"Step method {step_method_name} not found for {domain}"
                    )
                    return None

                # Call the appropriate step method
                # If user_input is empty, pass None to show the form
                if not user_input:
                    result = await step_method(None)
                else:
                    result = await step_method(user_input)

            result["flow_id"] = flow_id
            return result

        except Exception as e:
            _LOGGER.error(f"Failed to continue config flow for {domain}: {e}")
            return None
        finally:
            set_current_integration(None)
