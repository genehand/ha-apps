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
        }


class IntegrationManager:
    """Manages HACS integrations for the shim."""

    def __init__(
        self,
        storage: Storage,
        shim_dir: Path,
        notification_callback: Optional[callable] = None,
    ):
        self._storage = storage
        self._shim_dir = Path(shim_dir)
        self._integrations_dir = self._shim_dir / "custom_components"
        self._integrations_dir.mkdir(parents=True, exist_ok=True)

        # Check if running in container (addon mode) or locally
        self._is_addon = Path("/data").exists() and Path("/data").is_dir()

        # In container mode, use /data for persistent packages that survive restarts
        # In local dev mode, use the venv's site-packages
        if self._is_addon:
            # Use dynamic Python version in path (e.g., python3.11, python3.12)
            # Note: When using PYTHONUSERBASE=/data, pip installs to /data/lib/ not /data/.local/lib/
            python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
            self._persistent_packages_dir = Path(
                f"/data/lib/{python_version}/site-packages"
            )
            self._persistent_packages_dir.mkdir(parents=True, exist_ok=True)

            # Add persistent packages to Python path so they can be imported
            if str(self._persistent_packages_dir) not in sys.path:
                sys.path.insert(0, str(self._persistent_packages_dir))
                importlib.invalidate_caches()
            _LOGGER.debug(
                f"Using persistent packages dir: {self._persistent_packages_dir} "
                f"(Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro})"
            )
            _LOGGER.debug(
                f"sys.path includes: {[p for p in sys.path if 'site-packages' in p]}"
            )
        else:
            self._persistent_packages_dir = None
            _LOGGER.info("Running in local dev mode - using venv packages")

        # Callback for sending notifications to HA
        self._notification_callback = notification_callback

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
        self._last_notification_time: Optional[datetime] = None

        self._load_integrations()
        self._load_custom_repos()
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
        task.status = "downloading"
        _LOGGER.info(f"Processing install task for {task.domain}")

        try:
            success = await self._do_install(
                task.full_name_or_domain, task.version, task.source, task.custom_url
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
        """Periodically check for updates and send aggregated notifications."""
        while True:
            try:
                await asyncio.sleep(UPDATE_CHECK_INTERVAL_HOURS * 3600)
                await self._check_updates_and_notify()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Periodic update check error: {e}")

    async def _check_updates_and_notify(self):
        """Check for updates and send a single aggregated notification."""
        _LOGGER.info("Running periodic update check")

        updates = await self.check_for_updates()

        if not updates:
            return

        # Build aggregated message
        update_count = len(updates)
        integration_list = ", ".join(
            [f"{u.name} ({u.version} → {u.latest_version})" for u in updates[:5]]
        )

        if update_count > 5:
            integration_list += f" and {update_count - 5} more"

        title = f"{update_count} integration update{'s' if update_count > 1 else ''} available"
        message = f"Updates available for: {integration_list}"

        _LOGGER.info(f"Update notification: {title} - {message}")

        # Send notification to HA if callback is available
        if self._notification_callback:
            try:
                await self._notification_callback(title, message)
            except Exception as e:
                _LOGGER.error(f"Failed to send update notification: {e}")

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
    ) -> bool:
        """Perform the actual installation (used by queue worker).

        Args:
            full_name_or_domain: For HACS repos, the full_name (e.g., "owner/repo").
                               For custom repos, the domain.
        """
        if source == "hacs_default":
            # For HACS default repos, full_name_or_domain is the full_name
            full_name = full_name_or_domain
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
            # For custom repos, full_name_or_domain is the domain
            domain = full_name_or_domain
            # Check custom repositories first
            if domain in self._custom_repos:
                repo_info = self._custom_repos[domain]
                repo_url = repo_info["repository_url"]
            elif custom_url:
                # Direct URL provided
                repo_url = custom_url
                repo_info = await self._fetch_repo_info_custom(repo_url)
                if not repo_info:
                    _LOGGER.error(f"Failed to fetch info for {repo_url}")
                    return False
            else:
                _LOGGER.error(f"Custom repository {domain} not found")
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

            # Load manifest from the actual domain folder
            manifest = self._load_manifest(actual_domain)
            if not manifest:
                _LOGGER.error(f"Failed to load manifest for {actual_domain}")
                return False

            # Update integration info using actual domain
            info = IntegrationInfo(
                domain=actual_domain,
                name=manifest.get("name", actual_domain),
                version=manifest.get("version", "unknown"),
                description=manifest.get("documentation", ""),
                source=source,
                repository_url=repo_url,
                installed_at=datetime.now().isoformat(),
                latest_version=version or manifest.get("version", "unknown"),
                config_flow=manifest.get("config_flow", False),
                requirements=manifest.get("requirements", []),
                dependencies=manifest.get("dependencies", []),
            )

            self._integrations[actual_domain] = info
            self._save_integrations()

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

    def get_available_integrations(self) -> List[dict]:
        """Get integrations available from HACS with rich metadata."""
        integrations = []

        # Build a set of installed repository URLs for quick lookup
        installed_repos = {info.repository_url for info in self._integrations.values()}

        for full_name, info in self._hacs_repos.items():
            domain = info.get("domain", "")
            repo_url = info.get("repository_url", "")
            integration = {
                "full_name": full_name,  # Unique identifier for install links
                "domain": domain,
                "name": info.get("name", domain),
                "description": info.get("description", ""),
                "installed": repo_url in installed_repos,
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
            integration = {
                "full_name": full_name,
                "domain": domain,
                "name": info.get("name", domain),
                "description": info.get("description", ""),
                "installed": repo_url in installed_repos,
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

        # Sort by downloads (most popular first)
        integrations.sort(key=lambda x: x.get("downloads", 0), reverse=True)

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
        """Install Python requirements for an integration."""
        info = self._integrations.get(domain)
        if not info:
            _LOGGER.warning(f"Integration {domain} not found in _integrations")
            return True

        if not info.requirements:
            _LOGGER.debug(f"Integration {domain} has no requirements to install")
            return True

        _LOGGER.debug(f"Integration {domain} has requirements: {info.requirements}")

        # Use the venv pip to ensure packages are installed in the right environment
        venv_pip = self._shim_dir.parent / ".venv" / "bin" / "pip"
        if venv_pip.exists():
            pip_cmd = [str(venv_pip)]
            _LOGGER.debug(f"Using venv pip: {venv_pip}")
        else:
            # Fallback to current Python's pip
            pip_cmd = [sys.executable, "-m", "pip"]
            _LOGGER.debug(f"Using system pip: {pip_cmd}")

        installed_any = False

        # Prepare environment for pip commands in container mode
        if self._is_addon:
            pip_env = os.environ.copy()
            pip_env["PYTHONUSERBASE"] = "/data"
        else:
            pip_env = os.environ.copy()

        for requirement in info.requirements:
            try:
                # Parse package name (without version specifiers)
                pkg_name = (
                    requirement.split("==")[0].split(">=")[0].split("<=")[0].strip()
                )

                # Check if requirement is already installed (in venv or persistent location)
                check_proc = await asyncio.create_subprocess_exec(
                    *pip_cmd,
                    "show",
                    pkg_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=pip_env,
                )
                stdout, _ = await check_proc.communicate()

                if check_proc.returncode == 0:
                    # Package is installed, check version if specified
                    if (
                        "==" in requirement
                        or ">=" in requirement
                        or "<=" in requirement
                    ):
                        # Parse installed version from pip show output
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
                                # For >= or <=, we'll let pip handle it
                                _LOGGER.info(
                                    f"Requirement {requirement} has version {installed_version} installed, checking if update needed"
                                )
                    else:
                        # No version specified, package exists
                        _LOGGER.info(f"Requirement already satisfied: {requirement}")
                        continue

                _LOGGER.info(f"Installing requirement: {requirement}")

                # Build pip command based on environment
                if self._is_addon:
                    # In container: use --user with PYTHONUSERBASE to persist across restarts
                    install_cmd = [*pip_cmd, "install", "--user", requirement]
                else:
                    # Local dev: install directly to venv without --user
                    install_cmd = [*pip_cmd, "install", requirement]

                proc = await asyncio.create_subprocess_exec(
                    *install_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=pip_env,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    _LOGGER.error(f"Failed to install {requirement}: {stderr.decode()}")
                    return False
                _LOGGER.info(f"Successfully installed {requirement}")
                _LOGGER.debug(f"pip output: {stdout.decode()}")

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
        """Uninstall Python requirements for an integration.

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

        import sys
        import subprocess

        # Get the venv pip path
        venv_path = Path(sys.executable).parent
        pip_path = venv_path / "pip"

        # Set up environment based on mode
        if self._is_addon:
            # In container: target persistent packages with PYTHONUSERBASE
            env = os.environ.copy()
            env["PYTHONUSERBASE"] = "/data"
        else:
            # Local dev: use normal environment
            env = os.environ.copy()

        uninstalled_any = False
        for requirement in info.requirements:
            try:
                # Extract just the package name (strip version specifiers like ==1.0.0, >=1.0, etc.)
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

                # Check if package is installed (in venv or persistent location)
                result = subprocess.run(
                    [str(pip_path), "show", package_name],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                if result.returncode != 0:
                    _LOGGER.debug(
                        f"Package {package_name} (from {requirement}) not installed, skipping"
                    )
                    continue

                _LOGGER.info(
                    f"Uninstalling {package_name} (from requirement: {requirement})"
                )
                # Run pip uninstall
                subprocess.run(
                    [str(pip_path), "uninstall", "-y", package_name],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                _LOGGER.info(f"Successfully uninstalled {package_name}")
                uninstalled_any = True
            except subprocess.CalledProcessError as e:
                _LOGGER.warning(f"Failed to uninstall {package_name}: {e.stderr}")
                # Continue trying to uninstall other requirements
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
