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
INTEGRATIONS_JSON = SCRIPT_DIR / "rootfs" / "app" / "data" / "shim" / "integrations.json"


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


def update_integration_versions(repo_status: dict) -> dict:
    """Update repo_status versions from integrations.json metadata.

    Uses the git tag version tracked by IntegrationManager (more reliable
    than manifest.json). The app's own logic handles falling back to
    manifest.json versions for repos not tracked here.
    """
    if not INTEGRATIONS_JSON.exists():
        print("No integrations.json found, skipping metadata sync")
        return repo_status

    print("Checking integration metadata...")

    verified = repo_status.get("verified", {})
    repo_lookup = {key.lower(): key for key in verified}

    with open(INTEGRATIONS_JSON) as f:
        integrations = json.load(f)

    for domain, info in integrations.items():
        full_name = info.get("full_name")
        tag_version = info.get("version")
        if not full_name or not tag_version:
            continue

        repo_key = repo_lookup.get(full_name.lower())
        if not repo_key:
            continue

        current_version = verified[repo_key].get("version")
        if current_version != tag_version:
            print(
                f"  Updating {repo_key}: {current_version} \u2192 {tag_version}"
            )
            verified[repo_key]["version"] = tag_version

    return repo_status


def run_uv_sync() -> None:
    """Run uv sync to update dependencies."""
    os.chdir(SCRIPT_DIR / "rootfs" / "app")
    os.system("uv sync --inexact")


def main():
    version = get_version_from_config()
    sync_version_to_pyproject(version)

    with open(REPO_STATUS_FILE) as f:
        repo_status = json.load(f)

    repo_status = update_integration_versions(repo_status)

    with open(REPO_STATUS_FILE, "w") as f:
        json.dump(repo_status, f, indent=2)
        f.write("\n")

    run_uv_sync()


if __name__ == "__main__":
    main()
