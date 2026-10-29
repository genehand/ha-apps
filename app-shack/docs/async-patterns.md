# Async Patterns

```python
async def proxy_handler(request):
    # Use await for async operations
    return await proxy_websocket_filtered(request, client_ip)
```

## Blocking I/O on the event loop

Never call `open()`, `time.sleep()`, `Path.read_text()`, etc. directly
from `async def` code — the blocking-call detector will warn. Keep sync
I/O methods sync (so they work from `__init__` and tests), then wrap
them with `asyncio.to_thread()` at the async call site:

```python
def _save_data(self, data: dict) -> None:
    """Sync I/O primitive — safe from __init__ and tests."""
    with open(self._path, "w") as f:
        json.dump(data, f)

async def _async_save(self, data: dict) -> None:
    """Async-safe wrapper for route handlers and tasks."""
    await asyncio.to_thread(self._save_data, data)
```

See [`blocking-calls.md`](blocking-calls.md#fixing-blocking-call-warnings) for the
full pattern, the detector's warn list, and how to read its output.

## aiohttp sessions that touch `github.com`

GitHub sends a single ~9.2 KiB `Content-Security-Policy` header on every
`github.com` response (archive downloads, `/login/device/code`, HTML pages).
aiohttp's parser defaults cap a header field at **8190 bytes**, so the response
fails with `ClientResponseError: 400, message='Got more than 8190 bytes when
reading: b"default-src 'none'; ..."'` *before* the redirect to
`codeload.github.com` is followed. That is what broke integration installs and
the GitHub device flow (upstream: hacs/integration#5644).

Always build such sessions with `shim.http.github_session()` instead of
`aiohttp.ClientSession()`; it raises `max_line_size`/`max_field_size` to bounded
values (16 KiB / 64 KiB) and forwards any kwargs. `homeassistant.helpers
aiohttp_client` stubs use it too, so integrations that download from GitHub get
the same limits. Keep the limits bounded — never disable the check.

## Job creation

Create tasks with `hass.async_create_task(...)` (or `async_run_hass_job`) rather
than a bare `asyncio.create_task()`, so upstream helpers that schedule work through
`hass` behave the same inside the shim. `HassJobType` identity matters here — see
[`upstream-tier.md`](upstream-tier.md#the-upstream-tier) for why a duplicate enum
silently stops jobs from running.
