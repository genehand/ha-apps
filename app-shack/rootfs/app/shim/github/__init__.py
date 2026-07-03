"""GitHub authentication package for the Shack shim.

Provides OAuth device-flow authentication so the shim can make
authenticated GitHub API requests (5,000 req/hour vs. 60 req/hour
for anonymous clients). Mirrors the device-flow HACS uses.
"""

from .auth import GitHubAuth, HACS_CLIENT_ID

__all__ = ["GitHubAuth", "HACS_CLIENT_ID"]
