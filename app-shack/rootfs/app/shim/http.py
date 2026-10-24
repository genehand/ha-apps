"""Bounded aiohttp session defaults for github.com traffic.

GitHub sends a single ``Content-Security-Policy`` response header of ~9.2 KiB
on ``github.com`` responses (archive downloads, ``/login/device/code``, HTML
pages). aiohttp's parser defaults cap a header field at 8190 bytes, so the
response raises ``ClientResponseError: 400, message='Got more than 8190 bytes
when reading: b"default-src 'none'; ..."'`` *before* the redirect to
``codeload.github.com`` can be followed.

That breaks two shim flows:

- integration install/update (``https://github.com/<owner>/<repo>/archive/...``)
- GitHub device-flow login (``https://github.com/login/device/code``)

We raise both parser limits, keeping them bounded (64 KiB) rather than
disabling the check, mirroring HACS's fix in hacs/integration#5644.
"""

import aiohttp

# Bounded, but comfortably above GitHub's ~9.2 KiB CSP header.
GITHUB_MAX_LINE_SIZE = 16 * 1024
GITHUB_MAX_FIELD_SIZE = 64 * 1024


def github_session(**kwargs) -> aiohttp.ClientSession:
    """Create a ClientSession that tolerates GitHub's oversized headers.

    ``kwargs`` are forwarded to :class:`aiohttp.ClientSession`, so a caller
    may override the limits (or pass a connector). The limits are bounded by
    default; ``None`` (aiohttp's "unlimited" sentinel) is never passed.
    """
    kwargs.setdefault("max_line_size", GITHUB_MAX_LINE_SIZE)
    kwargs.setdefault("max_field_size", GITHUB_MAX_FIELD_SIZE)
    return aiohttp.ClientSession(**kwargs)
