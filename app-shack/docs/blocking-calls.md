# Blocking Call Detection

Home Assistant raises a `RuntimeError` for blocking calls on the event loop. The shim takes a gentler approach - it warns instead, allowing integrations to keep working while highlighting potential issues during development.

## How It Works

The `shim/block_async_io.py` module monkey-patches known blocking functions (file I/O, `time.sleep`, `importlib`, `HTTPConnection`) at startup. When one of these functions is called from the event loop thread, the wrapper:

1. Walks the call stack to find the caller
2. Logs a `WARNING` with a full traceback (first occurrence)
3. Logs `DEBUG` for repeat occurrences (deduplication by file + line)
4. Executes the original function normally — **never raises**

```
2026-05-07 17:26:49 WARNING: shim.block_async_io - Detected blocking call to
open with args (PosixPath('data/shim/application_credentials.json'), 'r')
inside the event loop at .../application_credentials.py, line 56:
    with open(self._storage_file, "r") as f:.
...
Traceback (most recent call last):
  ...
```

## Monitored Functions

The same 19 functions that Home Assistant monitors:

| Category | Functions | `check_allowed` |
|----------|-----------|-----------------|
| **HTTP** | `HTTPConnection.putrequest` | None |
| **Sleep** | `time.sleep` | Skips calls from `pydevd.py` (debugger) |
| **Glob** | `glob.glob`, `glob.iglob` | None |
| **Directory** | `os.walk`, `os.listdir`, `os.scandir` | None |
| **File I/O** | `builtins.open`, `Path.open`, `Path.read_text`, `Path.read_bytes`, `Path.write_text`, `Path.write_bytes` | Skips `/proc` paths and calls from Jinja2's template loader |
| **Import** | `importlib.import_module` | Skips already-imported modules and calls from uvicorn's internal importer |
| **SSL** | `SSLContext.load_default_certs`, `load_verify_locations`, `load_cert_chain`, `set_default_verify_paths` | Skips `cadata`-only calls to `load_verify_locations` |

## Activation

Blocking call detection is enabled automatically during startup by `ImportPatcher.patch()` in `shim/import_patch.py`. No manual setup required.

```python
# In import_patch.py — called once during bootstrap
block_async_io.enable()
```

### Framework Calls That Are Allowed

Some internal framework imports and file reads are expected on the event loop and are silently allowed:

| Caller | Reason |
|--------|--------|
| `uvicorn/importer.py` | Uvicorn's own module loading during server startup |
| `jinja2/loaders.py` | Template file reads during HTTP request rendering |

These are suppressed via `check_allowed` predicates that inspect the caller's stack frame.

## Design Differences from HA

| Aspect | Home Assistant | Shim |
|--------|---------------|------|
| **Behavior** | Raises `RuntimeError` (hard crash) | Logs `WARNING` (continues) |
| **`strict` parameter** | Per-function toggle | Always warn-only |
| **Stack attribution** | `get_integration_frame()` (identifies custom integration) | `sys._getframe(2)` (reports file + line) |
| **Deduplication** | `(integration, file, line)` | `(file, line)` |

## Fixing Blocking Call Warnings

When you see a `WARNING: shim.block_async_io - Detected blocking call to
open ...` in the logs, the call stack points to sync file I/O (or
`time.sleep`, `importlib`, etc.) running on the event loop. Fix it with
`asyncio.to_thread()`:

### Pattern: sync primitive + async wrapper

Keep the I/O function synchronous (so it can be used from `__init__`
and tests), then wrap it at the async call site:

```python
class MyManager:
    def _save_data(self, data: dict) -> None:
        """Sync I/O primitive — safe from __init__ and tests."""
        tmp = self._path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:           # blocking, but runs in a thread
            json.dump(data, f)
        tmp.rename(self._path)

    async def _async_save(self, data: dict) -> None:
        """Async-safe wrapper for route handlers and event-loop code."""
        await asyncio.to_thread(self._save_data, data)
```

### When to use `asyncio.to_thread`

| Call site | Approach |
|-----------|----------|
| `__init__`, sync tests | Call the sync method directly (no event loop running) |
| Route handlers (`async def`) | `await asyncio.to_thread(self._sync_method, ...)` |
| Background `asyncio.Task` | Same — wrap the I/O call |
| Integration code (`async_setup_entry`) | Not our code — fix in the shim layer, not the integration |

### Existing examples

| Module | Sync primitive | Async wrapper |
|--------|----------------|---------------|
| `shim/storage.py` | `save_entity_states()` | `async_save_entity_states()` |
| `shim/github/auth.py` | `_save_token()` | called via `asyncio.to_thread()` in `_wait_for_activation()` |
| `shim/github/auth.py` | `_delete_token_file()` | `async_clear_token()` |

### The `storage.py` allow-list

`_check_file_allowed()` in `block_async_io.py` silently allows file I/O
from `shim/storage.py` (line 94). This is a legacy allow-list for the
storage layer's own JSON methods. **New modules should not rely on this**
— they should use `asyncio.to_thread()` explicitly.

## Code Reference

| Component | File |
|-----------|------|
| Detection module | `shim/block_async_io.py` |
| Activation | `shim/import_patch.py` (called from `ImportPatcher.patch()`) |
| Tests | `tests/test_block_async_io.py` |
