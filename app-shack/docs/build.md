# Build Commands

```bash
# Sync dependencies (install from lock file)
cd app-shack/rootfs/app && uv sync

# Release: config.yaml version -> pyproject.toml (+ integration versions from
# data/shim/integrations.json -> metadata/repository_status.json), then uv sync.
# It syncs the same throwaway venv run.sh uses, not the project .venv.
cd app-shack && ./sync-versions.py

# Run it the way it deploys: throwaway venv (UV_PROJECT_ENVIRONMENT), pruned with
# scripts/prune_homeassistant.py, then main.py. Pruning a throwaway venv proves the
# keep-list still matches UPSTREAM_* without disturbing the project .venv, which the
# tests and debugging need whole.
cd app-shack && ./run.sh

# Run the application against the project venv as-is
cd app-shack/rootfs/app && uv run python3 main.py
```

**Important**: Always use `uv sync` and `uv run` to ensure consistent dependencies.
The `.venv` is managed by uv.

`uv sync` **prunes extraneous packages**, and it does so silently: it once removed
`protobuf`/`googleapis-common-protos` that `tests/test_nest_protect_platform_attr.py`
needs, so the suite was green only on machines with a dirty venv. Both are now
declared in the dev group; use `uv sync --inexact` if you install anything ad hoc.

## Docker

`app-shack/Dockerfile` + `build.sh` produce the image; the build finishes by running
the import patcher and importing every `UPSTREAM_HELPER_MODULES` entry, so a stale
keep-list fails the build rather than the app. See [`image-size.md`](image-size.md)
for the prune and the checks that validate a keep-list change without a build.

## s6 service layout

The app runs under s6-overlay: the service is `rootfs/etc/s6-overlay/s6-rc.d/shack/`
(`run`, `type`, `notification-fd`, `finish`, `dependencies`) and it must be listed in
`rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/`. The `run` script's shebang is
`#!/command/with-contenv bashio` — that shebang *is* the bashio loader.
