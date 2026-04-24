# Dasher

A proxy for Home Assistant to filter state events for dashboard entities.

## What It Does

- Forwards state changes only for entities found on your dashboard
- Reduces bandwidth and improves performance with low-powered devices

## How It Works

Dasher injects a small script into the main page served by Home Assistant. This script:

- Generates a unique ID
- Patches the WebSocket connection to include the ID
- Reports the current panel (e.g. `/lovelace/home`) to the proxy

Filtering is automatically disabled for non-dashboard pages (Settings, Developer tools, etc).

## Quick Start

1. Add this repository to your Home Assistant app store
2. Install and start the **Dasher** app
3. Open `http://<ha-host>:8125`

## Similar Projects

- [ha-ws-proxy](https://github.com/DragonHunter274/homeassistant-entity-filter-proxy) 