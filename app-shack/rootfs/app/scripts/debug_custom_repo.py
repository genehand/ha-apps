#!/usr/bin/env python3
"""Debug script to trace custom repository fetching.

Usage: python3 scripts/debug_custom_repo.py <github_repo_url>

Example:
    python3 scripts/debug_custom_repo.py https://github.com/schmittx/home-assistant-leviton-decora-smart-wifi
"""

import asyncio
import aiohttp
import re
import sys
from typing import Optional, List, Dict


async def _fetch_hacs_json(
    session: aiohttp.ClientSession, owner: str, repo: str, branches: List[str]
) -> Optional[dict]:
    """Fetch hacs.json configuration file."""
    for branch in branches:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/hacs.json"
        print(f"  Fetching hacs.json from {url}")
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                print(f"  Response: {response.status}")
                if response.status == 200:
                    return await response.json(content_type=None)
        except Exception as e:
            print(f"  Error: {e}")
            continue
    return None


async def _fetch_repo_tree(
    session: aiohttp.ClientSession, owner: str, repo: str, branch: str
) -> List[Dict]:
    """Fetch repository tree using GitHub API."""
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    print(f"  Fetching tree from {url}")
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            print(f"  Response: {response.status}")
            if response.status == 200:
                data = await response.json()
                return data.get("tree", [])
    except Exception as e:
        print(f"  Error: {e}")
    return []


def _find_custom_components_dir(tree: List[Dict]) -> Optional[str]:
    """Find the custom_components directory in the tree."""
    for item in tree:
        path = item.get("path", "")
        if item.get("type") == "tree" and path == "custom_components":
            return path
    return None


def _get_first_subdirectory(tree: List[Dict], parent_dir: str) -> Optional[str]:
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


def _find_manifest_in_custom_components(tree: List[Dict]) -> Optional[str]:
    """Find manifest.json path within custom_components."""
    for item in tree:
        path = item.get("path", "")
        if path.startswith("custom_components/") and path.endswith("/manifest.json"):
            return path
    return None


async def _fetch_manifest_from_path(
    session: aiohttp.ClientSession, owner: str, repo: str, branch: str, path: str
) -> Optional[dict]:
    """Fetch manifest.json from a specific path."""
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    print(f"  Fetching manifest from {url}")
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
            print(f"  Response: {response.status}")
            if response.status == 200:
                return await response.json(content_type=None)
    except Exception as e:
        print(f"  Error: {e}")
    return None


async def debug_fetch_repo_info(repo_url: str):
    """Debug the repo info fetching process."""
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", repo_url)
    if not match:
        print(f"Error: Invalid GitHub URL format: {repo_url}")
        return False

    owner, repo = match.groups()
    branches = ["main", "master"]

    print(f"\n{'=' * 60}")
    print(f"Debugging repository: {owner}/{repo}")
    print(f"{'=' * 60}\n")

    async with aiohttp.ClientSession() as session:
        # First, check for hacs.json
        print("Step 1: Check for hacs.json")
        hacs_config = await _fetch_hacs_json(session, owner, repo, branches)
        if hacs_config:
            print(f"  Found hacs.json: {hacs_config}")
        else:
            print("  No hacs.json found")

        content_in_root = (
            hacs_config.get("content_in_root", False) if hacs_config else False
        )
        print(f"  content_in_root: {content_in_root}\n")

        # Try to get repository tree
        for branch in branches:
            print(f"Step 2: Trying branch '{branch}'")
            print("-" * 40)

            try:
                tree = await _fetch_repo_tree(session, owner, repo, branch)
                if not tree:
                    print(f"  No tree found for branch {branch}\n")
                    continue

                print(f"  Found {len(tree)} items in tree")

                if content_in_root:
                    print("  Looking for manifest in root (content_in_root=True)")
                    manifest = await _fetch_manifest_from_path(
                        session, owner, repo, branch, "manifest.json"
                    )
                    if manifest:
                        print(f"\n{'=' * 60}")
                        print("SUCCESS! Found manifest:")
                        print(f"{'=' * 60}")
                        print(f"  Domain: {manifest.get('domain')}")
                        print(f"  Name: {manifest.get('name')}")
                        print(f"  Version: {manifest.get('version')}")
                        return True
                else:
                    print("  Looking for custom_components structure")
                    custom_components_dir = _find_custom_components_dir(tree)
                    if custom_components_dir:
                        print(f"  Found custom_components directory")
                        integration_dir = _get_first_subdirectory(
                            tree, custom_components_dir
                        )
                        if integration_dir:
                            print(f"  Found integration directory: {integration_dir}")
                            manifest_path = f"{custom_components_dir}/{integration_dir}/manifest.json"
                            manifest = await _fetch_manifest_from_path(
                                session, owner, repo, branch, manifest_path
                            )
                            if manifest:
                                print(f"\n{'=' * 60}")
                                print("SUCCESS! Found manifest:")
                                print(f"{'=' * 60}")
                                print(f"  Domain: {manifest.get('domain')}")
                                print(f"  Name: {manifest.get('name')}")
                                print(f"  Version: {manifest.get('version')}")
                                return True
                    else:
                        print("  custom_components directory NOT found")

                    # Fallback
                    print("  Trying fallback method to find manifest")
                    manifest_path = _find_manifest_in_custom_components(tree)
                    if manifest_path:
                        print(f"  Found manifest via fallback: {manifest_path}")
                        manifest = await _fetch_manifest_from_path(
                            session, owner, repo, branch, manifest_path
                        )
                        if manifest:
                            print(f"\n{'=' * 60}")
                            print("SUCCESS via fallback! Found manifest:")
                            print(f"{'=' * 60}")
                            print(f"  Domain: {manifest.get('domain')}")
                            print(f"  Name: {manifest.get('name')}")
                            print(f"  Version: {manifest.get('version')}")
                            return True

                print()

            except Exception as e:
                print(f"  Error on branch {branch}: {e}\n")
                continue

    print(f"{'=' * 60}")
    print("FAILED: Could not find a valid Home Assistant integration")
    print(f"{'=' * 60}")
    return False


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    repo_url = sys.argv[1]
    success = asyncio.run(debug_fetch_repo_info(repo_url))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
