# AGENTS.md - Home Assistant Apps Repository

This repository contains three Home Assistant-related applications.

## Repository Structure

| App | Description | Location |
|-----|-------------|----------|
| **app-dasher-rust** | WebSocket event proxy for dashboard entities (current) | `app-dasher-rust/` |
| **app-dasher** | WebSocket event proxy (legacy Python) | `app-dasher/` |
| **app-shack** | HACS compatibility layer for running integrations outside HA | `app-shack/` |

## Per-App Documentation

Each app has its own `AGENTS.md` file with specific build instructions, testing commands, and coding guidelines:

- **Rust implementation**: See `app-dasher-rust/AGENTS.md`
- **Legacy Python**: See `app-dasher/AGENTS.md`
- **HACS Shack**: See `app-shack/AGENTS.md`