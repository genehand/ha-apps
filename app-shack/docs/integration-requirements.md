# Integration Requirements

Integration Python requirements (`manifest.json` → `requirements`) are installed
into the shim's venv at runtime, because HACS integrations are downloaded after
the image is built. In the app container that venv is `/data/.venv`
(`UV_PROJECT_ENVIRONMENT`), so installed packages survive app restarts and app
updates.

`ShimManager._load_enabled_integrations()` calls
`IntegrationManager.install_requirements()` for **every** integration on **every**
start (disabled ones too, so their config flows work). That is why the
"is this already installed?" check has to be exact: anything it gets wrong is
re-installed on every boot.

## How Satisfaction Is Decided

`IntegrationManager._requirement_satisfied()` parses each requirement with
`packaging.requirements.Requirement` and checks:

| Requirement kind | Check |
|---|---|
| `pkg==1.2.3`, `pkg>=1.0,<2`, `pkg[extra]==1.0` | installed version of `pkg` from `importlib.metadata` satisfies the specifier |
| `pkg; python_version<"3.9"` (marker) | skipped entirely when the marker does not match this environment |
| `pkg @ git+https://…`, `pkg @ https://…/x.whl` (direct URL / VCS) | the exact requirement string is in `IntegrationInfo.installed_requirements` |
| anything `packaging` cannot parse (`-e .`) | same record |
| nothing installed | not satisfied |

Only the outstanding requirements are handed to a **single** `uv pip install`
call; when nothing is outstanding, uv is not invoked at all and the import cache
is not invalidated. `installed_requirements` is persisted with the integration
record, so direct-URL requirements (e.g. flightid's
`opensky-api @ git+https://github.com/openskynetwork/opensky-api.git#subdirectory=python`)
are cloned once instead of on every start. If the package disappears from the
venv, the presence check still forces a reinstall.

`install_requirements(domain, force=True)` bypasses the check (repair path).
`uninstall_requirements()` clears the record.

## Why the Version Lookup Is In-Process

The app runs `uv run python main.py` with `UV_PROJECT_ENVIRONMENT=/data/.venv`,
i.e. the very venv requirements are installed into, so `importlib.metadata`
sees them. The previous implementation shelled out to `uv pip show` per
requirement per integration per start; the in-process lookup is what keeps the
startup pass cheap.

It is blocking though — `importlib.metadata.version()` reads the dist-info
`METADATA` file — so `install_requirements()` resolves every version in **one**
`asyncio.to_thread(self._installed_versions, requirements)` hop before the
satisfaction loop. Reading it inline made `shim.block_async_io` warn with
`Detected blocking call to open ... METADATA` during startup.
`tests/test_requirement_install.py::test_version_lookup_runs_off_the_event_loop`
pins the thread hop.

## Known Gap

A requirement that `packaging` cannot parse and that is *not* in the record is
installed on every start until the first successful install records it (after
which it is skipped). Empty-dependency manifests (`requirements: []`) short-
circuit before any check.

## Tests

`tests/test_requirement_install.py` — satisfied specifiers skip uv, direct-URL
requirements install once then skip, URL changes reinstall, one batched install
command, unparsable requirements, `force=True`, failures, uninstall record
clearance, `to_dict()` round-trip.
