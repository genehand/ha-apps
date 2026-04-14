# Dasher

A WebSocket proxy for Home Assistant to filter state events for dashboard entities.

![Dasher logo](/app-dasher/logo.png)

## What It Does

- Only forwards state changes for entities found on your dashboard
- Reduces bandwidth and improves performance with low-powered devices

## Quick Start

1. Add this repository to your Home Assistant app store
2. Install and start the **Dasher** app
3. Open `http://<ha-host>:8125` instead of the main HA port

## Similar Projects

- [ha-ws-proxy](https://github.com/DragonHunter274/homeassistant-entity-filter-proxy) 