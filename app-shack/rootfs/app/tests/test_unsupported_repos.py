"""Tests for unsupported repositories functionality.

These tests verify that the unsupported repos static file works correctly,
including loading and blocking of unsupported repos.
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
import sys
from pathlib import Path
import tempfile

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.storage import Storage
from shim.integrations.manager import IntegrationManager


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def integration_manager_with_mocked_storage(temp_data_dir):
    """Create an IntegrationManager with mocked storage for testing."""
    mock_storage = MagicMock()
    mock_storage.load_unsupported_repos.return_value = {
        "owner/blocked_repo": {
            "reason": "Test reason for blocking",
        }
    }
    manager = IntegrationManager(mock_storage, temp_data_dir)
    return manager


class TestStorageUnsupportedRepos:
    """Test cases for Storage unsupported repos functionality."""

    def test_load_unsupported_repos_empty(self):
        """Test loading unsupported repos when file doesn't exist."""
        # Use a completely separate temp directory to ensure isolation
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_dir = Path(tmpdir) / "shim"
            empty_dir.mkdir()
            storage = Storage(empty_dir)
            # Patch the file path to a non-existent file in temp dir
            # Path should be tmpdir/data/unsupported_repos.json (one level up from shim/)
            storage._unsupported_repos_file = Path(tmpdir) / "data" / "nonexistent.json"
            repos = storage.load_unsupported_repos()
            assert repos == {}

    def test_load_unsupported_repos_from_static_file(self, temp_data_dir):
        """Test loading unsupported repos from static file."""
        # Create a unique data dir to avoid conflicts
        # File should be at app/data/unsupported_repos.json relative to shim dir
        unique_data_dir = temp_data_dir / "data"
        unique_data_dir.mkdir(exist_ok=True)
        static_file = unique_data_dir / "unsupported_repos.json"
        test_data = {
            "owner/test_repo": {
                "reason": "Test reason",
            }
        }
        with open(static_file, "w") as f:
            json.dump(test_data, f)

        # Create storage and patch the file path
        shim_dir = temp_data_dir / "shim"
        shim_dir.mkdir()
        storage = Storage(shim_dir)
        storage._unsupported_repos_file = static_file

        loaded = storage.load_unsupported_repos()
        assert loaded == test_data

    def test_is_unsupported_repo(self, temp_data_dir):
        """Test checking if a repository is unsupported."""
        # Create the static file at the correct location (app/data/)
        data_dir = temp_data_dir / "data"
        data_dir.mkdir(exist_ok=True)
        static_file = data_dir / "unsupported_repos.json"
        test_data = {
            "owner/blocked_repo": {
                "reason": "Blocked for testing",
            }
        }
        with open(static_file, "w") as f:
            json.dump(test_data, f)

        # Create storage and patch the file location
        shim_dir = temp_data_dir / "shim"
        shim_dir.mkdir()
        storage = Storage(shim_dir)
        storage._unsupported_repos_file = static_file

        entry = storage.is_unsupported_repo("owner/blocked_repo")
        assert entry is not None
        assert entry["reason"] == "Blocked for testing"

        not_blocked = storage.is_unsupported_repo("owner/allowed_repo")
        assert not_blocked is None

    def test_is_unsupported_repo_by_url(self, temp_data_dir):
        """Test checking if a repo URL is unsupported."""
        # Create the static file at the correct location (app/data/)
        data_dir = temp_data_dir / "data"
        data_dir.mkdir(exist_ok=True)
        static_file = data_dir / "unsupported_repos.json"
        test_data = {
            "owner/blocked_by_url": {
                "reason": "Blocked URL",
            }
        }
        with open(static_file, "w") as f:
            json.dump(test_data, f)

        # Create storage and patch the file location
        shim_dir = temp_data_dir / "shim"
        shim_dir.mkdir()
        storage = Storage(shim_dir)
        storage._unsupported_repos_file = static_file

        entry = storage.is_unsupported_repo_by_url(
            "https://github.com/owner/blocked_by_url"
        )
        assert entry is not None
        assert entry["reason"] == "Blocked URL"

        not_blocked = storage.is_unsupported_repo_by_url(
            "https://github.com/owner/allowed_url"
        )
        assert not_blocked is None

    def test_is_unsupported_repo_by_url(self, temp_data_dir):
        """Test checking if a repo URL is unsupported."""
        # Create the static file
        unique_data_dir = temp_data_dir / "test_data"
        unique_data_dir.mkdir(exist_ok=True)
        static_file = unique_data_dir / "unsupported_repos.json"
        test_data = {
            "owner/blocked_by_url": {
                "reason": "Blocked URL",
            }
        }
        with open(static_file, "w") as f:
            json.dump(test_data, f)

        # Create storage and patch the file location
        shim_dir = temp_data_dir / "shim"
        shim_dir.mkdir()
        storage = Storage(shim_dir)
        storage._unsupported_repos_file = static_file

        entry = storage.is_unsupported_repo_by_url(
            "https://github.com/owner/blocked_by_url"
        )
        assert entry is not None
        assert entry["reason"] == "Blocked URL"

        not_blocked = storage.is_unsupported_repo_by_url(
            "https://github.com/owner/allowed_url"
        )
        assert not_blocked is None


