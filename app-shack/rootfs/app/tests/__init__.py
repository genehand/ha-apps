"""Test configuration and fixtures for app-shack tests."""

import pytest
import sys
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Apply import_patch to ensure homeassistant modules are available
# This must happen before any shim.platforms imports in tests
from shim.import_patch import ImportPatcher

# Create patcher and apply patches for tests
# This ensures homeassistant.* stubs are available
_test_patcher = ImportPatcher(None)
_test_patcher.patch()
