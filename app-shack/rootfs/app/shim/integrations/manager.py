"""Integration Manager for Home Assistant Shim.

Handles downloading, installing, and updating HACS integrations.
"""

import os
import sys
import re
import json
import asyncio
import importlib
import aiohttp
import zipfile
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from awesomeversion import AwesomeVersion
from awesomeversion.exceptions import AwesomeVersionException

from ..logging import get_logger
from ..storage import Storage

_LOGGER = get_logger(__name__)

# HACS CDN data endpoint
HACS_CDN_URL = "https://data-v2.hacs.xyz/integration/data.json"
# Cache HACS data for 6 hours
HACS_CACHE_DURATION_HOURS = 6
# Check for updates every 4 hours
UPDATE_CHECK_INTERVAL_HOURS = 4


@dataclass
class InstallTask:
    """Task for async installation."""

    full_name_or_domain: str  # full_name for HACS repos, domain for custom repos
    version: Optional[str]
    source: str
    custom_url: Optional[str]
    callback: Optional[callable] = None
    status: str = "pending"  # pending, downloading, installing, complete, error
    error_message: Optional[str] = None
    domain: Optional[str] = None  # Will be set after manifest is read


class IntegrationInfo:
    """Information about an integration."""

    def __init__(
        self,
        domain: str,
        name: str,
        version: str,
        description: str,
        source: str,
        repository_url: str,
        enabled: bool = False,
        installed_at: Optional[str] = None,
        last_checked: Optional[str] = None,
        latest_version: Optional[str] = None,
        update_available: bool = False,
        config_flow: bool = False,
        requirements: Optional[List[str]] = None,
        dependencies: Optional[List[str]] = None,
        full_name: Optional[str] = None,
    ):
        self.domain = domain
        self.name = name
        self.version = version
        self.description = description
        self.source = source
        self.repository_url = repository_url
        self.enabled = enabled
        self.installed_at = installed_at
        self.last_checked = last_checked
        self.latest_version = latest_version
        self.update_available = update_available
        self.config_flow = config_flow
        self.requirements = requirements or []
        self.dependencies = dependencies or []
        self.full_name = full_name  # HACS full_name (owner/repo) for HACS default repos

    def to_dict(self) -> dict:
        """Convert to dictionary for templates/API."""
        return {
            "domain": self.domain,
            "name": self.name,
            "version": self.version,
            "description": self.description or "No description",
            "source": self.source,
            "repository_url": self.repository_url,
            "enabled": self.enabled,
            "installed_at": self.installed_at,
            "last_checked": self.last_checked,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "config_flow": self.config_flow,
            "requirements": self.requirements,
            "dependencies": self.dependencies,
            "full_name": self.full_name,
        }


