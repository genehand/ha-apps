"""Guard the line between shim code and upstream Home Assistant code.

``shim/import_patch.py`` imports a fixed subset of Home Assistant verbatim:
the modules HA keeps free of its model layer (core, config_entries, entity,
registries). Those are listed in ``UPSTREAM_UTIL_MODULES`` /
``UPSTREAM_HELPER_MODULES``.

Two ways this can rot, both silent, both tested here:

1. A name stops coming from the installed package and quietly falls back to a
   local reimplementation. That is the drift the vendoring existed to remove,
   so it must fail loudly instead.
2. The upstream behaviour differs from what the old stub did. We assert the
   upstream behaviour, not ours: the stubs are gone and integrations call the
   real functions.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shim.import_patch import (
    UPSTREAM_HELPER_MODULES,
    UPSTREAM_UTIL_MODULES,
    setup_import_patching,
)

# Resolve the installed package from a module we know comes from the wheel, so
# this works whether or not the patch has been applied yet.
HA_PACKAGE_DIR = Path(pytest.importorskip("homeassistant.const").__file__).parent
PRUNE_SCRIPT = Path(__file__).parent.parent / "scripts" / "prune_homeassistant.py"


@pytest.fixture(scope="module", autouse=True)
def patched():
    """Apply the shim's import patching (idempotent, other tests do the same)."""
    setup_import_patching(MagicMock())


def _assert_from_installed_package(module) -> None:
    path = Path(module.__file__)
    assert path.is_relative_to(HA_PACKAGE_DIR), (
        f"{module.__name__} resolved to {path}, which is outside the installed "
        f"homeassistant package. A stub is shadowing it."
    )


@pytest.mark.parametrize(
    "name",
    [
        "homeassistant.const",
        "homeassistant.exceptions",
        "homeassistant.generated.entity_platforms",
    ],
)
def test_upstream_top_level_modules(name):
    """const, exceptions and the generated tables come from the wheel.

    ``homeassistant.core`` is deliberately absent: the patcher replaces it with
    ``shim.core`` (which re-exports HassJob/CoreState/callback from the wheel --
    see test_upstream_core_names below).
    """
    import importlib

    _assert_from_installed_package(importlib.import_module(name))


@pytest.mark.parametrize("name", UPSTREAM_UTIL_MODULES)
def test_upstream_util_modules(name):
    """Every declared util module comes from the wheel."""
    import importlib

    _assert_from_installed_package(importlib.import_module(f"homeassistant.util.{name}"))


@pytest.mark.parametrize("name", UPSTREAM_HELPER_MODULES)
def test_upstream_helper_modules(name):
    """Every declared helpers module comes from the wheel."""
    import importlib

    _assert_from_installed_package(
        importlib.import_module(f"homeassistant.helpers.{name}")
    )


def test_unlisted_util_submodule_still_resolves():
    """homeassistant.util is the real package, so util stays open-ended.

    The shim only enumerates the util modules it *knows* integrations import;
    the package object itself is upstream's, so anything else loads from the
    wheel instead of raising ModuleNotFoundError.
    """
    import homeassistant.util.file

    _assert_from_installed_package(homeassistant.util.file)


def test_brightness_to_value_matches_upstream():
    """Regression: the old stub ignored the range and returned the brightness.

    It was written as ``int(brightness * 255 / 255)``, so 128 on a (1,1000)
    range came back as 128 instead of ~502. Integrations that convert light
    brightness to device levels got a silently wrong number.
    """
    from homeassistant.util.color import brightness_to_value, value_to_brightness

    assert brightness_to_value((1, 1000), 255) == 1000.0
    assert brightness_to_value((1, 1000), 128) == pytest.approx(501.96, abs=0.01)
    assert brightness_to_value((1, 255), 128) == 128.0
    assert value_to_brightness((1, 1000), 1000) == 255


def test_parse_datetime_matches_upstream():
    """Regression: the old stub was ``fromisoformat`` on a Z-stripped string."""
    from homeassistant.util.dt import parse_datetime

    parsed = parse_datetime("2026-10-29T12:00:00Z")
    assert parsed is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert parse_datetime("not-a-date") is None