class TestIntegrationManagerUnsupportedRepos:
    """Test cases for IntegrationManager unsupported repos read-only functionality."""

    def test_get_unsupported_repos(self, integration_manager_with_mocked_storage):
        """Test getting list of unsupported repos."""
        repos = integration_manager_with_mocked_storage.get_unsupported_repos()
        assert len(repos) == 1
        assert repos[0]["full_name"] == "owner/blocked_repo"
        assert repos[0]["reason"] == "Test reason for blocking"

    def test_is_unsupported_repo(self, integration_manager_with_mocked_storage):
        """Test checking if a repo is unsupported."""
        entry = integration_manager_with_mocked_storage.is_unsupported_repo(
            "owner/blocked_repo"
        )
        assert entry is not None
        assert entry["reason"] == "Test reason for blocking"

        not_blocked = integration_manager_with_mocked_storage.is_unsupported_repo(
            "owner/allowed"
        )
        assert not_blocked is None

    def test_is_unsupported_repo_by_url(self, integration_manager_with_mocked_storage):
        """Test checking unsupported repo by URL."""
        # The mock returns by full_name match
        entry = integration_manager_with_mocked_storage.is_unsupported_repo_by_url(
            "https://github.com/owner/blocked_repo"
        )
        assert entry is not None

        not_blocked = (
            integration_manager_with_mocked_storage.is_unsupported_repo_by_url(
                "https://github.com/owner/allowed"
            )
        )
        assert not_blocked is None


class TestGetAvailableIntegrationsUnsupportedFlag:
    """Test cases for unsupported flag in get_available_integrations."""

    def test_available_integrations_marks_unsupported(self, temp_data_dir):
        """Test that get_available_integrations marks unsupported repos."""
        mock_storage = MagicMock()
        mock_storage.load_unsupported_repos.return_value = {
            "owner/unsupported_repo": {
                "reason": "Not supported",
            }
        }
        mock_storage.load_integrations.return_value = {}
        mock_storage.load_custom_repos.return_value = {}

        manager = IntegrationManager(mock_storage, temp_data_dir)

        # Mock HACS repos
        manager._hacs_repos = {
            "owner/supported_repo": {
                "domain": "supported_domain",
                "repository_url": "https://github.com/owner/supported_repo",
                "name": "Supported Integration",
            },
            "owner/unsupported_repo": {
                "domain": "unsupported_domain",
                "repository_url": "https://github.com/owner/unsupported_repo",
                "name": "Unsupported Integration",
            },
        }

        available = manager.get_available_integrations()

        # Find the supported and unsupported entries
        supported = next(
            (a for a in available if a["full_name"] == "owner/supported_repo"), None
        )
        unsupported = next(
            (a for a in available if a["full_name"] == "owner/unsupported_repo"), None
        )

        assert supported is not None
        assert supported["unsupported"] is False
        assert supported.get("unsupported_reason") is None

        assert unsupported is not None
        assert unsupported["unsupported"] is True
        assert unsupported.get("unsupported_reason") == "Not supported"


