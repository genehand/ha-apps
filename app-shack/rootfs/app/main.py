#!/usr/bin/env python3

import asyncio
import logging
import signal
import sys
from pathlib import Path

import colorlog
from paho.mqtt.client import Client

from config import Config
from credentials import CredentialsManager
from mqtt_bridge import MqttBridge
from shim import ShimManager
from shim.web import WebUI

# Configuration paths
CONFIG_FILE_PATH = "/data/options.json"
DEV_CONFIG_PATH = "shack-config.yaml"

# Determine if running as addon or locally
IS_ADDON = Path("/data").exists() and Path("/data").is_dir()
CONFIG_DIR = Path("/data") if IS_ADDON else Path("./data")


def setup_logging(log_level: str) -> logging.Logger:
    """Configure colored logging."""
    logger = logging.getLogger("shack")

    # Clear any existing handlers first to prevent duplicates
    logger.handlers.clear()

    # Prevent propagation to root logger (which might have default handlers)
    logger.propagate = False

    logger.setLevel(getattr(logging, log_level.upper()))

    handler = colorlog.StreamHandler(sys.stdout)
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )
    )
    logger.addHandler(handler)

    return logger


async def main():
    """Main entry point."""
    # Load configuration
    config = Config.load(CONFIG_FILE_PATH, DEV_CONFIG_PATH)

    # Setup logging
    logger = setup_logging(config.log_level)
    logger.info("Starting Shack with HA Shim v0.1.0")

    # Initialize MQTT bridge
    mqtt_bridge = MqttBridge(
        host=config.mqtt_host,
        port=config.mqtt_port,
        username=config.mqtt_username,
        password=config.mqtt_password,
    )

    # Connect to MQTT first
    await mqtt_bridge.connect()

    # Initialize HA Shim Manager
    # The shim uses the raw MQTT client from the bridge
    shim_manager = ShimManager(
        config_dir=CONFIG_DIR,
        mqtt_client=mqtt_bridge.client,
        mqtt_base_topic="shim",
    )

    # Initialize Web UI
    web_ui = WebUI(
        shim_manager=shim_manager,
        host="0.0.0.0",
        port=8080,
    )

    # Setup signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def signal_handler():
        """Handle shutdown signals."""
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        # Start shim (this loads enabled integrations)
        await shim_manager.start()

        # Start web UI in background
        web_task = asyncio.create_task(web_ui.start())

        logger.info("Shack with HA Shim is running")
        logger.info(f"Web UI available at http://localhost:8080")
        logger.info("Press Ctrl+C to stop")

        # Wait for shutdown signal
        await shutdown_event.wait()
        logger.info("Shutdown requested, stopping services...")

        # Cancel the web task
        web_task.cancel()
        try:
            await web_task
        except asyncio.CancelledError:
            pass

    except asyncio.CancelledError:
        logger.info("Task cancelled")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise
    finally:
        # Remove signal handlers first to prevent re-entry
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.remove_signal_handler(sig)
            except (ValueError, NotImplementedError):
                pass  # Handler might already be removed
        # Always call shutdown - it has duplicate protection
        await shutdown(shim_manager, mqtt_bridge)


async def shutdown(shim_manager: ShimManager, mqtt_bridge: MqttBridge):
    """Graceful shutdown."""
    logger = logging.getLogger("shack")
    logger.info("Shutting down Shack...")

    try:
        await shim_manager.stop()
        await mqtt_bridge.disconnect()
    except Exception as e:
        logger.warning(f"Error during shutdown: {e}")

    logger.info("Shack stopped")


def main_sync():
    """Synchronous entry point with proper error handling."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # Normal shutdown via Ctrl+C
    except SystemExit:
        pass  # Normal shutdown via signal


if __name__ == "__main__":
    main_sync()