def test_prune_keep_list_covers_every_upstream_module():
    """scripts/prune_homeassistant.py must keep what UPSTREAM_* imports.

    The image deletes everything outside the keep-list, so adding a module here
    without extending the keep-list would produce an image that works locally
    and fails on first start.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("prune_homeassistant", PRUNE_SCRIPT)
    prune_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prune_module)

    import importlib

    names = [
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.exceptions",
        "homeassistant.generated",
        "homeassistant.generated.entity_platforms",
        *[f"homeassistant.util.{n}" for n in UPSTREAM_UTIL_MODULES],
        *[f"homeassistant.helpers.{n}" for n in UPSTREAM_HELPER_MODULES],
    ]

    # homeassistant.core is shim.core at runtime, so take its path directly.
    overrides = {"homeassistant.core": HA_PACKAGE_DIR / "core.py"}

    for name in names:
        file = overrides.get(name)
        if file is None:
            file = Path(importlib.import_module(name).__file__)
        relative = file.relative_to(HA_PACKAGE_DIR)
        top = relative.parts[0]
        assert top in prune_module.KEEP_TOP_LEVEL, (
            f"{name} lives at homeassistant/{relative}, which the image prune "
            f"deletes. Add {top!r} to KEEP_TOP_LEVEL in "
            f"scripts/prune_homeassistant.py."
        )
        if top == "helpers":
            assert relative.name in prune_module.KEEP_IN_HELPERS, (
                f"{name} lives at homeassistant/{relative}, which the image prune "
                f"deletes. Add {relative.name!r} to KEEP_IN_HELPERS in "
                f"scripts/prune_homeassistant.py."
            )


def _shadowed_helpers() -> set[str]:
    """Return the helpers modules the shim replaces with stubs.

    Synthetic modules carry no ``__file__``; anything real (parsed from the
    wheel) does. The closure below must stop at these: upstream's
    ``area_registry`` would otherwise pull the *real* ``device_registry`` (and
    with it ``frame`` and ``translation``), which is not what runs here.
    """
    import sys

    return {
        name
        for name, module in sys.modules.items()
        if name.startswith("homeassistant.helpers.")
        and name != "homeassistant.helpers"
        and getattr(module, "__file__", None) is None
    }


def _upstream_helper_closure(shadowed: set[str]) -> set[str]:
    """Return every helpers/*.py the upstream helper modules import.

    Measured in a fresh process with no shim installed, which is the only way
    to see the wheel's own dependency graph: in this process ``helpers.json``
    and friends are already imported (and the shim owns others), so sys.modules
    would answer a different question. The shadowed names are pre-registered as
    empty modules there, mirroring the stubs the patcher installs before the
    upstream imports.
    """
    import subprocess
    import sys

    names = list(UPSTREAM_HELPER_MODULES)
    code = (
        "import importlib, json, sys, types\n"
        "import homeassistant.helpers\n"
        f"for name in {sorted(shadowed)!r}:\n"
        "    sys.modules[name] = types.ModuleType(name)\n"
        "helpers = {}\n"
        f"for name in {names!r}:\n"
        "    importlib.import_module('homeassistant.helpers.' + name)\n"
        "print(json.dumps(sorted({\n"
        "    m.split('.')[2]\n"
        "    for m in sys.modules\n"
        "    if m.startswith('homeassistant.helpers.') and m.count('.') == 2\n"
        "    and getattr(sys.modules[m], '__file__', None)\n"
        "})))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(HA_PACKAGE_DIR.parent),
    )
    import json

    return {f"{name}.py" for name in json.loads(result.stdout)}


def test_prune_keep_list_covers_the_helper_closure():
    """The helpers keep-list must be the *transitive* closure, not the list.

    ``helpers.storage`` imports ``helpers.json``; every registry imports
    ``helpers.{storage,registry,singleton}``. Keeping only the names in
    UPSTREAM_HELPER_MODULES produces an image where those imports fail -- in
    /data, at boot, on a file the dev venv still has.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("prune_homeassistant", PRUNE_SCRIPT)
    prune_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prune_module)

    closure = _upstream_helper_closure(_shadowed_helpers())
    missing = closure - prune_module.KEEP_IN_HELPERS
    assert not missing, (
        f"helpers/{sorted(missing)} are imported by UPSTREAM_HELPER_MODULES but "
        f"deleted by scripts/prune_homeassistant.py. Add them to KEEP_IN_HELPERS."
    )


def test_upstream_helpers_share_the_shims_core_classes():
    """The helpers must bind the *shim's* core, not a second copy of it.

    ``import_patch`` imports the real helper files only after installing
    ``shim.core`` as ``homeassistant.core``. Reverse that order and
    ``helpers.dispatcher`` gets upstream's HassJob/HassJobType while
    ``hass._async_add_hass_job`` keeps comparing against the shim's -- two
    enums, no match, jobs silently never run.
    """
    import sys

    import homeassistant.core as core
    from homeassistant.helpers import dispatcher, registry, start, storage

    assert sys.modules["homeassistant.core"] is core
    assert core.__name__ == "shim.core"

    for module in (dispatcher, start):
        assert module.HassJob is core.HassJob
    for module in (dispatcher,):
        assert module.HassJobType is core.HassJobType
    for module in (storage, registry):
        assert module.CoreState is core.CoreState
