#!/usr/bin/env python3
"""Test script for meross_lan profile step."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


async def test_profile_step():
    from shim.core import HomeAssistant
    from shim.import_patch import setup_import_patching
    from shim.integrations.loader import IntegrationLoader
    from shim.integrations.manager import IntegrationManager
    from shim.logging import get_logger
    from shim.storage import Storage

    _LOGGER = get_logger("test")

    data_dir = Path(__file__).parent.parent / "data"
    shim_dir = data_dir / "shim"

    hass = HomeAssistant(data_dir)
    patcher = setup_import_patching(hass)
    patcher.patch()

    storage = Storage(shim_dir)
    integration_manager = IntegrationManager(storage, shim_dir)
    loader = IntegrationLoader(hass, integration_manager)
    hass.data["integration_loader"] = loader

    # Start config flow
    result = await loader.start_config_flow("meross_lan")
    print(f"Menu result type: {result.get('type')}")
    print(f"Menu options: {result.get('menu_options')}")

    # Click profile
    flow_id = result["flow_id"]
    print(f"\nClicking profile (flow_id: {flow_id})...")
    result2 = await loader.continue_config_flow(
        "meross_lan", flow_id, {"next_step": "profile"}
    )

    if result2:
        print(f"Profile result type: {result2.get('type')}")
        print(f"Profile step_id: {result2.get('step_id')}")
        if result2.get("type") == "form":
            print(f"Form schema: {result2.get('data_schema')}")
        elif result2.get("type") == "abort":
            print(f"Aborted: {result2.get('reason')}")
        elif result2.get("type") == "menu":
            print(f"Still showing menu: {result2.get('menu_options')}")
    else:
        print("Profile result is None!")


if __name__ == "__main__":
    import logging

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(test_profile_step())
