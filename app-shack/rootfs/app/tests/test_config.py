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

    def test_load_mqtt_from_env_when_set(self, monkeypatch):
        """Test that config uses environment variables when set (bashio)."""
        # Set environment variables as bashio would
        monkeypatch.setenv("MQTT_HOST", "auto.mosquitto.local")
        monkeypatch.setenv("MQTT_PORT", "1883")
        monkeypatch.setenv("MQTT_USERNAME", "addons")
        monkeypatch.setenv("MQTT_PASSWORD", "auto_generated_token")

        # Even with manual config provided, env vars should take priority
        data = {
            "mqtt_host": "manual.broker.com",
            "mqtt_port": 1884,
            "mqtt_username": "manual_user",
            "mqtt_password": "manual_pass",
        }

        config = Config.from_dict(data)

        # Should use environment variables from bashio
        assert config.mqtt_host == "auto.mosquitto.local"
        assert config.mqtt_port == 1883
        assert config.mqtt_username == "addons"
        assert config.mqtt_password == "auto_generated_token"

    def test_load_mqtt_fallback_when_env_not_set(self, monkeypatch):
        """Test that config falls back to manual options when env vars not set."""
        # Ensure environment variables are not set
        monkeypatch.delenv("MQTT_HOST", raising=False)
        monkeypatch.delenv("MQTT_PORT", raising=False)
        monkeypatch.delenv("MQTT_USERNAME", raising=False)
        monkeypatch.delenv("MQTT_PASSWORD", raising=False)

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

    def test_load_mqtt_incomplete_env_uses_manual(self, monkeypatch):
        """Test that incomplete env vars fall back to manual config."""
        # Set only some env vars (incomplete)
        monkeypatch.setenv("MQTT_HOST", "auto.mosquitto.local")
        # Missing username and password

        data = {
            "mqtt_host": "manual.broker.com",
            "mqtt_username": "manual_user",
            "mqtt_password": "manual_pass",
        }

        config = Config.from_dict(data)

        # Should fall back to manual config
        assert config.mqtt_host == "manual.broker.com"
        assert config.mqtt_username == "manual_user"
        assert config.mqtt_password == "manual_pass"

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


class TestLoadMqttFromEnv:
    """Test cases for _load_mqtt_from_env method."""

    def test_load_mqtt_from_env_returns_none_when_not_set(self, monkeypatch):
        """Test _load_mqtt_from_env returns None when env vars not set."""
        # Ensure environment variables are not set
        monkeypatch.delenv("MQTT_HOST", raising=False)
        monkeypatch.delenv("MQTT_PORT", raising=False)
        monkeypatch.delenv("MQTT_USERNAME", raising=False)
        monkeypatch.delenv("MQTT_PASSWORD", raising=False)

        result = Config._load_mqtt_from_env()
        assert result is None

    def test_load_mqtt_from_env_returns_data_when_set(self, monkeypatch):
        """Test _load_mqtt_from_env returns data when all env vars set."""
        expected_data = {
            "host": "mosquitto",
            "port": 1883,
            "username": "addons",
            "password": "secret123",
        }

        monkeypatch.setenv("MQTT_HOST", "mosquitto")
        monkeypatch.setenv("MQTT_PORT", "1883")
        monkeypatch.setenv("MQTT_USERNAME", "addons")
        monkeypatch.setenv("MQTT_PASSWORD", "secret123")

        result = Config._load_mqtt_from_env()
        assert result == expected_data

    def test_load_mqtt_from_env_returns_none_when_incomplete(self, monkeypatch):
        """Test _load_mqtt_from_env returns None when env vars incomplete."""
        # Set only some env vars
        monkeypatch.setenv("MQTT_HOST", "mosquitto")
        monkeypatch.setenv("MQTT_PORT", "1883")
        # Missing username and password

        result = Config._load_mqtt_from_env()
        assert result is None

    def test_load_mqtt_from_env_uses_default_port(self, monkeypatch):
        """Test _load_mqtt_from_env uses default port 1883 when not set."""
        monkeypatch.setenv("MQTT_HOST", "mosquitto")
        monkeypatch.setenv("MQTT_USERNAME", "addons")
        monkeypatch.setenv("MQTT_PASSWORD", "secret123")
        # Port not set

        result = Config._load_mqtt_from_env()
        assert result["port"] == 1883