class IntegrationManager:
    """Manages HACS integrations for the shim."""

    def __init__(
        self,
        storage: Storage,
        shim_dir: Path,
    ):
        self._storage = storage
        self._shim_dir = Path(shim_dir)
        self._integrations_dir = self._shim_dir / "custom_components"
        self._integrations_dir.mkdir(parents=True, exist_ok=True)

        # Check if running in container (addon mode) or locally
        self._is_addon = Path("/data").exists() and Path("/data").is_dir()

        # Determine venv path: /data/.venv for addon mode, local .venv for dev mode
        if self._is_addon:
            self._venv_dir = Path("/data/.venv")
            _LOGGER.info("Running in addon mode - using /data/.venv")
        else:
            # In local dev, venv is at the app root (shim/integrations/manager.py -> ../../ -> app root)
            self._venv_dir = Path(__file__).parent.parent.parent / ".venv"
            _LOGGER.info(f"Running in local dev mode - using {self._venv_dir}")

        # Construct site-packages path and add to sys.path for imports
        # Use abiflags (e.g., 't' for free-threading) to get correct directory name
        python_version = (
            f"python{sys.version_info.major}.{sys.version_info.minor}{sys.abiflags}"
        )
        self._persistent_packages_dir = (
            self._venv_dir / "lib" / python_version / "site-packages"
        )
        # Always add the persistent packages path to sys.path
        # (even if it doesn't exist yet - it may be created later when requirements are installed)
        persistent_path_str = str(self._persistent_packages_dir)
        if persistent_path_str not in sys.path:
            sys.path.insert(0, persistent_path_str)
            importlib.invalidate_caches()
            _LOGGER.debug(
                f"Added venv packages dir to sys.path: {self._persistent_packages_dir}"
            )
        if self._persistent_packages_dir.exists():
            _LOGGER.info(f"Using venv packages dir: {self._persistent_packages_dir}")
        else:
            _LOGGER.info(
                f"Venv packages dir will be created at: {self._persistent_packages_dir}"
            )

        # Create __init__.py to make custom_components a Python package
        init_file = self._integrations_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("# custom_components package\n")

        # Add the shim_dir to Python path so custom_components can be imported
        # This is needed for platform imports like custom_components.dyson_local.fan
        if str(self._shim_dir) not in sys.path:
            sys.path.insert(0, str(self._shim_dir))
            importlib.invalidate_caches()

        self._hacs_repos: Dict[str, dict] = {}  # domain -> repo info
        self._custom_repos: Dict[str, dict] = {}  # domain -> custom repo info
        self._integrations: Dict[str, IntegrationInfo] = {}  # domain -> info
        self._hacs_etag: Optional[str] = None  # ETag for CDN caching
        self._hacs_last_fetched: Optional[datetime] = None  # Last fetch time

        # Async install queue
        self._install_queue: asyncio.Queue[InstallTask] = asyncio.Queue()
        self._install_tasks: Dict[str, InstallTask] = {}  # domain -> task
        self._install_worker_task: Optional[asyncio.Task] = None

        # Periodic update check task
        self._update_check_task: Optional[asyncio.Task] = None

        # Callback for when updates are found (set externally, e.g., by ShimManager)
        self._on_updates_found: Optional[callable] = None

        self._load_integrations()
        self._load_custom_repos()
        self._load_unsupported_repos()
        self._load_hacs_cache()

    def _load_integrations(self) -> None:
        """Load installed integrations from storage."""
        data = self._storage.load_integrations()
        for domain, info_data in data.items():
            self._integrations[domain] = IntegrationInfo(**info_data)

    def _save_integrations(self) -> None:
        """Save integrations to storage."""
        data = {domain: info.to_dict() for domain, info in self._integrations.items()}
        self._storage.save_integrations(data)

    def _load_custom_repos(self) -> None:
        """Load custom repositories from storage."""
        data = self._storage.load_custom_repos()
        for domain, repo_data in data.items():
            self._custom_repos[domain] = repo_data

    def _save_custom_repos(self) -> None:
        """Save custom repositories to storage."""
        self._storage.save_custom_repos(self._custom_repos)

    def _load_unsupported_repos(self) -> None:
        """Load unsupported repositories from static file.

        This is a read-only static file that lists repositories known to be
        incompatible with the shim.
        """
        self._unsupported_repos: Dict[str, dict] = (
            self._storage.load_unsupported_repos()
        )
        self._verified_repos: Dict[str, dict] = self._storage.load_verified_repos()

    def get_unsupported_repos(self) -> List[dict]:
        """Get all unsupported repositories."""
        return [
            {
                "full_name": full_name,
                "reason": info.get("reason", "No reason provided"),
            }
            for full_name, info in self._unsupported_repos.items()
        ]

    def is_unsupported_repo(self, full_name: str) -> Optional[dict]:
        """Check if a repository is unsupported.

        Args:
            full_name: The full name of the repository (e.g., "owner/repo")

        Returns:
            The unsupported entry if found, None otherwise.
        """
        return self._unsupported_repos.get(full_name)

    def is_unsupported_repo_by_url(self, repo_url: str) -> Optional[dict]:
        """Check if a repository URL is unsupported.

        Args:
            repo_url: The repository URL to check

        Returns:
            The unsupported entry if found, None otherwise.
        """
        for full_name, entry in self._unsupported_repos.items():
            if full_name in repo_url or entry.get("repository_url") == repo_url:
                return entry
        return None

    def get_verified_repos(self) -> List[dict]:
        """Get all verified repositories."""
        return [
            {
                "full_name": full_name,
                "version": info.get("version", "unknown"),
                "notes": info.get("notes", ""),
            }
            for full_name, info in self._verified_repos.items()
        ]

    def is_verified_repo(self, full_name: str) -> Optional[dict]:
        """Check if a repository is verified.

        Args:
            full_name: The full name of the repository (e.g., "owner/repo")

        Returns:
            The verified entry if found, None otherwise.
        """
        return self._verified_repos.get(full_name)

    def is_verified_repo_by_url(self, repo_url: str) -> Optional[dict]:
        """Check if a repository URL is verified.

        Args:
            repo_url: The repository URL to check

        Returns:
            The verified entry if found, None otherwise.
        """
        for full_name, entry in self._verified_repos.items():
            if full_name in repo_url or entry.get("repository_url") == repo_url:
                return entry
        return None

    def _load_hacs_cache(self) -> None:
        """Load HACS repository cache from disk."""
        try:
            cache_file = Path(self._storage._shim_dir) / "hacs_cache.json"
            if cache_file.exists():
                with open(cache_file, "r") as f:
                    cache = json.load(f)

                    # Check cache format version - if old format (keyed by domain), invalidate it
                    cache_version = cache.get("version", 1)
                    if cache_version < 2:
                        _LOGGER.info(
                            "HACS cache is old format (v1), invalidating to support duplicate domains"
                        )
                        self._hacs_repos = {}
                        self._hacs_etag = None
                        self._hacs_last_fetched = None
                        return

                    self._hacs_repos = cache.get("repos", {})
                    self._hacs_etag = cache.get("etag")
                    self._hacs_last_fetched = (
                        datetime.fromisoformat(cache.get("last_fetched"))
                        if cache.get("last_fetched")
                        else None
                    )
                    _LOGGER.info(
                        f"Loaded HACS cache from disk: {len(self._hacs_repos)} repos"
                    )
        except Exception as e:
            _LOGGER.warning(f"Failed to load HACS cache: {e}")
            self._hacs_repos = {}
            self._hacs_etag = None
            self._hacs_last_fetched = None

    def _save_hacs_cache(self) -> None:
        """Save HACS repository cache to disk."""
        try:
            cache_file = Path(self._storage._shim_dir) / "hacs_cache.json"
            cache = {
                "version": 2,  # Cache format version (v2 = keyed by full_name)
                "repos": self._hacs_repos,
                "etag": self._hacs_etag,
                "last_fetched": self._hacs_last_fetched.isoformat()
                if self._hacs_last_fetched
                else None,
            }
            with open(cache_file, "w") as f:
                json.dump(cache, f)
            _LOGGER.debug("Saved HACS cache to disk")
        except Exception as e:
            _LOGGER.warning(f"Failed to save HACS cache: {e}")

    async def add_custom_repository(self, repo_url: str) -> Tuple[bool, str]:
        """Add a custom repository by URL.

        Args:
            repo_url: GitHub repository URL (e.g., https://github.com/owner/repo)

        Returns:
            Tuple of (success: bool, message: str)
        """
        # Validate URL format
        if "github.com" not in repo_url:
            return False, "Only GitHub repositories are supported"

        match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", repo_url)
        if not match:
            return False, "Invalid GitHub URL format"

        owner, repo = match.groups()
        full_name = f"{owner}/{repo}"

        # Check if repository is on the unsupported list
        unsupported_entry = self.is_unsupported_repo(full_name)
        if unsupported_entry:
            reason = unsupported_entry.get("reason", "No reason provided")
            return (
                False,
                f"Repository {full_name} is not supported: {reason}",
            )

        # Check if already exists
        for domain, existing in self._custom_repos.items():
            if existing.get("repository_url") == repo_url:
                return False, f"Repository already added as {domain}"

        try:
            # Fetch repository info to validate it
            async with aiohttp.ClientSession() as session:
                repo_info = await self._fetch_repo_info(session, repo_url)

            if not repo_info:
                return (
                    False,
                    "Could not find a valid Home Assistant integration in this repository",
                )

            domain = repo_info.get("domain")
            if not domain:
                return False, "Could not determine integration domain from manifest"

            # Check if domain is already installed from a different repository
            if domain in self._integrations:
                installed = self._integrations[domain]
                if installed.repository_url != repo_url:
                    return (
                        False,
                        f"Integration {domain} is already installed from {installed.repository_url}",
                    )

            # Check if domain conflicts with existing custom repo
            if domain in self._custom_repos:
                existing = self._custom_repos[domain]
                if existing.get("repository_url") != repo_url:
                    return False, f"Custom repository for {domain} already exists"

            # Store the custom repo
            self._custom_repos[domain] = {
                "domain": domain,
                "name": repo_info.get("name", domain),
                "description": repo_info.get("description", ""),
                "repository_url": repo_url,
                "full_name": f"{owner}/{repo}",
                "added_at": datetime.now().isoformat(),
                "manifest": repo_info.get("manifest", {}),
            }

            self._save_custom_repos()
            _LOGGER.info(f"Added custom repository {domain} from {repo_url}")
            return True, f"Successfully added {domain}"

        except Exception as e:
            _LOGGER.error(f"Error adding custom repository: {e}")
            return False, f"Error adding repository: {str(e)}"

    async def remove_custom_repository(self, domain: str) -> Tuple[bool, str]:
        """Remove a custom repository.

        Args:
            domain: Integration domain to remove

        Returns:
            Tuple of (success: bool, message: str)
        """
        if domain not in self._custom_repos:
            return False, f"Custom repository {domain} not found"

        # Check if integration is installed
        if domain in self._integrations:
            return (
                False,
                f"Please remove the {domain} integration first before removing the repository",
            )

        del self._custom_repos[domain]
        self._save_custom_repos()
        _LOGGER.info(f"Removed custom repository {domain}")
        return True, f"Successfully removed {domain}"

    def get_custom_repositories(self) -> List[dict]:
        """Get all custom repositories."""
        return [
            {
                "domain": domain,
                "name": info.get("name", domain),
                "description": info.get("description", ""),
                "repository_url": info.get("repository_url", ""),
                "full_name": info.get("full_name", ""),
                "added_at": info.get("added_at"),
                "installed": domain in self._integrations,
            }
            for domain, info in self._custom_repos.items()
        ]

    async def fetch_hacs_repositories(self, force: bool = False) -> Dict[str, dict]:
        """Fetch HACS default repository list from CDN.

        Uses ETag-based caching to avoid re-downloading unchanged data.
        Data is cached locally for 6 hours.

        Args:
            force: If True, ignore cache and force refresh

        Returns:
            Dictionary of repositories keyed by domain
        """
        # Check if we need to refresh (cached data is still fresh)
        if not force and self._hacs_last_fetched:
            age = datetime.now() - self._hacs_last_fetched
            if age < timedelta(hours=HACS_CACHE_DURATION_HOURS):
                _LOGGER.debug(
                    f"Using cached HACS data ({age.total_seconds() / 3600:.1f} hours old)"
                )
                return self._hacs_repos

        try:
            headers = {}
            if self._hacs_etag and not force:
                headers["If-None-Match"] = self._hacs_etag

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    HACS_CDN_URL,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as response:
                    if response.status == 304:
                        # Data hasn't changed, update timestamp and return cached
                        _LOGGER.debug("HACS CDN data unchanged (304 Not Modified)")
                        self._hacs_last_fetched = datetime.now()
                        return self._hacs_repos

                    if response.status == 200:
                        data = await response.json()

                        # Store ETag for next request
                        self._hacs_etag = response.headers.get("ETag")
                        self._hacs_last_fetched = datetime.now()

                        # Parse CDN format: {repo_id: {domain, full_name, description, ...}}
                        repos = {}
                        for repo_id, repo_data in data.items():
                            domain = repo_data.get("domain")
                            full_name = repo_data.get("full_name")

                            if not domain or not full_name:
                                continue

                            # Build repository URL from full_name
                            repo_url = f"https://github.com/{full_name}"

                            # Extract manifest data if available
                            manifest = repo_data.get("manifest", {})

                            # Use full_name as the unique key to track all repos
                            # (allows multiple repos with same domain)
                            repos[full_name] = {
                                "domain": domain,
                                "name": (
                                    repo_data.get("manifest_name")
                                    or manifest.get("name")
                                    or domain.replace("_", " ").title()
                                ),
                                "description": repo_data.get("description", ""),
                                "repository_url": repo_url,
                                "full_name": full_name,
                                "downloads": repo_data.get("downloads", 0),
                                "stars": repo_data.get("stargazers_count", 0),
                                "topics": repo_data.get("topics", []),
                                "last_version": repo_data.get("last_version"),
                                "last_commit": repo_data.get("last_commit"),
                                "last_updated": repo_data.get("last_updated"),
                                "manifest": manifest,
                                "etag_repository": repo_data.get("etag_repository"),
                                "etag_releases": repo_data.get("etag_releases"),
                                "repo_id": repo_id,
                            }

                        self._hacs_repos = repos
                        self._save_hacs_cache()
                        _LOGGER.info(f"Fetched {len(repos)} HACS repositories from CDN")
                        return repos
                    else:
                        _LOGGER.error(
                            f"Failed to fetch HACS repos: HTTP {response.status}"
                        )
                        return {}
        except aiohttp.ClientError as e:
            _LOGGER.error(f"Network error fetching HACS repositories: {e}")
            # Return cached data if available
            if self._hacs_repos:
                _LOGGER.info("Using cached HACS repository data due to network error")
                return self._hacs_repos
            return {}
        except Exception as e:
            _LOGGER.error(f"Error fetching HACS repositories: {e}")
            # Return cached data if available
            if self._hacs_repos:
                _LOGGER.info("Using cached HACS repository data due to error")
                return self._hacs_repos
            return {}

    async def get_repo_details(self, full_name: str) -> Optional[dict]:
        """Get detailed info for a specific repository by full_name.

        Uses cached CDN data which already contains manifest details.
        Only fetches from GitHub if additional info is needed.
        """
        # Check HACS default repos first (keyed by full_name)
        if full_name in self._hacs_repos:
            repo_info = self._hacs_repos[full_name].copy()

            # Add additional computed fields
            manifest = repo_info.get("manifest", {})
            repo_info["config_flow"] = manifest.get("config_flow", False)
            repo_info["requirements"] = manifest.get("requirements", [])
            repo_info["dependencies"] = manifest.get("dependencies", [])
            repo_info["documentation"] = manifest.get("documentation", "")
            repo_info["iot_class"] = manifest.get("iot_class", "")

            return repo_info

        # Check custom repos (keyed by domain, but we can look up by full_name)
        for domain, info in self._custom_repos.items():
            if info.get("full_name") == full_name:
                repo_info = info.copy()

                # Add additional computed fields
                manifest = repo_info.get("manifest", {})
                repo_info["config_flow"] = manifest.get("config_flow", False)
                repo_info["requirements"] = manifest.get("requirements", [])
                repo_info["dependencies"] = manifest.get("dependencies", [])
                repo_info["documentation"] = manifest.get("documentation", "")
                repo_info["iot_class"] = manifest.get("iot_class", "")

                return repo_info

        return None

    def get_repos_by_domain(self, domain: str) -> List[dict]:
        """Get all repositories for a specific domain."""
        repos = []
        for full_name, info in self._hacs_repos.items():
            if info.get("domain") == domain:
                repo_copy = info.copy()
                manifest = repo_copy.get("manifest", {})
                repo_copy["config_flow"] = manifest.get("config_flow", False)
                repo_copy["requirements"] = manifest.get("requirements", [])
                repo_copy["dependencies"] = manifest.get("dependencies", [])
                repo_copy["documentation"] = manifest.get("documentation", "")
                repo_copy["iot_class"] = manifest.get("iot_class", "")
                repos.append(repo_copy)
        return repos

    async def _fetch_repo_info(
        self, session: aiohttp.ClientSession, repo_url: str
    ) -> Optional[dict]:
        """Fetch repository information including manifest.

        Follows HACS conventions:
        1. Check for hacs.json for configuration hints
        2. Look for custom_components/<domain>/manifest.json
        3. Support content_in_root for single-file integrations
        """
        if "github.com" not in repo_url:
            return None

        match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", repo_url)
        if not match:
            return None

        owner, repo = match.groups()
        branches = ["main", "master"]

        # First, check for hacs.json to get repository configuration
        hacs_config = await self._fetch_hacs_json(session, owner, repo, branches)
        content_in_root = (
            hacs_config.get("content_in_root", False) if hacs_config else False
        )

        # Try to get repository tree to find the actual directory structure
        for branch in branches:
            try:
                tree = await self._fetch_repo_tree(session, owner, repo, branch)
                if not tree:
                    continue

                if content_in_root:
                    # Manifest is in root directory
                    manifest = await self._fetch_manifest_from_path(
                        session, owner, repo, branch, "manifest.json"
                    )
                    if manifest:
                        return {
                            "domain": manifest.get("domain"),
                            "name": manifest.get("name", repo),
                            "version": manifest.get("version", "unknown"),
                            "description": manifest.get("documentation", ""),
                            "repository_url": repo_url,
                            "manifest": manifest,
                        }
                else:
                    # Look for custom_components structure
                    custom_components_dir = self._find_custom_components_dir(tree)
                    if custom_components_dir:
                        # Get the first (and usually only) subdirectory
                        integration_dir = self._get_first_subdirectory(
                            tree, custom_components_dir
                        )
                        if integration_dir:
                            manifest_path = f"{custom_components_dir}/{integration_dir}/manifest.json"
                            manifest = await self._fetch_manifest_from_path(
                                session, owner, repo, branch, manifest_path
                            )
                            if manifest:
                                return {
                                    "domain": manifest.get("domain"),
                                    "name": manifest.get("name", repo),
                                    "version": manifest.get("version", "unknown"),
                                    "description": manifest.get("documentation", ""),
                                    "repository_url": repo_url,
                                    "manifest": manifest,
                                }

                    # Fallback: try to find manifest.json anywhere in custom_components
                    manifest_path = self._find_manifest_in_custom_components(tree)
                    if manifest_path:
                        manifest = await self._fetch_manifest_from_path(
                            session, owner, repo, branch, manifest_path
                        )
                        if manifest:
                            return {
                                "domain": manifest.get("domain"),
                                "name": manifest.get("name", repo),
                                "version": manifest.get("version", "unknown"),
                                "description": manifest.get("documentation", ""),
                                "repository_url": repo_url,
                                "manifest": manifest,
                            }

            except Exception as e:
                _LOGGER.debug(
                    f"Error fetching repo info for {owner}/{repo} on {branch}: {e}"
                )
                continue

        return None

    async def _fetch_hacs_json(
        self, session: aiohttp.ClientSession, owner: str, repo: str, branches: List[str]
    ) -> Optional[dict]:
        """Fetch hacs.json configuration file."""
        for branch in branches:
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/hacs.json"
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status == 200:
                        # Use content_type=None because raw.githubusercontent.com returns text/plain
                        return await response.json(content_type=None)
            except Exception:
                continue
        return None

    async def _fetch_repo_tree(
        self, session: aiohttp.ClientSession, owner: str, repo: str, branch: str
    ) -> List[dict]:
        """Fetch repository tree using GitHub API."""
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("tree", [])
        except Exception:
            pass
        return []

    def _find_custom_components_dir(self, tree: List[dict]) -> Optional[str]:
        """Find the custom_components directory in the tree."""
        for item in tree:
            path = item.get("path", "")
            if item.get("type") == "tree" and path == "custom_components":
                return path
        return None

    def _get_first_subdirectory(
        self, tree: List[dict], parent_dir: str
    ) -> Optional[str]:
        """Get the first subdirectory within a parent directory."""
        prefix = f"{parent_dir}/"
        for item in tree:
            path = item.get("path", "")
            if (
                item.get("type") == "tree"
                and path.startswith(prefix)
                and path.count("/") == prefix.count("/")
            ):
                return path[len(prefix) :]
        return None

    def _find_manifest_in_custom_components(self, tree: List[dict]) -> Optional[str]:
        """Find manifest.json path within custom_components."""
        for item in tree:
            path = item.get("path", "")
            if path.startswith("custom_components/") and path.endswith(
                "/manifest.json"
            ):
                return path
        return None

    async def _fetch_manifest_from_path(
        self,
        session: aiohttp.ClientSession,
        owner: str,
        repo: str,
        branch: str,
        path: str,
    ) -> Optional[dict]:
        """Fetch manifest.json from a specific path."""
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    # Use content_type=None because raw.githubusercontent.com returns text/plain
                    return await response.json(content_type=None)
        except Exception:
            pass
        return None

    async def check_for_updates(self) -> List[IntegrationInfo]:
        """Check all installed integrations for updates using CDN data."""
        updates_available = []

        # Refresh CDN data if stale
        await self.fetch_hacs_repositories()

        # Build a lookup by repository_url for quick matching
        hacs_repos_by_url = {
            info.get("repository_url"): info for info in self._hacs_repos.values()
        }

        for domain, info in self._integrations.items():
            if not info.enabled:
                continue

            try:
                # Use CDN data for version checking - match by repository_url
                if info.repository_url in hacs_repos_by_url:
                    hacs_repo = hacs_repos_by_url[info.repository_url]
                    latest = hacs_repo.get("last_version")
                    if latest and self._compare_versions(info.version, latest):
                        info.latest_version = latest
                        info.update_available = True
                        updates_available.append(info)
                        _LOGGER.info(
                            f"Update available for {domain}: {info.version} -> {latest}"
                        )
                    else:
                        # No update available - clear flags
                        if info.update_available:
                            info.update_available = False
                            info.latest_version = None
                            _LOGGER.debug(
                                f"Cleared update flag for {domain} - now up to date"
                            )
                else:
                    # Fallback to GitHub API for custom repos not in CDN
                    latest = await self._get_latest_version_from_github(
                        info.repository_url
                    )
                    if latest and self._compare_versions(info.version, latest):
                        info.latest_version = latest
                        info.update_available = True
                        updates_available.append(info)
                        _LOGGER.info(
                            f"Update available for {domain}: {info.version} -> {latest}"
                        )
                    else:
                        # No update available - clear flags
                        if info.update_available:
                            info.update_available = False
                            info.latest_version = None
                            _LOGGER.debug(
                                f"Cleared update flag for {domain} - now up to date"
                            )
            except Exception as e:
                _LOGGER.warning(f"Failed to check updates for {domain}: {e}")

            info.last_checked = datetime.now().isoformat()

        self._save_integrations()
        return updates_available

    def _compare_versions(self, current: str, latest: str) -> bool:
        """Compare two versions using AwesomeVersion.

        Returns True if latest is newer than current.
        """
        try:
            current_ver = AwesomeVersion(current)
            latest_ver = AwesomeVersion(latest)
            return latest_ver > current_ver
        except AwesomeVersionException as e:
            _LOGGER.warning(f"Version comparison failed for {current} vs {latest}: {e}")
            # Fallback to simple string comparison
            return latest != current

    def set_updates_found_callback(self, callback: Optional[callable]) -> None:
        """Set callback to be called when updates are found during periodic check.

        Args:
            callback: Async or sync callable that will be called with the list
                     of IntegrationInfo objects that have updates available.
        """
        self._on_updates_found = callback
        _LOGGER.debug(f"Set updates found callback: {callback is not None}")

    async def start_background_tasks(self):
        """Start background tasks for install queue and update checking."""
        if self._install_worker_task is None or self._install_worker_task.done():
            self._install_worker_task = asyncio.create_task(self._install_worker())
            _LOGGER.debug("Started install queue worker")

        if self._update_check_task is None or self._update_check_task.done():
            self._update_check_task = asyncio.create_task(self._periodic_update_check())
            _LOGGER.debug("Started periodic update check task")

    async def stop_background_tasks(self):
        """Stop background tasks."""
        if self._install_worker_task:
            self._install_worker_task.cancel()
            try:
                await self._install_worker_task
            except asyncio.CancelledError:
                pass
            _LOGGER.info("Stopped install queue worker")

        if self._update_check_task:
            self._update_check_task.cancel()
            try:
                await self._update_check_task
            except asyncio.CancelledError:
                pass
            _LOGGER.info("Stopped periodic update check task")

    async def _install_worker(self):
        """Background worker that processes install queue."""
        while True:
            try:
                task = await self._install_queue.get()
                await self._process_install_task(task)
                self._install_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Install worker error: {e}")

    async def _process_install_task(self, task: InstallTask):
        """Process a single install task."""
        # Set initial status and determine domain early for better logging/UI
        task.status = "downloading"

        # Determine domain early for better logging and status tracking
        if (
            task.source == "hacs_default"
            and task.full_name_or_domain in self._hacs_repos
        ):
            task.domain = self._hacs_repos[task.full_name_or_domain].get("domain", "")
        elif task.source == "custom":
            # For custom repos, full_name_or_domain could be domain or full_name
            domain = task.full_name_or_domain
            if domain in self._custom_repos:
                task.domain = domain
            else:
                # Try to find by full_name
                for d, info in self._custom_repos.items():
                    if info.get("full_name") == task.full_name_or_domain:
                        task.domain = d
                        break

        _LOGGER.info(
            f"Processing install task for {task.domain or task.full_name_or_domain}"
        )

        try:
            success = await self._do_install(
                task.full_name_or_domain,
                task.version,
                task.source,
                task.custom_url,
                task,
            )
            if success:
                task.status = "complete"
                _LOGGER.info(f"Successfully installed {task.full_name_or_domain}")
            else:
                task.status = "error"
                task.error_message = "Installation failed"
                _LOGGER.error(f"Failed to install {task.full_name_or_domain}")
        except Exception as e:
            task.status = "error"
            task.error_message = str(e)
            _LOGGER.error(f"Exception installing {task.full_name_or_domain}: {e}")
        finally:
            # Call callback if provided
            if task.callback:
                try:
                    task.callback(
                        task.full_name_or_domain, task.status, task.error_message
                    )
                except Exception as e:
                    _LOGGER.error(f"Install callback error: {e}")

    def queue_install(
        self,
        full_name_or_domain: str,
        version: Optional[str] = None,
        source: str = "hacs_default",
        custom_url: Optional[str] = None,
        callback: Optional[callable] = None,
    ) -> InstallTask:
        """Queue an integration for async installation.

        Returns immediately with a task object that can be used to track progress.
        """
        task = InstallTask(
            full_name_or_domain=full_name_or_domain,
            version=version,
            source=source,
            custom_url=custom_url,
            callback=callback,
        )
        self._install_tasks[full_name_or_domain] = task
        self._install_queue.put_nowait(task)
        _LOGGER.info(f"Queued {full_name_or_domain} for installation")
        return task

    def get_install_status(self, full_name_or_domain: str) -> Optional[InstallTask]:
        """Get the current installation status for a full_name or domain."""
        return self._install_tasks.get(full_name_or_domain)

    async def _periodic_update_check(self):
        """Periodically check for updates."""
        while True:
            try:
                await asyncio.sleep(UPDATE_CHECK_INTERVAL_HOURS * 3600)
                _LOGGER.info("Running periodic update check")
                updates = await self.check_for_updates()
                if updates:
                    _LOGGER.info(f"Found {len(updates)} available updates")
                    # Notify callback if set (e.g., to publish MQTT update entity)
                    if self._on_updates_found:
                        try:
                            if asyncio.iscoroutinefunction(self._on_updates_found):
                                await self._on_updates_found(updates)
                            else:
                                self._on_updates_found(updates)
                        except Exception as e:
                            _LOGGER.error(f"Error in updates found callback: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Periodic update check error: {e}")

    async def _get_latest_version_from_github(self, repo_url: str) -> Optional[str]:
        """Get latest release version from GitHub (fallback for custom repos)."""
        if "github.com" not in repo_url:
            return None

        match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", repo_url)
        if not match:
            return None

        owner, repo = match.groups()
        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    api_url, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("tag_name", "").lstrip("v")
                    elif response.status == 404:
                        # No releases, try tags
                        tags_url = f"https://api.github.com/repos/{owner}/{repo}/tags"
                        async with session.get(tags_url) as tags_response:
                            if tags_response.status == 200:
                                tags = await tags_response.json()
                                if tags:
                                    return tags[0]["name"].lstrip("v")
        except Exception as e:
            _LOGGER.debug(f"Error fetching latest version: {e}")

        return None

    async def install_integration(
        self,
        full_name_or_domain: str,
        version: Optional[str] = None,
        source: str = "hacs_default",
        custom_url: Optional[str] = None,
        wait: bool = False,
    ) -> Union[bool, InstallTask]:
        """Queue an integration for installation.

        Args:
            full_name_or_domain: For HACS repos, the full_name (e.g., "owner/repo").
                               For custom repos, the domain.
            version: Optional specific version to install
            source: "hacs_default" or "custom"
            custom_url: Direct URL for custom repos not in the list
            wait: If True, block until installation completes (legacy behavior)

        Returns:
            InstallTask for async tracking, or bool for legacy blocking installs.
        """
        if wait:
            # Synchronous (blocking) install - old behavior
            return await self._do_install(
                full_name_or_domain, version, source, custom_url
            )

        # Async install - queue and return task immediately
        task = self.queue_install(full_name_or_domain, version, source, custom_url)
        return task

    async def _do_install(
        self,
        full_name_or_domain: str,
        version: Optional[str] = None,
        source: str = "hacs_default",
        custom_url: Optional[str] = None,
        task: Optional[InstallTask] = None,
    ) -> bool:
        """Perform the actual installation (used by queue worker).

        Args:
            full_name_or_domain: For HACS repos, the full_name (e.g., "owner/repo").
                               For custom repos, the domain.
            task: Optional InstallTask to update with progress/domain info.
        """
        if source == "hacs_default":
            # For HACS default repos, full_name_or_domain is the full_name
            full_name = full_name_or_domain

            # Check if repository is on the unsupported list
            unsupported_entry = self.is_unsupported_repo(full_name)
            if unsupported_entry:
                reason = unsupported_entry.get("reason", "No reason provided")
                _LOGGER.error(
                    f"Cannot install {full_name}: Repository is not supported - {reason}"
                )
                return False

            if full_name in self._hacs_repos:
                repo_info = self._hacs_repos[full_name]
                repo_url = repo_info["repository_url"]
                domain = repo_info.get("domain", "")
            else:
                _LOGGER.error(
                    f"Repository {full_name} not found in HACS default repositories"
                )
                return False
        elif source == "custom":
            # For custom repos, full_name_or_domain could be either the domain or full_name
            # Try to find the repo by domain first, then by full_name
            repo_info = None
            domain = full_name_or_domain

            if domain in self._custom_repos:
                repo_info = self._custom_repos[domain]
                repo_url = repo_info["repository_url"]
            else:
                # Try to find by full_name
                for d, info in self._custom_repos.items():
                    if info.get("full_name") == full_name_or_domain:
                        repo_info = info
                        domain = d
                        repo_url = info["repository_url"]
                        break

            if repo_info:
                pass  # repo_url already set above
            elif custom_url:
                # Direct URL provided
                repo_url = custom_url
                repo_info = await self._fetch_repo_info_custom(repo_url)
                if not repo_info:
                    _LOGGER.error(f"Failed to fetch info for {repo_url}")
                    return False
                domain = repo_info.get("domain", domain)
            else:
                _LOGGER.error(f"Custom repository {full_name_or_domain} not found")
                return False
        else:
            repo_url = custom_url
            repo_info = await self._fetch_repo_info_custom(repo_url)
            if not repo_info:
                _LOGGER.error(f"Failed to fetch info for {repo_url}")
                return False
            domain = repo_info.get("domain", "")

        try:
            _LOGGER.info(f"Installing {domain} from {repo_url}")

            # Download and extract - this returns the actual domain from manifest
            actual_domain = await self._download_integration(repo_url, domain, version)

            # Update task domain with actual domain from manifest for accurate status tracking
            if task:
                task.domain = actual_domain
                _LOGGER.debug(f"Updated task domain to {actual_domain} from manifest")

            # Load manifest from the actual domain folder
            manifest = self._load_manifest(actual_domain)
            if not manifest:
                _LOGGER.error(f"Failed to load manifest for {actual_domain}")
                return False

            # Preserve enabled state from existing integration if present
            existing_info = self._integrations.get(actual_domain)
            was_enabled = existing_info.enabled if existing_info else False

            # Get installed version from manifest
            installed_version = manifest.get("version", "unknown")

            # Warn if installed version doesn't match requested version
            if version and installed_version != version:
                _LOGGER.warning(
                    f"Version mismatch for {actual_domain}: requested {version}, "
                    f"got {installed_version}"
                )

            # Update integration info using actual domain
            info = IntegrationInfo(
                domain=actual_domain,
                name=manifest.get("name", actual_domain),
                version=installed_version,
                description=manifest.get("documentation", ""),
                source=source,
                repository_url=repo_url,
                enabled=was_enabled,
                installed_at=datetime.now().isoformat(),
                latest_version=version or installed_version,
                config_flow=manifest.get("config_flow", False),
                requirements=manifest.get("requirements", []),
                dependencies=manifest.get("dependencies", []),
                full_name=full_name if source == "hacs_default" else None,
            )

            self._integrations[actual_domain] = info
            self._save_integrations()

            # Update status to installing while we install requirements
            if task:
                task.status = "installing"
                _LOGGER.debug(
                    f"Updated task status to 'installing' for {actual_domain}"
                )

            # Install requirements
            await self.install_requirements(actual_domain)

            _LOGGER.info(f"Successfully installed {actual_domain}@{info.version}")
            return True

        except Exception as e:
            _LOGGER.error(f"Failed to install {domain}: {e}")
            return False

    async def _fetch_repo_info_custom(self, repo_url: str) -> Optional[dict]:
        """Fetch repo info for custom URL."""
        async with aiohttp.ClientSession() as session:
            return await self._fetch_repo_info(session, repo_url)

    async def _download_integration(
        self, repo_url: str, domain: str, version: Optional[str] = None
    ) -> str:
        """Download integration from GitHub.

        Returns:
            The actual domain from the integration's manifest
        """
        match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", repo_url)
        if not match:
            raise ValueError(f"Invalid GitHub URL: {repo_url}")

        owner, repo = match.groups()

        # Construct download URL
        if version:
            download_url = (
                f"https://github.com/{owner}/{repo}/archive/refs/tags/{version}.zip"
            )
        else:
            download_url = (
                f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip"
            )

        # Download to temp location
        temp_dir = self._shim_dir / "temp"
        temp_dir.mkdir(exist_ok=True)
        zip_path = temp_dir / f"{domain}.zip"
        extract_dir: Path | None = None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    download_url, timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    if response.status == 200:
                        with open(zip_path, "wb") as f:
                            f.write(await response.read())
                    else:
                        # Try master branch if main fails
                        if not version:
                            download_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip"
                            async with session.get(download_url) as response2:
                                if response2.status == 200:
                                    with open(zip_path, "wb") as f:
                                        f.write(await response2.read())
                                else:
                                    raise Exception(
                                        f"Failed to download: HTTP {response.status}"
                                    )
                        else:
                            raise Exception(
                                f"Failed to download: HTTP {response.status}"
                            )

            # Extract
            extract_dir = temp_dir / f"{domain}_extract"
            if extract_dir.exists():
                shutil.rmtree(extract_dir)

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)

            # Find custom_components folder
            custom_components_dir = None
            for root, dirs, files in os.walk(extract_dir):
                if "custom_components" in dirs:
                    custom_components_dir = Path(root) / "custom_components"
                    break

            if not custom_components_dir:
                raise Exception("No custom_components folder found in archive")

            # Find the integration folder in custom_components
            source_dir = None
            for item in custom_components_dir.iterdir():
                if item.is_dir():
                    source_dir = item
                    break

            if not source_dir or not source_dir.exists():
                raise Exception(
                    f"Could not find integration folder in custom_components"
                )

            # Read manifest to get the actual domain
            manifest_path = source_dir / "manifest.json"
            if not manifest_path.exists():
                raise Exception("No manifest.json found in integration folder")

            with open(manifest_path, "r") as f:
                manifest = json.load(f)
                actual_domain = manifest.get("domain", domain)

            # Install using the actual domain as the folder name (HACS convention)
            integration_dir = self._integrations_dir / actual_domain
            if integration_dir.exists():
                shutil.rmtree(integration_dir)

            shutil.copytree(source_dir, integration_dir)
            _LOGGER.info(f"Installed {domain} as {actual_domain} from manifest")

            return actual_domain

        finally:
            # Cleanup
            if zip_path.exists():
                zip_path.unlink()
            if extract_dir and extract_dir.exists():
                shutil.rmtree(extract_dir)

    def _load_manifest(self, domain: str) -> Optional[dict]:
        """Load manifest.json for an integration."""
        manifest_path = self._integrations_dir / domain / "manifest.json"
        if not manifest_path.exists():
            return None

        try:
            with open(manifest_path, "r") as f:
                return json.load(f)
        except Exception as e:
            _LOGGER.error(f"Error loading manifest for {domain}: {e}")
            return None

    def get_integration(self, domain: str) -> Optional[IntegrationInfo]:
        """Get integration info by domain."""
        return self._integrations.get(domain)

    def get_all_integrations(self) -> List[IntegrationInfo]:
        """Get all registered integrations."""
        return list(self._integrations.values())

    def get_enabled_integrations(self) -> List[IntegrationInfo]:
        """Get all enabled integrations."""
        return [info for info in self._integrations.values() if info.enabled]

    def update_integration_field(self, domain: str, **kwargs) -> bool:
        """Update specific fields of an integration.

        Args:
            domain: The integration domain
            **kwargs: Fields to update (e.g., full_name="owner/repo")

        Returns:
            True if updated successfully, False if integration not found
        """
        info = self._integrations.get(domain)
        if not info:
            return False

        # Update the specified fields
        for key, value in kwargs.items():
            if hasattr(info, key):
                setattr(info, key, value)

        # Save to storage
        self._save_integrations()
        return True

    def resolve_full_name_by_url(self, repository_url: str) -> Optional[str]:
        """Resolve full_name by matching repository_url in HACS repos.

        This is used for backwards compatibility with existing integrations
        that were installed before the full_name field was added.

        Args:
            repository_url: The repository URL to match (e.g., "https://github.com/owner/repo")

        Returns:
            The full_name (e.g., "owner/repo") or None if not found
        """
        if not repository_url:
            return None

        # Normalize URL for comparison
        normalized_url = repository_url.rstrip("/")

        for full_name, info in self._hacs_repos.items():
            repo_url = info.get("repository_url", "")
            if repo_url.rstrip("/") == normalized_url:
                return full_name

        return None

    def get_available_integrations(self) -> List[dict]:
        """Get integrations available from HACS with rich metadata."""
        integrations = []

        # Build a set of installed repository URLs for quick lookup
        installed_repos = {info.repository_url for info in self._integrations.values()}

        for full_name, info in self._hacs_repos.items():
            domain = info.get("domain", "")
            repo_url = info.get("repository_url", "")
            # Check if this repo is unsupported or verified
            unsupported_entry = self.is_unsupported_repo(full_name)
            verified_entry = self.is_verified_repo(full_name)
            integration = {
                "full_name": full_name,  # Unique identifier for install links
                "domain": domain,
                "name": info.get("name", domain),
                "description": info.get("description", ""),
                "installed": repo_url in installed_repos,
                "unsupported": bool(unsupported_entry),
                "unsupported_reason": unsupported_entry.get("reason")
                if unsupported_entry
                else None,
                "verified": bool(verified_entry),
                "verified_version": verified_entry.get("version")
                if verified_entry
                else None,
                "source": "hacs_default",
                # Rich metadata from CDN
                "repository_url": repo_url,
                "downloads": info.get("downloads", 0),
                "stars": info.get("stars", 0),
                "topics": info.get("topics", []),
                "last_version": info.get("last_version"),
                "last_commit": info.get("last_commit"),
                "last_updated": info.get("last_updated"),
            }
            integrations.append(integration)

        # Add custom repositories
        for domain, info in self._custom_repos.items():
            full_name = info.get("full_name", "")
            repo_url = info.get("repository_url", "")
            # Check if this custom repo is unsupported or verified
            unsupported_entry = self.is_unsupported_repo(full_name)
            verified_entry = self.is_verified_repo(full_name)
            integration = {
                "full_name": full_name,
                "domain": domain,
                "name": info.get("name", domain),
                "description": info.get("description", ""),
                "installed": repo_url in installed_repos,
                "unsupported": bool(unsupported_entry),
                "unsupported_reason": unsupported_entry.get("reason")
                if unsupported_entry
                else None,
                "verified": bool(verified_entry),
                "verified_version": verified_entry.get("version")
                if verified_entry
                else None,
                "source": "custom",
                "repository_url": repo_url,
                "downloads": 0,
                "stars": 0,
                "topics": [],
                "last_version": info.get("manifest", {}).get("version"),
                "last_commit": None,
                "last_updated": None,
            }
            integrations.append(integration)

        # Sort by verified first, then by downloads (most popular first)
        integrations.sort(
            key=lambda x: (-int(x.get("verified", False)), -x.get("downloads", 0))
        )

        return integrations

    async def enable_integration(self, domain: str) -> bool:
        """Enable an integration."""
        if domain not in self._integrations:
            _LOGGER.error(f"Integration {domain} not installed")
            return False

        self._integrations[domain].enabled = True
        self._save_integrations()
        _LOGGER.info(f"Enabled integration {domain}")
        return True

    async def disable_integration(self, domain: str) -> bool:
        """Disable an integration."""
        if domain not in self._integrations:
            _LOGGER.error(f"Integration {domain} not installed")
            return False

        self._integrations[domain].enabled = False
        self._save_integrations()
        _LOGGER.info(f"Disabled integration {domain}")
        return True

    async def remove_integration(self, domain: str) -> bool:
        """Remove an integration completely."""
        # Uninstall requirements before removing
        await self.uninstall_requirements(domain)

        if domain in self._integrations:
            del self._integrations[domain]
            self._save_integrations()

        # Remove files
        integration_dir = self._integrations_dir / domain
        if integration_dir.exists():
            shutil.rmtree(integration_dir)

        _LOGGER.info(f"Removed integration {domain}")
        return True

    def integration_exists(self, domain: str) -> bool:
        """Check if integration files exist."""
        return (self._integrations_dir / domain / "manifest.json").exists()

    def get_integration_path(self, domain: str) -> Optional[Path]:
        """Get path to integration directory."""
        path = self._integrations_dir / domain
        if path.exists():
            return path
        return None

    async def install_requirements(self, domain: str) -> bool:
        """Install Python requirements for an integration using uv."""
        info = self._integrations.get(domain)
        if not info:
            _LOGGER.warning(f"Integration {domain} not found in _integrations")
            return True

        if not info.requirements:
            _LOGGER.debug(f"Integration {domain} has no requirements to install")
            return True

        _LOGGER.debug(f"Integration {domain} has requirements: {info.requirements}")

        # Use system uv with the venv's Python
        uv_cmd = "uv"
        venv_python = self._venv_dir / "bin" / "python"
        uv_env = os.environ.copy()

        installed_any = False

        for requirement in info.requirements:
            try:
                # Parse package name (without version specifiers)
                pkg_name = (
                    requirement.split("==")[0].split(">=")[0].split("<=")[0].strip()
                )

                # Check if requirement is already installed using uv pip show
                check_cmd = [
                    uv_cmd,
                    "pip",
                    "show",
                    pkg_name,
                    "--python",
                    str(venv_python),
                ]

                check_proc = await asyncio.create_subprocess_exec(
                    *check_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=uv_env,
                )
                stdout, _ = await check_proc.communicate()

                if check_proc.returncode == 0:
                    # Package is installed, check version if specified
                    if (
                        "==" in requirement
                        or ">=" in requirement
                        or "<=" in requirement
                    ):
                        # Parse installed version from uv pip show output
                        installed_version = None
                        for line in stdout.decode().strip().split("\n"):
                            if line.startswith("Version:"):
                                installed_version = line.split(":", 1)[1].strip()
                                break

                        if installed_version:
                            # Check if version matches
                            req_clean = requirement.strip()
                            if req_clean.endswith(f"=={installed_version}"):
                                _LOGGER.info(
                                    f"Requirement already satisfied: {requirement}"
                                )
                                continue
                            elif "==" not in req_clean:
                                # For >= or <=, we'll let uv handle it
                                _LOGGER.info(
                                    f"Requirement {requirement} has version {installed_version} installed, checking if update needed"
                                )
                    else:
                        # No version specified, package exists
                        _LOGGER.info(f"Requirement already satisfied: {requirement}")
                        continue

                _LOGGER.info(f"Installing requirement: {requirement}")

                # Build uv pip install command
                install_cmd = [
                    uv_cmd,
                    "pip",
                    "install",
                    "--python",
                    str(venv_python),
                    requirement,
                ]

                proc = await asyncio.create_subprocess_exec(
                    *install_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=uv_env,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    _LOGGER.error(f"Failed to install {requirement}: {stderr.decode()}")
                    return False
                _LOGGER.info(f"Successfully installed {requirement}")
                _LOGGER.debug(f"uv output: {stdout.decode()}")

                installed_any = True
            except Exception as e:
                _LOGGER.error(f"Failed to install {requirement}: {e}")
                return False

        # Invalidate import cache so newly installed packages can be imported
        if installed_any:
            import importlib

            importlib.invalidate_caches()
            _LOGGER.info(
                f"Invalidated import cache after installing requirements for {domain}"
            )
        else:
            _LOGGER.debug(
                f"All requirements already satisfied for {domain}, no cache invalidation needed"
            )

        return True

    async def uninstall_requirements(self, domain: str) -> bool:
        """Uninstall Python requirements for an integration using uv.

        Called when removing an integration to clean up its dependencies.
        """
        info = self._integrations.get(domain)
        if not info:
            _LOGGER.debug(
                f"Integration {domain} not found, no requirements to uninstall"
            )
            return True

        if not info.requirements:
            _LOGGER.debug(f"Integration {domain} has no requirements to uninstall")
            return True

        _LOGGER.info(f"Uninstalling requirements for {domain}: {info.requirements}")

        # Use system uv with the venv's Python
        uv_cmd = "uv"
        venv_python = self._venv_dir / "bin" / "python"
        env = os.environ.copy()
        venv_python = self._venv_dir / "bin" / "python"
        env = os.environ.copy()

        uninstalled_any = False
        for requirement in info.requirements:
            try:
                # Extract just the package name (strip version specifiers)
                package_name = (
                    requirement.split("==")[0]
                    .split(">=")[0]
                    .split("<=")[0]
                    .split(">")[0]
                    .split("<")[0]
                    .split("~=")[0]
                    .split("!=")[0]
                    .strip()
                )

                # Check if package is installed using uv pip show
                show_cmd = [
                    uv_cmd,
                    "pip",
                    "show",
                    package_name,
                    "--python",
                    str(venv_python),
                ]

                result = await asyncio.create_subprocess_exec(
                    *show_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                stdout, _ = await result.communicate()

                if result.returncode != 0:
                    _LOGGER.debug(
                        f"Package {package_name} (from {requirement}) not installed, skipping"
                    )
                    continue

                _LOGGER.info(
                    f"Uninstalling {package_name} (from requirement: {requirement})"
                )

                # Run uv pip uninstall
                uninstall_cmd = [
                    uv_cmd,
                    "pip",
                    "uninstall",
                    package_name,
                    "--python",
                    str(venv_python),
                ]

                uninstall_proc = await asyncio.create_subprocess_exec(
                    *uninstall_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                _, stderr = await uninstall_proc.communicate()

                if uninstall_proc.returncode != 0:
                    _LOGGER.warning(
                        f"Failed to uninstall {package_name}: {stderr.decode()}"
                    )
                    continue

                _LOGGER.info(f"Successfully uninstalled {package_name}")
                uninstalled_any = True
            except Exception as e:
                _LOGGER.error(f"Error uninstalling requirement {requirement}: {e}")
                return False

        # Invalidate import cache if we uninstalled anything
        if uninstalled_any:
            import importlib

            importlib.invalidate_caches()
            _LOGGER.info(
                f"Invalidated import cache after uninstalling requirements for {domain}"
            )

        return True
