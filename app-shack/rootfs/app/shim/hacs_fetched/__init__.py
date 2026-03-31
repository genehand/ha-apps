"""Auto-fetched HACS compatibility files.

This package contains utility files fetched from HACS integration 2.0.5.

Key modules:
- utils/version.py: Version comparison using AwesomeVersion
- utils/validate.py: Validation schemas for manifests and hacs.json
- utils/url.py: URL builders for GitHub releases
- utils/path.py: Path safety validation
- utils/queue_manager.py: Async queue management for downloads
- utils/data.py: Data storage utilities
- utils/backup.py: Backup utilities during installation

Run `python3 fetch_hacs_files.py` to update to latest HACS release.
"""

__version__ = "2.0.5"
