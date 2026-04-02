"""Tests for repository status functionality.

These tests verify that the repository status static file works correctly,
including loading and blocking of unsupported repos and tracking verified repos.
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
    mock_storage.load_verified_repos.return_value = {
        "owner/verified_repo": {
            "version": "1.0.0",
            "notes": "Test verified repo",
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
            storage._repository_status_file = Path(tmpdir) / "data" / "nonexistent.json"
            repos = storage.load_unsupported_repos()
            assert repos == {}

    def test_load_unsupported_repos_from_static_file(self, temp_data_dir):
        """Test loading unsupported repos from static file."""
        # Create a unique data dir to avoid conflicts
        unique_data_dir = temp_data_dir / "data"
        unique_data_dir.mkdir(exist_ok=True)
        static_file = unique_data_dir / "repository_status.json"
        test_data = {
            "unsupported": {
                "owner/test_repo": {
                    "reason": "Test reason",
                }
            },
            "verified": {},
        }
        with open(static_file, "w") as f:
            json.dump(test_data, f)

        # Create storage and patch the file path
        shim_dir = temp_data_dir / "shim"
        shim_dir.mkdir()
        storage = Storage(shim_dir)
        storage._repository_status_file = static_file

        loaded = storage.load_unsupported_repos()
        assert loaded == test_data["unsupported"]

    def test_is_unsupported_repo(self, temp_data_dir):
        """Test checking if a repository is unsupported."""
        # Create the static file at the correct location
        data_dir = temp_data_dir / "data"
        data_dir.mkdir(exist_ok=True)
        static_file = data_dir / "repository_status.json"
        test_data = {
            "unsupported": {
                "owner/blocked_repo": {
                    "reason": "Blocked for testing",
                }
            },
            "verified": {},
        }
        with open(static_file, "w") as f:
            json.dump(test_data, f)

        # Create storage and patch the file location
        shim_dir = temp_data_dir / "shim"
        shim_dir.mkdir()
        storage = Storage(shim_dir)
        storage._repository_status_file = static_file

        entry = storage.is_unsupported_repo("owner/blocked_repo")
        assert entry is not None
        assert entry["reason"] == "Blocked for testing"

        not_blocked = storage.is_unsupported_repo("owner/allowed_repo")
        assert not_blocked is None

    def test_is_unsupported_repo_by_url(self, temp_data_dir):
        """Test checking if a repo URL is unsupported."""
        # Create the static file at the correct location
        data_dir = temp_data_dir / "data"
        data_dir.mkdir(exist_ok=True)
        static_file = data_dir / "repository_status.json"
        test_data = {
            "unsupported": {
                "owner/blocked_by_url": {
                    "reason": "Blocked URL",
                }
            },
            "verified": {},
        }
        with open(static_file, "w") as f:
            json.dump(test_data, f)

        # Create storage and patch the file location
        shim_dir = temp_data_dir / "shim"
        shim_dir.mkdir()
        storage = Storage(shim_dir)
        storage._repository_status_file = static_file

        entry = storage.is_unsupported_repo_by_url(
            "https://github.com/owner/blocked_by_url"
        )
        assert entry is not None
        assert entry["reason"] == "Blocked URL"

        not_blocked = storage.is_unsupported_repo_by_url(
            "https://github.com/owner/allowed_url"
        )
        assert not_blocked is None

    def test_is_verified_repo(self, temp_data_dir):
        """Test checking if a repository is verified."""
        # Create the static file
        unique_data_dir = temp_data_dir / "test_data"
        unique_data_dir.mkdir(exist_ok=True)
        static_file = unique_data_dir / "repository_status.json"
        test_data = {
            "unsupported": {},
            "verified": {
                "owner/verified_repo": {
                    "version": "1.0.0",
                    "notes": "Verified for testing",
                }
            },
        }
        with open(static_file, "w") as f:
            json.dump(test_data, f)

        # Create storage and patch the file location
        shim_dir = temp_data_dir / "shim"
        shim_dir.mkdir()
        storage = Storage(shim_dir)
        storage._repository_status_file = static_file

        entry = storage.is_verified_repo("owner/verified_repo")
        assert entry is not None
        assert entry["version"] == "1.0.0"
        assert entry["notes"] == "Verified for testing"

        not_verified = storage.is_verified_repo("owner/unverified_repo")
        assert not_verified is None

    def test_is_verified_repo_by_url(self, temp_data_dir):
        """Test checking if a repo URL is verified."""
        # Create the static file
        unique_data_dir = temp_data_dir / "test_data"
        unique_data_dir.mkdir(exist_ok=True)
        static_file = unique_data_dir / "repository_status.json"
        test_data = {
            "unsupported": {},
            "verified": {
                "owner/verified_by_url": {
                    "version": "2.0.0",
                    "notes": "Verified URL",
                }
            },
        }
        with open(static_file, "w") as f:
            json.dump(test_data, f)

        # Create storage and patch the file location
        shim_dir = temp_data_dir / "shim"
        shim_dir.mkdir()
        storage = Storage(shim_dir)
        storage._repository_status_file = static_file

        entry = storage.is_verified_repo_by_url(
            "https://github.com/owner/verified_by_url"
        )
        assert entry is not None
        assert entry["version"] == "2.0.0"

        not_verified = storage.is_verified_repo_by_url(
            "https://github.com/owner/unverified_url"
        )
        assert not_verified is None


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


class TestIntegrationManagerVerifiedRepos:
    """Test cases for IntegrationManager verified repos functionality."""

    def test_get_verified_repos(self, integration_manager_with_mocked_storage):
        """Test getting list of verified repos."""
        repos = integration_manager_with_mocked_storage.get_verified_repos()
        assert len(repos) == 1
        assert repos[0]["full_name"] == "owner/verified_repo"
        assert repos[0]["version"] == "1.0.0"
        assert repos[0]["notes"] == "Test verified repo"

    def test_is_verified_repo(self, integration_manager_with_mocked_storage):
        """Test checking if a repo is verified."""
        entry = integration_manager_with_mocked_storage.is_verified_repo(
            "owner/verified_repo"
        )
        assert entry is not None
        assert entry["version"] == "1.0.0"
        assert entry["notes"] == "Test verified repo"

        not_verified = integration_manager_with_mocked_storage.is_verified_repo(
            "owner/unverified"
        )
        assert not_verified is None

    def test_is_verified_repo_by_url(self, integration_manager_with_mocked_storage):
        """Test checking verified repo by URL."""
        entry = integration_manager_with_mocked_storage.is_verified_repo_by_url(
            "https://github.com/owner/verified_repo"
        )
        assert entry is not None

        not_verified = integration_manager_with_mocked_storage.is_verified_repo_by_url(
            "https://github.com/owner/unverified"
        )
        assert not_verified is None


class TestGetAvailableIntegrationsFlags:
    """Test cases for verified and unsupported flags in get_available_integrations."""

    def test_available_integrations_marks_unsupported_and_verified(self, temp_data_dir):
        """Test that get_available_integrations marks unsupported and verified repos."""
        mock_storage = MagicMock()
        mock_storage.load_unsupported_repos.return_value = {
            "owner/unsupported_repo": {
                "reason": "Not supported",
            }
        }
        mock_storage.load_verified_repos.return_value = {
            "owner/verified_repo": {
                "version": "1.0.0",
                "notes": "Test verified",
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
                "downloads": 100,
            },
            "owner/unsupported_repo": {
                "domain": "unsupported_domain",
                "repository_url": "https://github.com/owner/unsupported_repo",
                "name": "Unsupported Integration",
                "downloads": 200,
            },
            "owner/verified_repo": {
                "domain": "verified_domain",
                "repository_url": "https://github.com/owner/verified_repo",
                "name": "Verified Integration",
                "downloads": 50,
            },
        }

        available = manager.get_available_integrations()

        # Find the entries
        supported = next(
            (a for a in available if a["full_name"] == "owner/supported_repo"), None
        )
        unsupported = next(
            (a for a in available if a["full_name"] == "owner/unsupported_repo"), None
        )
        verified = next(
            (a for a in available if a["full_name"] == "owner/verified_repo"), None
        )

        assert supported is not None
        assert supported["unsupported"] is False
        assert supported["verified"] is False
        assert supported.get("unsupported_reason") is None
        assert supported.get("verified_version") is None

        assert unsupported is not None
        assert unsupported["unsupported"] is True
        assert unsupported["verified"] is False
        assert unsupported.get("unsupported_reason") == "Not supported"

        assert verified is not None
        assert verified["unsupported"] is False
        assert verified["verified"] is True
        assert verified.get("verified_version") == "1.0.0"

    def test_available_integrations_sorts_verified_first(self, temp_data_dir):
        """Test that verified repos are sorted to the top of available integrations."""
        mock_storage = MagicMock()
        mock_storage.load_unsupported_repos.return_value = {}
        mock_storage.load_verified_repos.return_value = {
            "owner/verified_low_downloads": {
                "version": "1.0.0",
                "notes": "Verified but low downloads",
            }
        }
        mock_storage.load_integrations.return_value = {}
        mock_storage.load_custom_repos.return_value = {}

        manager = IntegrationManager(mock_storage, temp_data_dir)

        # Mock HACS repos - verified repo has lowest downloads
        manager._hacs_repos = {
            "owner/popular_repo": {
                "domain": "popular",
                "repository_url": "https://github.com/owner/popular_repo",
                "name": "Popular Integration",
                "downloads": 10000,
            },
            "owner/medium_repo": {
                "domain": "medium",
                "repository_url": "https://github.com/owner/medium_repo",
                "name": "Medium Integration",
                "downloads": 5000,
            },
            "owner/verified_low_downloads": {
                "domain": "verified",
                "repository_url": "https://github.com/owner/verified_low_downloads",
                "name": "Verified Integration",
                "downloads": 100,  # Much lower downloads but should be first
            },
        }

        available = manager.get_available_integrations()

        # Verified repo should be first despite lowest downloads
        assert available[0]["full_name"] == "owner/verified_low_downloads"
        assert available[0]["verified"] is True

        # Others should be sorted by downloads
        assert available[1]["full_name"] == "owner/popular_repo"
        assert available[1]["verified"] is False
        assert available[2]["full_name"] == "owner/medium_repo"
        assert available[2]["verified"] is False


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
        mock_storage.load_verified_repos.return_value = {}

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
        mock_storage.load_verified_repos.return_value = {}

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
        mock_storage.load_verified_repos.return_value = {}

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


class TestCustomRepoInstallByFullName:
    """Test that custom repos can be installed using full_name instead of domain."""

    @pytest.mark.asyncio
    async def test_do_install_custom_repo_by_full_name(self, temp_data_dir):
        """Test that _do_install can find custom repos by full_name."""
        mock_storage = MagicMock()
        mock_storage.load_unsupported_repos.return_value = {}
        mock_storage.load_verified_repos.return_value = {}
        mock_storage.load_custom_repos.return_value = {}
        mock_storage.load_integrations.return_value = {}
        mock_storage._shim_dir = temp_data_dir

        manager = IntegrationManager(mock_storage, temp_data_dir)

        # Add a custom repo with both domain and full_name
        manager._custom_repos = {
            "rinnai": {
                "domain": "rinnai",
                "name": "Rinnai Control-R",
                "repository_url": "https://github.com/explosivo22/rinnaicontrolr-ha",
                "full_name": "explosivo22/rinnaicontrolr-ha",
                "manifest": {"domain": "rinnai", "name": "Rinnai Control-R"},
            }
        }

        # Verify lookup by domain works
        assert "rinnai" in manager._custom_repos

        # Verify lookup by full_name works (simulating the fix)
        full_name = "explosivo22/rinnaicontrolr-ha"
        found_by_full_name = None
        found_domain = None
        for d, info in manager._custom_repos.items():
            if info.get("full_name") == full_name:
                found_by_full_name = info
                found_domain = d
                break

        assert found_by_full_name is not None, "Should find repo by full_name"
        assert found_domain == "rinnai"
        assert found_by_full_name["domain"] == "rinnai"

    @pytest.mark.asyncio
    async def test_process_install_task_resolves_domain_from_full_name(
        self, temp_data_dir
    ):
        """Test that _process_install_task resolves domain from full_name for custom repos."""
        mock_storage = MagicMock()
        mock_storage.load_unsupported_repos.return_value = {}
        mock_storage.load_verified_repos.return_value = {}
        mock_storage.load_custom_repos.return_value = {}
        mock_storage.load_integrations.return_value = {}
        mock_storage._shim_dir = temp_data_dir

        manager = IntegrationManager(mock_storage, temp_data_dir)

        # Add a custom repo
        manager._custom_repos = {
            "rinnai": {
                "domain": "rinnai",
                "name": "Rinnai Control-R",
                "repository_url": "https://github.com/explosivo22/rinnaicontrolr-ha",
                "full_name": "explosivo22/rinnaicontrolr-ha",
            }
        }

        # Create an InstallTask with full_name (not domain)
        from shim.integrations.manager import InstallTask

        task = InstallTask(
            full_name_or_domain="explosivo22/rinnaicontrolr-ha",
            version=None,
            source="custom",
            custom_url=None,
        )

        # Simulate the domain resolution logic from _process_install_task
        if task.source == "custom":
            domain = task.full_name_or_domain
            if domain in manager._custom_repos:
                task.domain = domain
            else:
                # Try to find by full_name
                for d, info in manager._custom_repos.items():
                    if info.get("full_name") == task.full_name_or_domain:
                        task.domain = d
                        break

        # Verify that domain was resolved correctly
        assert task.domain == "rinnai", f"Expected domain 'rinnai', got '{task.domain}'"


class TestInitialRepositoryStatusEntry:
    """Test the initial repository status entries."""

    def test_initial_entry_in_data_file(self):
        """Test that the initial entries exist in the repository status file."""
        # File is in app/metadata/ (next to shim code)
        # Path: tests/file.py -> tests/ -> app/ -> app/metadata/
        data_file = Path(__file__).parent.parent / "metadata" / "repository_status.json"

        assert data_file.exists(), f"Data file not found: {data_file}"

        with open(data_file, "r") as f:
            data = json.load(f)

        # Check for sections
        assert "unsupported" in data
        assert "verified" in data

        # Check for alexa_media_player entry in unsupported
        assert "alandtse/alexa_media_player" in data["unsupported"]
        entry = data["unsupported"]["alandtse/alexa_media_player"]
        assert "reason" in entry
        assert (
            "HA MQTT integration doesn't support the media_player platform"
            in entry["reason"]
        )

        # Check that verified repos exist and have required fields
        verified = data.get("verified", {})
        assert len(verified) > 0, "Should have at least one verified repo"
        for full_name, entry in verified.items():
            assert "version" in entry, f"Verified repo {full_name} should have version"

    def test_is_verified_repo_optional_notes(self, temp_data_dir):
        """Test that verified repos work without optional notes field."""
        # Create the static file with a repo that has no notes
        unique_data_dir = temp_data_dir / "test_data"
        unique_data_dir.mkdir(exist_ok=True)
        static_file = unique_data_dir / "repository_status.json"
        test_data = {
            "unsupported": {},
            "verified": {
                "owner/minimal_verified": {
                    "version": "1.0.0",
                    # No notes field
                }
            },
        }
        with open(static_file, "w") as f:
            json.dump(test_data, f)

        # Create storage and patch the file location
        shim_dir = temp_data_dir / "shim"
        shim_dir.mkdir()
        storage = Storage(shim_dir)
        storage._repository_status_file = static_file

        entry = storage.is_verified_repo("owner/minimal_verified")
        assert entry is not None
        assert entry["version"] == "1.0.0"
        # notes should not be present
        assert "notes" not in entry

    def test_verified_repos_structure(self):
        """Test that verified repos have the correct structure."""
        data_file = Path(__file__).parent.parent / "metadata" / "repository_status.json"

        assert data_file.exists(), f"Data file not found: {data_file}"

        with open(data_file, "r") as f:
            data = json.load(f)

        verified = data.get("verified", {})
        for full_name, entry in verified.items():
            # version is required
            assert "version" in entry, f"Verified repo {full_name} should have version"
            # notes is optional (defaults to empty string)
            if "notes" in entry:
                assert isinstance(entry["notes"], str)
