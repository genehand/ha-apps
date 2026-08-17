# AGENTS.md - Home Assistant Apps Repository

This repository contains Home Assistant apps (formerly 'add-ons').

## Repository Structure

| App | Description | Location |
|-----|-------------|----------|
| **app-dasher** | WebSocket proxy for dashboard entities | `app-dasher/` |
| **app-greenroom** | Spotify Connect monitor for track info without the Web API (deprecated) | `app-greenroom/` |
| **app-shack** | HACS compatibility layer for running integrations outside HA | `app-shack/` |
| **app-soloist** | Spotify Soloist bridge with track info + playback controls | `app-soloist/` |

## Per-App Documentation

Each app has its own `AGENTS.md` file with specific build instructions, testing commands, and coding guidelines:

- **Dasher**: See `app-dasher/AGENTS.md`
- **HACS Shack**: See `app-shack/AGENTS.md`
- **Greenroom**: See `app-greenroom/AGENTS.md`
- **Soloist**: See `app-soloist/AGENTS.md`