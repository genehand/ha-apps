"""Home Assistant exceptions shim.

Provides exceptions commonly raised by HA integrations.
"""


class HomeAssistantError(Exception):
    """General Home Assistant exception."""

    pass


class ConfigEntryNotReady(HomeAssistantError):
    """Error to indicate that config entry is not ready."""

    pass


class ConfigEntryAuthFailed(HomeAssistantError):
    """Error to indicate that config entry could not authenticate."""

    pass


class PlatformNotReady(HomeAssistantError):
    """Error to indicate that platform is not ready."""

    pass


class InvalidStateError(HomeAssistantError):
    """When an invalid state is encountered."""

    pass


class Unauthorized(HomeAssistantError):
    """When an action is unauthorized."""

    pass


class UnknownUser(HomeAssistantError):
    """When an user is unknown."""

    pass


class TemplateError(HomeAssistantError):
    """Error during template rendering."""

    pass


class ConditionError(HomeAssistantError):
    """Error during condition evaluation."""

    pass


class IntegrationError(HomeAssistantError):
    """Base class for integration errors."""

    pass


class NoEntitySpecifiedError(HomeAssistantError):
    """No entity is specified."""

    pass


class MultipleInvalid(HomeAssistantError):
    """Multiple errors found."""

    pass


class ServiceNotFound(HomeAssistantError):
    """Service not found."""

    def __init__(self, domain: str, service: str) -> None:
        """Initialize error."""
        super().__init__(f"Service {domain}.{service} not found")
        self.domain = domain
        self.service = service
