#!/bin/bash
# sync-versions.sh - Sync version from config.yaml to Cargo.toml and update dependencies

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.yaml"
CARGO_FILE="$SCRIPT_DIR/Cargo.toml"

# Extract version from config.yaml
version=$(grep "^version:" "$CONFIG_FILE" | sed 's/version:[[:space:]]*//')

if [ -z "$version" ]; then
    echo "Error: Could not find version in config.yaml"
    exit 1
fi

echo "Found version: $version"

# Sync version to Cargo.toml
# Use sed to replace version = "X.Y.Z" with the new version
sed -i '' "s/^version = \"[^\"]*\"/version = \"$version\"/" "$CARGO_FILE"

echo "Synced version to Cargo.toml"

# Run cargo update
echo "Running cargo update..."
cd "$SCRIPT_DIR"
cargo update

echo "Done!"
