"""Home Assistant Supervisor API helpers.

Provides utilities for communicating with the HA Supervisor when running
as an add-on (e.g., fetching the external URL for OAuth redirect URIs).
"""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional


async def fetch_ha_external_url() -> Optional[str]:
    """Fetch the Home Assistant external_url from the Supervisor API.

    Queries the Supervisor-proxied HA config endpoint to get the
    configured external URL. Returns None if not running as an addon
    (no SUPERVISOR_TOKEN) or if the API is unreachable.

    Returns:
        The external URL string (e.g. 'https://myha.example.com')
        or None if unavailable.
    """
    logger = logging.getLogger("shack.supervisor")

    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        logger.debug("SUPERVISOR_TOKEN not set, not running as add-on")
        return None

    def _query(path: str):
        url = f"http://supervisor{path}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        result = await asyncio.to_thread(_query, "/core/api/config")
        external_url = result.get("external_url") if result else None
        if external_url:
            logger.debug("HA external_url from Supervisor: %s", external_url)
        else:
            logger.debug("HA config has no external_url set")
        return external_url
    except Exception as e:
        logger.error("Failed to fetch HA config from Supervisor: %s", e)
        return None
