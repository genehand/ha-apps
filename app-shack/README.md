# HACS Shack

This app creates a shim environment that lets you run [HACS](https://www.hacs.xyz/) integrations separately from Home Assistant and publishes standalone MQTT entities.

## Features

- **HACS Integration Support**: Install and run HACS integrations from the official repositories
- **MQTT Auto-Discovery**: Entities automatically appear in Home Assistant via MQTT discovery
- **Config Flow Support**: Guided setup for integrations that require configuration
- **Auto-Updates**: Automatic update checking for installed integrations

### Why?

- Mainly to minimize full HA restarts
- Core restarts are faster as well with less dependencies to manage
- Probably minor, but splitting out python threads allows them to run on another core


## Installation

### Home Assistant app

1. Add this repository to your Home Assistant app store
2. Install the "Shack" app
3. Install the Mosquitto MQTT broker if not already installed
4. Start the app
5. Open the Web UI

## Supported Entity Platforms

This aims to support all entity platforms supported by the [MQTT integration](https://www.home-assistant.io/integrations/mqtt/), however not all [command topics](TODO.md) are implemented yet.

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

Several HACS integrations work out of the box, tested ones are marked as `Verified`. To add a new integration:

1. Search for it in the Web UI integration browser
2. Click Install to download from HACS repository
3. If the integration requires config flow, complete the setup wizard
4. Entities should appear in Home Assistant automatically