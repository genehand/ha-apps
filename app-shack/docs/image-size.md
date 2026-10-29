# Image Size and the Keep-List

`homeassistant` installs at ~368 MB, of which `components/` is ~360 MB of
integrations that cannot import without a running `hass`. Both the Docker build and
the s6 `run` script call `rootfs/app/scripts/prune_homeassistant.py`, which deletes
everything outside the keep-list (`KEEP_TOP_LEVEL`, `KEEP_IN_HELPERS`), taking the
package down to ~1.3 MB. `util` and `generated` are kept whole on purpose;
`core.py` is kept for the names `shim.core` re-exports.

The runtime venv is `/data/.venv` (not `/app/.venv`), which is on the user's data
partition — hence the prune in `rootfs/etc/s6-overlay/s6-rc.d/shack/run` as well.
`IntegrationManager` installs integration requirements into, and adds to `sys.path`,
the environment the app actually runs in — it derives that from `sys.prefix` /
`sysconfig.get_path("purelib")`. So `/data/.venv` (addon), the project `.venv` (dev)
and `run.sh`'s throwaway venv all behave, and `uv pip install --python` can never
target a different environment than the interpreter that imports the result.
The Docker build finishes by running the patcher and importing every
`UPSTREAM_HELPER_MODULES` entry, so a keep-list that no longer matches the code
fails the build instead of the app.

The keep-list can also *grow* after a user's `/data/.venv` was pruned by an older
build, and `uv sync --check` will not notice (dist-info intact, files gone). The s6
`run` script probes `homeassistant.core` and `homeassistant.helpers.storage` and
reinstalls the package when either is missing — keep that probe.

## Pruned-image check

Validates a keep-list change without a Docker build:

```bash
cd rootfs/app
rm -rf /tmp/sp && mkdir -p /tmp/sp
cp -R .venv/lib/python3*/site-packages/homeassistant /tmp/sp/
.venv/bin/python scripts/prune_homeassistant.py /tmp/sp
PYTHONPATH=/tmp/sp .venv/bin/python -m pytest tests/ -q -m "not integration"
```

Copying only `homeassistant/` (not all of site-packages) is enough and much faster:
`PYTHONPATH` wins over site-packages.

## End-to-end boot without `/data`

Falls back to `./data`; reaches the MQTT connection and stops there without a
broker, which is expected.

```bash
mkdir -p /tmp/runshack && cd /tmp/runshack
PYTHONPATH=<repo>/app-shack/rootfs/app <repo>/app-shack/rootfs/app/.venv/bin/python \
  <repo>/app-shack/rootfs/app/main.py
```
