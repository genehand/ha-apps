"""Logging shim for Home Assistant integrations.

Provides logger instances that prefix messages with integration name.
"""

import logging
from typing import Optional


class IntegrationLogger:
    """Logger wrapper that adds integration context."""

    def __init__(self, name: str, integration: Optional[str] = None):
        self._logger = logging.getLogger(name)
        self._integration = integration
        self._name = name

    def _format_message(self, msg: str) -> str:
        """Format message with integration prefix."""
        if self._integration:
            return f"[{self._integration}] {msg}"
        return msg

    def debug(self, msg: str, *args, **kwargs):
        """Log debug message."""
        self._logger.debug(self._format_message(msg), *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        """Log info message."""
        self._logger.info(self._format_message(msg), *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        """Log warning message."""
        self._logger.warning(self._format_message(msg), *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        """Log error message."""
        self._logger.error(self._format_message(msg), *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        """Log critical message."""
        self._logger.critical(self._format_message(msg), *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        """Log exception with traceback."""
        self._logger.exception(self._format_message(msg), *args, **kwargs)

    def setLevel(self, level):
        """Set logging level."""
        self._logger.setLevel(level)

    def isEnabledFor(self, level):
        """Check if level is enabled."""
        return self._logger.isEnabledFor(level)

    def getEffectiveLevel(self):
        """Get effective logging level."""
        return self._logger.getEffectiveLevel()


# Global integration context
_current_integration: Optional[str] = None


def set_current_integration(integration: Optional[str]) -> None:
    """Set the current integration context for logging."""
    global _current_integration
    _current_integration = integration


def get_current_integration() -> Optional[str]:
    """Get the current integration context."""
    return _current_integration


def get_logger(name: str, integration: Optional[str] = None) -> IntegrationLogger:
    """Get a logger with optional integration context.

    Args:
        name: Logger name (usually __name__)
        integration: Optional integration name to prefix messages

    Returns:
        IntegrationLogger instance
    """
    # If no integration specified, use global context
    if integration is None:
        integration = _current_integration

    return IntegrationLogger(name, integration)


# For homeassistant.core.logging compatibility
class HALogger:
    """Compatibility class for homeassistant.core.logging."""

    @staticmethod
    def getLogger(name: str):
        """Get logger - matches HA's API."""
        return get_logger(name)


def setup_logging(level: int = logging.INFO) -> None:
    """Setup logging configuration.

    This should be called once during startup.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
