#!/usr/bin/env python3
"""sync-versions.py - Sync version from config.yaml to pyproject.toml and update integration versions."""

import json
import os
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "config.yaml"
PYPROJECT_FILE = SCRIPT_DIR / "rootfs" / "app" / "pyproject.toml"
REPO_STATUS_FILE = SCRIPT_DIR / "rootfs" / "app" / "metadata" / "repository_status.json"
INTEGRATIONS_DIR = SCRIPT_DIR / "rootfs" / "app" / "data" / "shim" / "custom_components"


def get_version_from_config() -> str:
    """Extract version from config.yaml."""
    content = CONFIG_FILE.read_text()
    match = re.search(r"^version:\s*(.+)$", content, re.MULTILINE)
    if not match:
        raise ValueError("Could not find version in config.yaml")
    return match.group(1).strip()


def sync_version_to_pyproject(version: str) -> None:
    """Sync version to pyproject.toml."""
    content = PYPROJECT_FILE.read_text()
    content = re.sub(
        r'^version = ".*"$', f'version = "{version}"', content, flags=re.MULTILINE
    )
    PYPROJECT_FILE.write_text(content)
    print(f"Synced version {version} to pyproject.toml")


def extract_github_repo(manifest: dict) -> str | None:
    """Extract GitHub repo path from manifest documentation or issue_tracker."""
    for key in ["documentation", "issue_tracker"]:
        url = manifest.get(key, "")
        if url and "github.com" in url:
            # Extract user/repo from URLs like https://github.com/user/repo/...
            match = re.search(r"github\.com/([^/]+/[^/]+)", url)
            if match:
                return match.group(1)
    return None


def update_integration_versions() -> None:
    """Check locally installed integrations and update repository_status.json."""
    if not INTEGRATIONS_DIR.exists():
        print("No integrations directory found")
        return

    print("Checking locally installed integrations...")

    # Load repository_status.json
    with open(REPO_STATUS_FILE) as f:
        repo_status = json.load(f)

    # Build case-insensitive lookup for verified repos
    verified = repo_status.get("verified", {})
    repo_lookup = {key.lower(): key for key in verified}

    updated = False

    # Scan installed integrations
    for manifest_path in INTEGRATIONS_DIR.glob("*/manifest.json"):
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)

            local_version = manifest.get("version")
            github_repo = extract_github_repo(manifest)

            if not local_version or not github_repo:
                continue

            # Case-insensitive lookup
            repo_key = repo_lookup.get(github_repo.lower())
            if not repo_key:
                continue

            current_version = verified[repo_key].get("version")

            if current_version != local_version:
                print(f"  Updating {repo_key}: {current_version} -> {local_version}")
                verified[repo_key]["version"] = local_version
                updated = True

        except Exception as e:
            print(f"  Error reading {manifest_path}: {e}")

    # Save updated repository_status.json
    if updated:
        with open(REPO_STATUS_FILE, "w") as f:
            json.dump(repo_status, f, indent=2)
            f.write("\n")


def run_uv_sync() -> None:
    """Run uv sync to update dependencies."""
    os.chdir(SCRIPT_DIR / "rootfs" / "app")
    os.system("uv sync --inexact")


def main():
    version = get_version_from_config()
    sync_version_to_pyproject(version)
    update_integration_versions()
    run_uv_sync()


if __name__ == "__main__":
    main()
