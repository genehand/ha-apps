# AGENTS.md - Home Assistant Apps Repository

This repository contains Home Assistant apps (formerly 'add-ons').

## Repository Structure

| App | Description | Location |
|-----|-------------|----------|
| **app-dasher** | WebSocket proxy for dashboard entities | `app-dasher/` |
| **app-shack** | HACS compatibility layer for running integrations outside HA | `app-shack/` |
| **app-soloist** | Spotify Soloist bridge with track info + playback controls | `app-soloist/` |

## Conventions (all apps)

- **An s6 `run` script's shebang IS the bashio loader: `#!/command/with-contenv bashio`,
  never `#!/command/with-contenv bash`.** Nothing else sources `/usr/lib/bashio`, so with a
  plain `bash` shebang every `bashio::*` call fails at runtime with `command not found` —
  and the script keeps running, so the app appears to start while its logging and config
  reading silently do nothing. Bashio also enables `set -e -o pipefail -o nounset` for the
  whole script, so guard any parse of untrusted output (e.g. `jq`/`bashio::jq` on a
  Supervisor API response) with `|| true`, or an error page aborts startup.
- Each app is supervised by s6-overlay, so a service needs
  `rootfs/etc/s6-overlay/s6-rc.d/<service>/{run,type,notification-fd,finish,dependencies}`
  (service names here: `dasher`, `shack`, `soloist` — note these are *not* the
  `app-` prefixed slugs) **plus** an entry named after the service in
  `rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/`. A new app that is missing the
  `contents.d` entry is never started.

## Per-App Documentation

Each app has its own `AGENTS.md` file with specific build instructions, testing commands, and coding guidelines:

- **Dasher**: See `app-dasher/AGENTS.md`
- **HACS Shack**: See `app-shack/AGENTS.md`
- **Soloist**: See `app-soloist/AGENTS.md`