class TestUnsupportedReposBlocking:
    """Test cases for blocking functionality of unsupported repos."""

    @pytest.mark.asyncio
    async def test_add_custom_repository_blocked(self, temp_data_dir):
        """Test that adding a blocked custom repository fails."""
        mock_storage = MagicMock()
        mock_storage.load_unsupported_repos.return_value = {
            "alandtse/alexa_media_player": {
                "reason": "MQTT component doesn't support the media_player platform",
            }
        }

        manager = IntegrationManager(mock_storage, temp_data_dir)

        # Try to add it as custom repo
        success, message = await manager.add_custom_repository(
            "https://github.com/alandtse/alexa_media_player"
        )

        assert success is False
        assert "not supported" in message.lower()
        assert "MQTT component doesn't support the media_player platform" in message

    @pytest.mark.asyncio
    async def test_add_custom_repository_allowed(self, temp_data_dir):
        """Test that adding an allowed custom repository succeeds (not blocked)."""
        mock_storage = MagicMock()
        mock_storage.load_unsupported_repos.return_value = {}

        manager = IntegrationManager(mock_storage, temp_data_dir)

        # Mock the _fetch_repo_info to avoid network calls
        with patch.object(
            manager,
            "_fetch_repo_info",
            return_value={
                "domain": "allowed_domain",
                "name": "Allowed Integration",
                "version": "1.0.0",
                "repository_url": "https://github.com/owner/allowed_repo",
                "manifest": {
                    "domain": "allowed_domain",
                    "name": "Allowed Integration",
                    "version": "1.0.0",
                },
            },
        ):
            success, message = await manager.add_custom_repository(
                "https://github.com/owner/allowed_repo"
            )

            # Note: This might fail if the repo already exists, but that's ok
            # We're testing that it's not blocked by unsupported repos
            if not success:
                assert "not supported" not in message.lower()

    @pytest.mark.asyncio
    async def test_install_integration_blocked_from_hacs(self, temp_data_dir):
        """Test that installing a blocked HACS repository fails."""
        mock_storage = MagicMock()
        mock_storage.load_unsupported_repos.return_value = {
            "owner/blocked_hacs_repo": {
                "reason": "Blocked HACS repo",
            }
        }

        manager = IntegrationManager(mock_storage, temp_data_dir)

        # Mock the HACS repos
        manager._hacs_repos = {
            "owner/blocked_hacs_repo": {
                "domain": "blocked_hacs",
                "repository_url": "https://github.com/owner/blocked_hacs_repo",
            }
        }

        # Try to install (this should fail during _do_install)
        result = await manager._do_install(
            full_name_or_domain="owner/blocked_hacs_repo",
            source="hacs_default",
        )

        assert result is False


class TestInitialUnsupportedRepoEntry:
    """Test the initial unsupported repos entry for alexa_media_player."""

    def test_initial_entry_in_data_file(self):
        """Test that the initial alexa_media_player entry exists in the data file."""
        # File is in app/metadata/ (next to shim code)
        # Path: tests/file.py -> tests/ -> app/ -> app/metadata/
        data_file = Path(__file__).parent.parent / "metadata" / "unsupported_repos.json"

        assert data_file.exists(), f"Data file not found: {data_file}"

        with open(data_file, "r") as f:
            data = json.load(f)

        # Check for alexa_media_player entry
        assert "alandtse/alexa_media_player" in data
        entry = data["alandtse/alexa_media_player"]
        # Only "reason" field should be present
        assert "reason" in entry
        assert "full_name" not in entry
        assert "domain" not in entry
        assert "name" not in entry
        assert (
            "MQTT component doesn't support the media_player platform"
            in entry["reason"]
        )
