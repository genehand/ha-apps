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
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

from ..logging import get_logger
from ..storage import Storage

_LOGGER = get_logger(__name__)

# HACS CDN data endpoint
HACS_CDN_URL = "https://data-v2.hacs.xyz/integration/data.json"
# Cache HACS data for 6 hours
HACS_CACHE_DURATION_HOURS = 6


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

    def __init__(self, storage: Storage, shim_dir: Path):
        self._storage = storage
        self._shim_dir = Path(shim_dir)
        self._integrations_dir = self._shim_dir / "custom_components"
        self._integrations_dir.mkdir(parents=True, exist_ok=True)

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

        self._load_integrations()
        self._load_custom_repos()

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

            # Check if domain conflicts with HACS default
            if domain in self._hacs_repos:
                return (
                    False,
                    f"Integration {domain} is already available in HACS default repositories",
                )

            # Check if domain conflicts with existing custom repo
            if domain in self._custom_repos:
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

                            repos[domain] = {
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

    async def get_repo_details(self, domain: str) -> Optional[dict]:
        """Get detailed info for a specific repository.

        Uses cached CDN data which already contains manifest details.
        Only fetches from GitHub if additional info is needed.
        """
        # Check HACS default repos first
        if domain in self._hacs_repos:
            repo_info = self._hacs_repos[domain].copy()

            # Add additional computed fields
            manifest = repo_info.get("manifest", {})
            repo_info["config_flow"] = manifest.get("config_flow", False)
            repo_info["requirements"] = manifest.get("requirements", [])
            repo_info["dependencies"] = manifest.get("dependencies", [])
            repo_info["documentation"] = manifest.get("documentation", "")
            repo_info["iot_class"] = manifest.get("iot_class", "")

            return repo_info

        # Check custom repos
        if domain in self._custom_repos:
            repo_info = self._custom_repos[domain].copy()

            # Add additional computed fields
            manifest = repo_info.get("manifest", {})
            repo_info["config_flow"] = manifest.get("config_flow", False)
            repo_info["requirements"] = manifest.get("requirements", [])
            repo_info["dependencies"] = manifest.get("dependencies", [])
            repo_info["documentation"] = manifest.get("documentation", "")
            repo_info["iot_class"] = manifest.get("iot_class", "")

            return repo_info

        return None

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

        for domain, info in self._integrations.items():
            if not info.enabled:
                continue

            try:
                # Use CDN data for version checking
                if domain in self._hacs_repos:
                    hacs_repo = self._hacs_repos[domain]
                    latest = hacs_repo.get("last_version")
                    if latest and latest != info.version:
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
                    if latest and latest != info.version:
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
        domain: str,
        version: Optional[str] = None,
        source: str = "hacs_default",
        custom_url: Optional[str] = None,
    ) -> bool:
        """Install or update an integration."""
        if source == "hacs_default":
            if domain in self._hacs_repos:
                repo_info = self._hacs_repos[domain]
                repo_url = repo_info["repository_url"]
            elif domain in self._custom_repos:
                # Check custom repos if not in HACS defaults
                _LOGGER.info(f"Found {domain} in custom repositories")
                repo_info = self._custom_repos[domain]
                repo_url = repo_info["repository_url"]
                source = "custom"
            else:
                _LOGGER.error(
                    f"Integration {domain} not found in HACS default or custom repositories"
                )
                return False
        elif source == "custom":
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
        for domain, info in self._hacs_repos.items():
            integration = {
                "domain": domain,  # Needed for links/buttons
                "name": info.get("name", domain),
                "description": info.get("description", ""),
                "installed": domain in self._integrations,
                "source": "hacs_default",
                # Rich metadata from CDN
                "full_name": info.get("full_name", ""),
                "repository_url": info.get("repository_url", ""),
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
            integration = {
                "domain": domain,
                "name": info.get("name", domain),
                "description": info.get("description", ""),
                "installed": domain in self._integrations,
                "source": "custom",
                "full_name": info.get("full_name", ""),
                "repository_url": info.get("repository_url", ""),
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

        _LOGGER.info(f"Integration {domain} has requirements: {info.requirements}")

        import sys

        # Use the venv pip to ensure packages are installed in the right environment
        venv_pip = self._shim_dir.parent / ".venv" / "bin" / "pip"
        if venv_pip.exists():
            pip_cmd = [str(venv_pip)]
            _LOGGER.debug(f"Using venv pip: {venv_pip}")
        else:
            # Fallback to current Python's pip
            pip_cmd = [sys.executable, "-m", "pip"]
            _LOGGER.debug(f"Using system pip: {pip_cmd}")

        for requirement in info.requirements:
            try:
                _LOGGER.info(f"Installing requirement: {requirement}")
                proc = await asyncio.create_subprocess_exec(
                    *pip_cmd,
                    "install",
                    requirement,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    _LOGGER.error(f"Failed to install {requirement}: {stderr.decode()}")
                    return False
                _LOGGER.info(f"Successfully installed {requirement}")
                _LOGGER.debug(f"pip output: {stdout.decode()}")
            except Exception as e:
                _LOGGER.error(f"Failed to install {requirement}: {e}")
                return False

        # Invalidate import cache so newly installed packages can be imported
        import importlib

        importlib.invalidate_caches()
        _LOGGER.info(
            f"Invalidated import cache after installing requirements for {domain}"
        )

        return True
