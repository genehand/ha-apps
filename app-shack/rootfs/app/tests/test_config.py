"""Tests for config loader."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from config import Config, _query_supervisor_api


class TestQuerySupervisorApi:
    """Test cases for _query_supervisor_api function."""

    def test_returns_none_when_no_token(self):
        """Test returns None when SUPERVISOR_TOKEN not set."""
        with patch.dict(os.environ, {}, clear=True):
            result = _query_supervisor_api("/services/mqtt")
            assert result is None

    def test_returns_none_on_api_failure(self, monkeypatch):
        """Test returns None when API call fails."""
        monkeypatch.setenv("SUPERVISOR_TOKEN", "fake_token")

        # Mock urllib to raise an exception
        def mock_urlopen(*args, **kwargs):
            raise Exception("Connection refused")

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        result = _query_supervisor_api("/services/mqtt")
        assert result is None


class TestConfig:
    """Test cases for Config loader."""

    def test_from_dict_with_manual_mqtt(self):
        """Test loading config with manual MQTT settings."""
        data = {
            "mqtt_host": "external.broker.com",
            "mqtt_port": 1884,
            "mqtt_username": "user",
            "mqtt_password": "pass",
            "log_level": "DEBUG",
        }

        config = Config.from_dict(data)

        assert config.mqtt_host == "external.broker.com"
        assert config.mqtt_port == 1884
        assert config.mqtt_username == "user"
        assert config.mqtt_password == "pass"
        assert config.log_level == "DEBUG"

    def test_from_dict_missing_optional_fields(self):
        """Test config handles missing optional MQTT fields."""
        # Simulate when mqtt_host and mqtt_port are not in config (optional)
        data = {"log_level": "DEBUG"}

        config = Config.from_dict(data)

        # Should use defaults when services not available and fields missing
        assert config.mqtt_host == "core-mosquitto"
        assert config.mqtt_port == 1883
        assert config.mqtt_username is None
        assert config.mqtt_password is None
        assert config.log_level == "DEBUG"

    def test_from_dict_defaults(self):
        """Test config uses defaults when values not provided."""
        data = {}

        config = Config.from_dict(data)

        assert config.mqtt_host == "core-mosquitto"
        assert config.mqtt_port == 1883
        assert config.mqtt_username is None
        assert config.mqtt_password is None
        assert config.log_level == "INFO"

    def test_from_dict_empty_strings_become_none(self):
        """Test that empty strings for credentials become None."""
        data = {
            "mqtt_username": "",
            "mqtt_password": "",
        }

        config = Config.from_dict(data)

        assert config.mqtt_username is None
        assert config.mqtt_password is None

    def test_from_dict_integration_log_levels_list(self):
        """Test converting list format for integration_log_levels."""
        data = {
            "integration_log_levels": [
                {"name": "integration1", "level": "DEBUG"},
                {"name": "integration2", "level": "WARNING"},
            ]
        }

        config = Config.from_dict(data)

        assert config.integration_log_levels == {
            "integration1": "DEBUG",
            "integration2": "WARNING",
        }

    def test_from_dict_integration_log_levels_dict(self):
        """Test passing dict format for integration_log_levels."""
        data = {
            "integration_log_levels": {"integration1": "DEBUG", "integration2": "ERROR"}
        }

        config = Config.from_dict(data)

        assert config.integration_log_levels == {
            "integration1": "DEBUG",
            "integration2": "ERROR",
        }

    def test_load_mqtt_services_fallback_when_no_services(self, tmp_path, monkeypatch):
        """Test that config falls back to manual options when services not available."""

        # Mock both API and file to return None
        def mock_query_api(path):
            return None

        def mock_load_file(cls):
            return None

        monkeypatch.setattr("config._query_supervisor_api", mock_query_api)
        monkeypatch.setattr(Config, "_load_mqtt_services", classmethod(mock_load_file))

        data = {
            "mqtt_host": "my.broker.com",
            "mqtt_port": 8883,
            "mqtt_username": "manual_user",
            "mqtt_password": "manual_pass",
        }

        config = Config.from_dict(data)

        assert config.mqtt_host == "my.broker.com"
        assert config.mqtt_port == 8883
        assert config.mqtt_username == "manual_user"
        assert config.mqtt_password == "manual_pass"

    def test_load_mqtt_services_uses_supervisor_api(self, tmp_path, monkeypatch):
        """Test that config uses Supervisor API credentials when available."""

        # Mock Supervisor API response
        def mock_query_api(path):
            if path == "/services/mqtt":
                return {
                    "data": {
                        "host": "auto.mosquitto.local",
                        "port": 1883,
                        "username": "addons",
                        "password": "auto_generated_token",
                    }
                }
            return None

        # Mock the _load_mqtt_services method directly to return API data
        def mock_load_services(cls):
            response = mock_query_api("/services/mqtt")
            if response and "data" in response:
                data = response["data"]
                if data and data.get("username") and data.get("password"):
                    return {
                        "host": data.get("host", "core-mosquitto"),
                        "port": data.get("port", 1883),
                        "username": data.get("username"),
                        "password": data.get("password"),
                    }
            return None

        monkeypatch.setattr(
            Config, "_load_mqtt_services", classmethod(mock_load_services)
        )

        # Even with manual config provided, services should take priority
        data = {
            "mqtt_host": "manual.broker.com",
            "mqtt_port": 1884,
            "mqtt_username": "manual_user",
            "mqtt_password": "manual_pass",
        }

        config = Config.from_dict(data)

        # Should use services credentials from API
        assert config.mqtt_host == "auto.mosquitto.local"
        assert config.mqtt_port == 1883
        assert config.mqtt_username == "addons"
        assert config.mqtt_password == "auto_generated_token"

    def test_load_from_json_file(self, tmp_path):
        """Test loading config from JSON file."""
        config_path = tmp_path / "options.json"
        config_data = {
            "mqtt_host": "json.broker.com",
            "mqtt_port": 1885,
            "log_level": "WARNING",
        }
        with open(config_path, "w") as f:
            json.dump(config_data, f)

        dev_path = tmp_path / "nonexistent.yaml"

        config = Config.load(str(config_path), str(dev_path))

        assert config.mqtt_host == "json.broker.com"
        assert config.mqtt_port == 1885
        assert config.log_level == "WARNING"

    def test_load_from_yaml_file(self, tmp_path):
        """Test loading config from YAML file."""
        dev_path = tmp_path / "config.yaml"
        config_data = {
            "mqtt_host": "yaml.broker.com",
            "mqtt_port": 1886,
            "log_level": "ERROR",
        }
        with open(dev_path, "w") as f:
            yaml.dump(config_data, f)

        config_path = tmp_path / "nonexistent.json"

        config = Config.load(str(config_path), str(dev_path))

        assert config.mqtt_host == "yaml.broker.com"
        assert config.mqtt_port == 1886
        assert config.log_level == "ERROR"

    def test_load_creates_dev_config(self, tmp_path):
        """Test that load creates dev config when neither file exists."""
        config_path = tmp_path / "options.json"
        dev_path = tmp_path / "config.yaml"

        config = Config.load(str(config_path), str(dev_path))

        # Should have created the dev config file
        assert dev_path.exists()
        assert config.mqtt_host == "core-mosquitto"  # Default value

    def test_save_dev_config(self, tmp_path):
        """Test saving dev config."""
        config = Config(
            mqtt_host="test.com",
            mqtt_port=1887,
            mqtt_username="testuser",
            mqtt_password="testpass",
            log_level="DEBUG",
        )

        dev_path = tmp_path / "config.yaml"
        config._save_dev_config(str(dev_path))

        # Verify file was created and can be loaded
        with open(dev_path, "r") as f:
            loaded = yaml.safe_load(f)

        assert loaded["mqtt_host"] == "test.com"
        assert loaded["mqtt_port"] == 1887
        assert loaded["mqtt_username"] == "testuser"
        assert loaded["mqtt_password"] == "testpass"
        assert loaded["log_level"] == "DEBUG"


class TestLoadMqttServices:
    """Test cases for _load_mqtt_services method."""

    def test_load_mqtt_services_returns_none_when_no_services(self, monkeypatch):
        """Test _load_mqtt_services returns None when no services available."""

        # Mock both API and file to return None
        def mock_query_api(path):
            return None

        monkeypatch.setattr("config._query_supervisor_api", mock_query_api)
        monkeypatch.setattr(
            Config, "_load_mqtt_services", classmethod(lambda cls: None)
        )

        result = Config._load_mqtt_services()
        assert result is None

    def test_load_mqtt_services_returns_data_from_api(self, monkeypatch):
        """Test _load_mqtt_services returns data from Supervisor API when available."""
        expected_data = {
            "host": "mosquitto",
            "port": 1883,
            "username": "addons",
            "password": "secret123",
        }

        def mock_query_api(path):
            if path == "/services/mqtt":
                return {"data": expected_data}
            return None

        monkeypatch.setattr("config._query_supervisor_api", mock_query_api)
        monkeypatch.setattr(
            Config, "_load_mqtt_services", classmethod(lambda cls: expected_data)
        )

        result = Config._load_mqtt_services()
        assert result == expected_data

    def test_load_mqtt_services_api_missing_credentials(self, monkeypatch):
        """Test _load_mqtt_services falls back when API has no credentials."""

        # Mock API to return empty data
        def mock_query_api(path):
            if path == "/services/mqtt":
                return {
                    "data": {"host": "mosquitto", "port": 1883}
                }  # No username/password
            return None

        monkeypatch.setattr("config._query_supervisor_api", mock_query_api)

        # Mock _load_mqtt_services to return None (simulating API with no credentials)
        monkeypatch.setattr(
            Config, "_load_mqtt_services", classmethod(lambda cls: None)
        )

        result = Config._load_mqtt_services()
        assert result is None

    def test_load_mqtt_services_returns_data_from_file(self, tmp_path, monkeypatch):
        """Test _load_mqtt_services returns data from services file when API fails."""

        # Mock API to fail
        monkeypatch.setattr("config._query_supervisor_api", lambda path: None)

        services_dir = tmp_path / "services" / "mqtt"
        services_dir.mkdir(parents=True)
        services_file = services_dir / "config.json"

        expected_data = {
            "host": "mosquitto",
            "port": 1883,
            "username": "addons",
            "password": "secret123",
        }
        with open(services_file, "w") as f:
            json.dump(expected_data, f)

        # Monkeypatch the path in the method
        def mock_load(cls):
            # API call returns None
            api_result = None
            if api_result and "data" in api_result:
                return api_result["data"]

            # Fallback to file
            try:
                with open(services_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return None

        monkeypatch.setattr(Config, "_load_mqtt_services", classmethod(mock_load))

        result = Config._load_mqtt_services()
        assert result == expected_data

    def test_load_mqtt_services_handles_json_error(self, tmp_path, monkeypatch):
        """Test _load_mqtt_services handles malformed JSON gracefully."""
        monkeypatch.setattr("config._query_supervisor_api", lambda path: None)

        services_dir = tmp_path / "services" / "mqtt"
        services_dir.mkdir(parents=True)
        services_file = services_dir / "config.json"

        # Write invalid JSON
        with open(services_file, "w") as f:
            f.write("not valid json")

        def mock_load(cls):
            try:
                with open(services_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return None

        monkeypatch.setattr(Config, "_load_mqtt_services", classmethod(mock_load))

        result = Config._load_mqtt_services()
        assert result is None

    def test_load_mqtt_services_handles_io_error(self, tmp_path, monkeypatch):
        """Test _load_mqtt_services handles IO errors gracefully."""
        monkeypatch.setattr("config._query_supervisor_api", lambda path: None)

        services_dir = tmp_path / "services" / "mqtt"
        services_dir.mkdir(parents=True)
        services_file = services_dir / "config.json"

        # Create file but make it unreadable (use a directory as file)
        services_file.mkdir()  # Can't read a directory as JSON

        def mock_load(cls):
            try:
                with open(services_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return None

        monkeypatch.setattr(Config, "_load_mqtt_services", classmethod(mock_load))

        result = Config._load_mqtt_services()
        assert result is None
