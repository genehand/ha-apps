# 🏚️ HACS Shack

This app creates a shim environment that lets you run [HACS](https://www.hacs.xyz/) integrations separately from Home Assistant and publishes standalone MQTT entities.

## Features

- **HACS Integration Support**: Install and run HACS integrations from the official repository
- **MQTT Auto-Discovery**: Entities automatically appear in Home Assistant via MQTT discovery
- **Config Flow Support**: Guided setup for integrations that require configuration
- **Auto-Updates**: Automatic update checking for installed integrations
- **Multiple Entity Platforms**: Support for sensors, switches, fans, lights, climate, binary sensors, etc

## Architecture

The shim provides a compatibility layer that:

- Shims Home Assistant components so integrations run like they would inside HA
- Translates entity state changes to MQTT messages
- Handles MQTT command topics and routes them to entity methods
- Manages integration lifecycle (install, enable, disable, update)

## Installation

### As Home Assistant App

1. Add this repository to your Home Assistant app store
2. Install the "Shack" add-on
3. Install the Mosquitto MQTT broker add-on if not already installed
4. Configure MQTT credentials in the add-on options
5. Start the add-on
6. Access the Web UI at `http://homeassistant.local:8080`

## Using the Web UI

1. **Browse Integrations**: View available HACS integrations from the repository
2. **Install**: Click install to download and set up an integration
3. **Configure**: Use the config flow UI to set up credentials and options
4. **Manage**: Enable/disable integrations, check for updates, or remove
5. **Monitor**: View entity status and MQTT topic subscriptions

## Supported Entity Platforms

HACS Shack aims to support all entity platforms supported by the [Home Assistant MQTT integration](https://www.home-assistant.io/integrations/mqtt/).

## How It Works

### Import Patching

The shim patches Python's import system to intercept Home Assistant's:

```python
# Integration code:
from homeassistant.components.sensor import SensorEntity

# Shim intercepts and provides:
from shim.platforms.sensor import SensorEntity
```

### Entity Lifecycle

1. **Discovery**: Integration creates entities → Shim publishes MQTT discovery config
2. **State Updates**: Entity reports state change → Shim publishes to MQTT state topic
3. **Commands**: HA sends command via MQTT → Shim routes to entity method
4. **Cleanup**: Integration unloaded → Shim removes MQTT topics

## Adding New Integrations

Many HACS integrations work out of the box. To add a new integration:

1. Search for it in the Web UI integration browser
2. Click Install to download from HACS repository
3. If the integration requires config flow, complete the setup wizard
4. Entities should appear in Home Assistant automatically

For integrations that need special handling, see the `shim/` directory for platform implementations.