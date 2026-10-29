#!/usr/bin/env python3
"""Delete the parts of the installed Home Assistant package the shim never imports.

Home Assistant is installed as a library (``homeassistant==<pin>`` in
pyproject.toml), but the shim only imports the part of HA that stays free of its
model layer -- see ``UPSTREAM_UTIL_MODULES`` / ``UPSTREAM_HELPER_MODULES`` in
``shim/import_patch.py``.

The wheel is ~368 MB and ``components/`` alone is ~360 MB of integrations that
could not import without a running ``hass`` anyway. Nobody imports them here:
integrations are served by the shim's synthetic ``homeassistant.components``
package, not by upstream's.

This matters twice over on a Home Assistant OS box, because the runtime venv is
``/data/.venv`` and ``/data`` is the user's data partition:

    python scripts/prune_homeassistant.py /data/.venv/lib/python*/site-packages

Defaults to the running interpreter's site-packages. Accepts several targets and
is idempotent. Anything needed by UPSTREAM_* survives; the Docker build proves
that by importing them afterwards.
"""

from __future__ import annotations

import shutil
import site
import sys
from pathlib import Path

# Top-level entries of homeassistant/ we keep.
#
# `util` is kept whole on purpose: HA keeps util pure by design, the shim hands
# the real package object to integrations, and so an unknown
# `homeassistant.util.<anything>` still resolves instead of failing.
# `generated` holds the tables const.py builds from.
# `core.py` is kept for its *names*, not its model layer: shim/core.py re-exports
# HassJob/CoreState/callback machinery from it, and the upstream helpers import
# them through shim.core. It imports only const/exceptions/util/helpers.json.
KEEP_TOP_LEVEL = frozenset(
    {
        "__init__.py",
        "const.py",
        "core.py",
        "exceptions.py",
        "py.typed",
        "generated",
        "util",
        "helpers",
    }
)

# helpers/ is stripped too -- everything except these imports homeassistant.core
# (or a registry built on it), which the shim replaces.
#
# This is the *transitive* closure of UPSTREAM_HELPER_MODULES, not just that
# list: helpers.storage imports helpers.json, the registries import
# helpers.{storage,registry,singleton}, and so on. tests/test_upstream_modules.py
# checks the closure, because a missing name here is a file that exists in the
# dev venv and not in the image.
KEEP_IN_HELPERS = frozenset(
    {
        "__init__.py",
        "typing.py",
        "deprecation.py",
        "json.py",
        "storage.py",
        "dispatcher.py",
        "start.py",
        "instance_id.py",
        "issue_registry.py",
        "floor_registry.py",
        "label_registry.py",
        "category_registry.py",
        "singleton.py",
        "registry.py",
        "normalized_name_base_registry.py",
        "area_registry.py",
        "network.py",
        "http.py",
        "hassio.py",
        "debounce.py",
        "ratelimit.py",
        "location.py",
        "sun.py",
        "trace.py",
    }
)


def _size(path: Path) -> int:
    """Return the size of a file or tree, best effort."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            pass
    return total


def prune(site_packages: Path) -> int | None:
    """Prune one site-packages directory. Returns bytes removed, or None if absent."""
    ha_dir = site_packages / "homeassistant"
    if not ha_dir.is_dir():
        print(f"{site_packages}: no homeassistant package", file=sys.stderr)
        return None

    victims = [e for e in ha_dir.iterdir() if e.name not in KEEP_TOP_LEVEL]

    helpers = ha_dir / "helpers"
    if helpers.is_dir():
        victims.extend(e for e in helpers.iterdir() if e.name not in KEEP_IN_HELPERS)

    freed = sum(_size(v) for v in victims)
    for victim in victims:
        try:
            if victim.is_dir():
                shutil.rmtree(victim)
            else:
                victim.unlink()
        except OSError as err:
            print(f"could not remove {victim}: {err}", file=sys.stderr)

    return freed


def main(argv: list[str]) -> int:
    targets = [Path(arg) for arg in argv[1:]]
    if not targets:
        targets = [Path(p) for p in site.getsitepackages()]

    total = 0
    found = 0
    for target in targets:
        freed = prune(target)
        if freed is None:
            continue
        found += 1
        total += freed
        print(f"{target}: removed {freed / 1_000_000:.0f} MB")

    print(f"total removed: {total / 1_000_000:.0f} MB")

    if not found:
        print(
            "error: no homeassistant package under any target; nothing pruned "
            f"(targets: {', '.join(str(t) for t in targets)})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
