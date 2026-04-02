#!/bin/bash
# sync-version.sh - Sync version from config.yaml to pyproject.toml
# Usage: ./sync-version.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.yaml"
PYPROJECT_FILE="$SCRIPT_DIR/rootfs/app/pyproject.toml"

# Get version from config.yaml
VERSION=$(grep "^version:" "$CONFIG_FILE" | sed 's/version: //' | tr -d '[:space:]')

# Sync to pyproject.toml
sed -i.bak "s/^version = .*/version = \"$VERSION\"/" "$PYPROJECT_FILE" && rm -f "$PYPROJECT_FILE.bak"

echo "Synced version $VERSION to pyproject.toml"

uv --directory rootfs/app sync
