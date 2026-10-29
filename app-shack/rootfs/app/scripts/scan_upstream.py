#!/usr/bin/env python3
"""Measure what importing an upstream Home Assistant module actually drags in.

This is the admission test for the upstream tier (see docs/upstream-tier.md).
A candidate has to be measured **in a fresh process, one module at a time**: once
``homeassistant.core`` is in ``sys.modules`` every later scan looks clean, and a
warm interpreter will tell you a module is pure when it is not.

    python scripts/scan_upstream.py homeassistant.helpers.json
    python scripts/scan_upstream.py homeassistant.helpers.storage homeassistant.helpers.trace

Two readings come out of it:

* ``core`` -- the module imports ``homeassistant.core``. That is *allowed*: the
  patcher installs ``shim.core`` as ``homeassistant.core`` before the helper files
  load, so such a module binds the shim's classes. It only means the module must
  be imported late (see ``UPSTREAM_HELPER_MODULES``).
* ``blockers`` -- anything in the model layer (config_entries, loader, setup,
  bootstrap, data_entry_flow). Those cannot be satisfied without a booted hass,
  so the module needs the full model layer.

Imports are measured against the installed wheel, i.e. the module's *own* graph.
Inside the shim a sibling import may bind a shim stub instead (``area_registry``
imports ``device_registry``, which the shim replaces), so the wheel's graph is the
conservative answer: if it is clean here, it is certainly clean there.
``tests/test_upstream_modules.py`` measures the shadowed variant for the keep-list.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys

# Model-layer modules the shim replaces with its own implementation.
BLOCKERS = (
    "homeassistant.config_entries",
    "homeassistant.loader",
    "homeassistant.setup",
    "homeassistant.bootstrap",
    "homeassistant.data_entry_flow",
    "homeassistant.components",
)


def scan(name: str) -> dict[str, object]:
    """Import one module and report what it pulled in."""
    before = set(sys.modules)
    try:
        importlib.import_module(name)
    except Exception as err:  # noqa: BLE001 - the failure is the result
        return {"name": name, "error": f"{type(err).__name__}: {err}"}

    new = {m for m in set(sys.modules) - before if m.startswith("homeassistant")}
    helpers = sorted(
        {m.split(".")[2] for m in new if m.startswith("homeassistant.helpers.") and m.count(".") >= 2}
    )
    top_level = sorted({m.split(".")[1] for m in new if m.count(".") >= 1})
    return {
        "name": name,
        "pulls": len(new),
        "core": "homeassistant.core" in new,
        "blockers": [b for b in BLOCKERS if b in new],
        "helpers": helpers,
        "top_level": top_level,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="+", help="dotted module names to measure")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv[1:])

    if "shim" in sys.modules:
        print(
            "warning: the shim is already imported in this process; "
            "run one module per process for a truthful answer",
            file=sys.stderr,
        )

    results = [scan(name) for name in args.modules]

    if args.json:
        print(json.dumps(results, indent=1))
        return 0

    for result in results:
        name = result["name"]
        if error := result.get("error"):
            print(f"{name}: FAIL {error}")
            continue
        blockers = ", ".join(result["blockers"]) or "-"
        core = "core" if result["core"] else "no-core"
        print(
            f"{name}: pulls={result['pulls']:3} {core:7} blockers={blockers}"
            f"\n    helpers: {', '.join(result['helpers']) or '-'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
