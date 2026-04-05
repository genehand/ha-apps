#!/usr/bin/env python3

import asyncio
import logging
import signal
import sys
from pathlib import Path

import colorlog
from paho.mqtt.client import Client

from config import Config
from mqtt_bridge import MqttBridge
from shim import ShimManager, __version__
from shim.web import WebUI

# Configuration paths
CONFIG_FILE_PATH = "/data/options.json"
DEV_CONFIG_PATH = "shack-config.yaml"

# Determine if running as addon or locally
IS_ADDON = Path("/data").exists() and Path("/data").is_dir()
CONFIG_DIR = Path("/data") if IS_ADDON else Path("./data")


def setup_logging(
    log_level: str, integration_log_levels: dict = None
) -> logging.Logger:
    """Configure colored logging for root logger to capture all output."""
    # Get root logger to capture all logging (including shim.*)
    root_logger = logging.getLogger()

    # Clear any existing handlers first to prevent duplicates
    root_logger.handlers.clear()

    # Remove default handlers that might interfere
    root_logger.handlers = []

    # Set level on root logger
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Create handler with colored output
    handler = colorlog.StreamHandler(sys.stdout)
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s %(levelname)s: %(name)s - %(message)s",
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
    root_logger.addHandler(handler)

    # Also configure the shack logger for backwards compatibility
    shack_logger = logging.getLogger("shack")
    shack_logger.setLevel(getattr(logging, log_level.upper()))

    # Apply per-integration log levels
    integration_log_levels = integration_log_levels or {}
    for logger_name, level in integration_log_levels.items():
        logging.getLogger(logger_name).setLevel(getattr(logging, level.upper()))
        shack_logger.debug(f"Set log level for {logger_name} to {level}")

    return shack_logger


async def main():
    """Main entry point."""
    # Load configuration
    config = Config.load(CONFIG_FILE_PATH, DEV_CONFIG_PATH)

    # Setup logging
    logger = setup_logging(config.log_level, config.integration_log_levels)
    logger.info(f"Starting Shack with HA Shim v{__version__}")

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
        mqtt_base_topic="shack",
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
        # Start shim phase 1 (fast setup: MQTT, HACS fetch, basic state)
        await shim_manager.start()

        # Start web UI immediately (available during integration loading)
        web_task = asyncio.create_task(web_ui.start())

        # Start shim phase 2 (integration loading in background)
        # This allows the web UI to be accessible while integrations load
        loading_task = asyncio.create_task(shim_manager.start_integration_loading())

        # Wait for shutdown signal
        await shutdown_event.wait()
        logger.debug("Shutdown requested, stopping services...")

        # Cancel the web task
        web_task.cancel()
        try:
            await web_task
        except asyncio.CancelledError:
            pass

        # Wait for loading task to complete (if still running)
        if loading_task:
            loading_task.cancel()
            try:
                await loading_task
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
    logger.debug("Shutting down Shack...")

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
