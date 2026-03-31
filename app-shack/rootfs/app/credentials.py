"""Credentials manager for device authentication."""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("shack.credentials")

CREDENTIALS_FILE = "/data/credentials.json"
DEV_CREDENTIALS_FILE = "credentials.json"


@dataclass
class DeviceCredential:
    """Credential for a single device."""

    serial: str
    device_type: str
    credential: str
    name: str
    ip_address: str

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "device_type": self.device_type,
            "credential": self.credential,
            "name": self.name,
            "ip_address": self.ip_address,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceCredential":
        return cls(
            serial=data["serial"],
            device_type=data["device_type"],
            credential=data["credential"],
            name=data.get("name", f"Dyson {data['serial']}"),
            ip_address=data.get("ip_address", ""),
        )


@dataclass
class CloudCredentials:
    """MyDyson cloud account credentials."""

    email: str
    password: str

    def to_dict(self) -> dict:
        return {
            "email": self.email,
            "password": self.password,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CloudCredentials":
        return cls(
            email=data["email"],
            password=data["password"],
        )


@dataclass
class Credentials:
    """All credentials container."""

    devices: List[DeviceCredential] = field(default_factory=list)
    cloud_credentials: Optional[CloudCredentials] = None

    def to_dict(self) -> dict:
        result = {
            "version": 1,
            "devices": [d.to_dict() for d in self.devices],
        }
        if self.cloud_credentials:
            result["cloud_credentials"] = self.cloud_credentials.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Credentials":
        devices = [DeviceCredential.from_dict(d) for d in data.get("devices", [])]
        cloud = None
        if "cloud_credentials" in data:
            cloud = CloudCredentials.from_dict(data["cloud_credentials"])
        return cls(devices=devices, cloud_credentials=cloud)


class CredentialsManager:
    """Manages device credentials storage."""

    def __init__(self):
        self._path = self._get_credentials_path()

    def _get_credentials_path(self) -> str:
        """Get the appropriate credentials file path."""
        if os.path.exists(CREDENTIALS_FILE):
            return CREDENTIALS_FILE
        return DEV_CREDENTIALS_FILE

    def load(self) -> Credentials:
        """Load credentials from file."""
        if not os.path.exists(self._path):
            logger.info(f"Credentials file not found at {self._path}")
            return Credentials()

        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            credentials = Credentials.from_dict(data)
            logger.info(f"Loaded credentials for {len(credentials.devices)} device(s)")
            return credentials
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return Credentials()

    def save(self, credentials: Credentials):
        """Save credentials to file."""
        try:
            with open(self._path, "w") as f:
                json.dump(credentials.to_dict(), f, indent=2)
            logger.info(f"Saved credentials for {len(credentials.devices)} device(s)")
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")

    def add_device(self, credential: DeviceCredential):
        """Add or update a device credential."""
        credentials = self.load()

        # Remove existing entry for this serial
        credentials.devices = [
            d for d in credentials.devices if d.serial != credential.serial
        ]
        credentials.devices.append(credential)

        self.save(credentials)
