"""Requirement installation: only install what is missing.

``IntegrationLoader``/``ShimManager`` call ``install_requirements`` for every
integration on every start, so the "is it already there?" check has to be
correct. It used to be a hand-rolled ``uv pip show`` + string ``endswith`` test
that only understood ``==``:

* ``PyJWT>=2.0.0`` (rinnai) never short-circuited, so ``uv pip install`` ran on
  every boot;
* ``opensky-api @ git+https://github.com/...#subdirectory=python`` (flightid)
  was passed to ``uv pip show`` verbatim, which fails, so uv re-cloned the repo
  from GitHub on every boot.

These tests pin the current behaviour: specifiers are evaluated against the
installed distribution, direct-URL requirements are tracked by the requirement
string that was installed, and everything outstanding is installed in one
``uv pip install`` call.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shim.integrations.manager import IntegrationInfo, IntegrationManager

RINNAI_REQUIREMENTS = ["aiorinnai==0.5.1", "PyJWT>=2.0.0"]
FLIGHTID_REQUIREMENTS = [
    "opensky-api @ git+https://github.com/openskynetwork/opensky-api.git"
    "#subdirectory=python"
]


class _FakeProc:
    """Minimal ``asyncio.subprocess.Process`` stand-in."""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.fixture
def temp_data_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def manager(temp_data_dir):
    storage = MagicMock()
    storage.load_integrations.return_value = {}
    return IntegrationManager(storage, temp_data_dir / "shim")


def _add_info(manager, requirements, domain="test_integration"):
    info = IntegrationInfo(
        domain=domain,
        name="Test Integration",
        version="1.0.0",
        description="Test",
        source="custom",
        repository_url="https://github.com/owner/repo",
        requirements=list(requirements),
    )
    manager._integrations[domain] = info
    return info


def _install_calls(call_log):
    return [call for call in call_log if "install" in call]


@pytest.mark.asyncio
async def test_satisfied_specifier_requirements_install_nothing(manager):
    """A ``>=`` requirement already satisfied must not trigger any uv call.

    Regression: rinnai's ``PyJWT>=2.0.0`` re-installed on every restart.
    """
    info = _add_info(manager, RINNAI_REQUIREMENTS)
    call_log = []

    async def fake_exec(*args, **kwargs):
        call_log.append(args)
        return _FakeProc()

    with patch.object(
        IntegrationManager, "_installed_version", side_effect=lambda n: {
            "aiorinnai": "0.5.1",
            "PyJWT": "2.10.1",
        }.get(n)
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ):
        assert await manager.install_requirements("test_integration") is True

    assert call_log == [], f"expected no uv subprocess, got {call_log}"
    assert info.installed_requirements == RINNAI_REQUIREMENTS


@pytest.mark.asyncio
async def test_direct_url_requirement_installed_once_then_skipped(manager):
    """A VCS requirement installs once and is skipped on later starts.

    Regression: flightid's ``opensky-api @ git+...`` re-cloned on every restart
    because ``uv pip show`` cannot be handed a URL.
    """
    info = _add_info(manager, FLIGHTID_REQUIREMENTS)
    call_log = []

    async def fake_exec(*args, **kwargs):
        call_log.append(args)
        return _FakeProc()

    # Not installed yet, nothing recorded -> one install, record written.
    with patch.object(
        IntegrationManager, "_installed_version", return_value=None
    ), patch.object(
        manager, "_save_integrations"
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ), patch(
        "shim.integrations.manager.importlib.invalidate_caches"
    ):
        assert await manager.install_requirements("test_integration") is True

    install_calls = _install_calls(call_log)
    assert len(install_calls) == 1
    assert FLIGHTID_REQUIREMENTS[0] in install_calls[0]
    assert info.installed_requirements == FLIGHTID_REQUIREMENTS

    # Next start: the package is present and the URL is on record -> no uv call.
    call_log.clear()
    with patch.object(
        IntegrationManager,
        "_installed_version",
        side_effect=lambda n: "1.0.0" if n == "opensky-api" else None,
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ):
        assert await manager.install_requirements("test_integration") is True

    assert call_log == [], f"expected no uv subprocess on restart, got {call_log}"


@pytest.mark.asyncio
async def test_changed_direct_url_is_reinstalled(manager):
    """A different URL for the same package installs again."""
    info = _add_info(manager, FLIGHTID_REQUIREMENTS)
    info.installed_requirements = [
        "opensky-api @ git+https://github.com/other/fork.git#subdirectory=python"
    ]

    async def fake_exec(*args, **kwargs):
        return _FakeProc()

    with patch.object(
        IntegrationManager,
        "_installed_version",
        side_effect=lambda n: "1.0.0" if n == "opensky-api" else None,
    ), patch.object(
        manager, "_save_integrations"
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ) as exec_mock, patch(
        "shim.integrations.manager.importlib.invalidate_caches"
    ):
        assert await manager.install_requirements("test_integration") is True

    assert exec_mock.call_count == 1
    assert info.installed_requirements == FLIGHTID_REQUIREMENTS


@pytest.mark.asyncio
async def test_outstanding_requirements_installed_in_one_call(manager):
    """Only the missing requirements go into a single ``uv pip install``."""
    info = _add_info(
        manager, ["present==1.0", "missing==2.0", "upgrade-me>=5.0", "url-pkg @ https://x/y.whl"]
    )
    call_log = []

    async def fake_exec(*args, **kwargs):
        call_log.append(args)
        return _FakeProc()

    with patch.object(
        IntegrationManager,
        "_installed_version",
        side_effect=lambda n: {
            "present": "1.0",
            "missing": None,
            "upgrade-me": "1.2",
            "url-pkg": "0.9",
        }.get(n),
    ), patch.object(
        manager, "_save_integrations"
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ), patch(
        "shim.integrations.manager.importlib.invalidate_caches"
    ):
        assert await manager.install_requirements("test_integration") is True

    install_calls = _install_calls(call_log)
    assert len(install_calls) == 1
    command = install_calls[0]
    assert "missing==2.0" in command
    assert "upgrade-me>=5.0" in command
    assert "url-pkg @ https://x/y.whl" in command
    assert "present==1.0" not in command
    assert info.installed_requirements == info.requirements


@pytest.mark.asyncio
async def test_requirement_with_non_matching_marker_is_skipped(manager):
    """A marker that excludes this environment never hits uv.

    Otherwise the package can never be "installed" and uv would be spawned on
    every single start.
    """
    _add_info(manager, ['legacy-pkg==1.0; python_version < "3.9"'])
    call_log = []

    async def fake_exec(*args, **kwargs):
        call_log.append(args)
        return _FakeProc()

    with patch.object(
        IntegrationManager, "_installed_version", return_value=None
    ), patch.object(
        manager, "_save_integrations"
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ):
        assert await manager.install_requirements("test_integration") is True

    assert call_log == [], f"expected no uv subprocess, got {call_log}"


@pytest.mark.asyncio
async def test_unparsable_requirement_uses_the_install_record(manager):
    """Editable/odd requirements install once, then rely on the record."""
    info = _add_info(manager, ["-e ."])
    call_log = []

    async def fake_exec(*args, **kwargs):
        call_log.append(args)
        return _FakeProc()

    with patch.object(
        IntegrationManager, "_installed_version", return_value=None
    ), patch.object(
        manager, "_save_integrations"
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ), patch(
        "shim.integrations.manager.importlib.invalidate_caches"
    ):
        assert await manager.install_requirements("test_integration") is True

    assert len(_install_calls(call_log)) == 1
    assert info.installed_requirements == ["-e ."]

    call_log.clear()
    with patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ):
        assert await manager.install_requirements("test_integration") is True
    assert call_log == []


@pytest.mark.asyncio
async def test_force_reinstalls_satisfied_requirements(manager):
    """``force=True`` bypasses the satisfaction check (repair path)."""
    _add_info(manager, ["present==1.0"])
    call_log = []

    async def fake_exec(*args, **kwargs):
        call_log.append(args)
        return _FakeProc()

    with patch.object(
        IntegrationManager, "_installed_version", return_value="1.0"
    ), patch.object(
        manager, "_save_integrations"
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ), patch(
        "shim.integrations.manager.importlib.invalidate_caches"
    ):
        assert (
            await manager.install_requirements("test_integration", force=True) is True
        )

    assert len(_install_calls(call_log)) == 1


@pytest.mark.asyncio
async def test_failed_install_returns_false_and_keeps_record(manager):
    """A failing uv install reports failure and does not record success."""
    info = _add_info(manager, ["missing==2.0"])

    async def failing_exec(*args, **kwargs):
        return _FakeProc(returncode=1, stderr=b"boom")

    with patch.object(
        IntegrationManager, "_installed_version", return_value=None
    ), patch.object(
        manager, "_save_integrations"
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=failing_exec,
    ):
        assert await manager.install_requirements("test_integration") is False

    assert info.installed_requirements == []


@pytest.mark.asyncio
async def test_uninstall_clears_the_install_record(manager):
    """Removing an integration drops its requirement record."""
    info = _add_info(manager, ["missing==2.0"])
    info.installed_requirements = ["missing==2.0"]

    async def fake_exec(*args, **kwargs):
        return _FakeProc()

    with patch.object(
        manager, "_save_integrations"
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ), patch(
        "shim.integrations.manager.importlib.invalidate_caches"
    ):
        assert await manager.uninstall_requirements("test_integration") is True

    assert info.installed_requirements == []


def test_install_record_survives_to_dict_round_trip(manager):
    """The record is part of the persisted integration info."""
    info = IntegrationInfo(
        domain="test_integration",
        name="Test",
        version="1.0.0",
        description="",
        source="custom",
        repository_url="https://github.com/owner/repo",
        requirements=["url-pkg @ https://x/y.whl"],
        installed_requirements=["url-pkg @ https://x/y.whl"],
    )
    restored = IntegrationInfo(**info.to_dict())
    assert restored.installed_requirements == ["url-pkg @ https://x/y.whl"]


def test_requirement_parsing_covers_manifest_forms(manager):
    """The parser understands every form bundled manifests use."""
    assert manager._parse_requirement("pypng==0.20220715.0").name == "pypng"
    assert manager._parse_requirement("libdyson-neon==1.6.0").specifier.contains("1.6.0")
    assert manager._parse_requirement("PyJWT>=2.0.0").specifier.contains("2.10.1")
    parsed_url = manager._parse_requirement(FLIGHTID_REQUIREMENTS[0])
    assert parsed_url.name == "opensky-api"
    assert parsed_url.url.startswith("git+https://github.com/openskynetwork")
    assert manager._parse_requirement("-e .") is None


@pytest.mark.asyncio
async def test_missing_package_is_not_reported_as_satisfied(manager):
    """Specifier matching never hides a package that is simply absent."""
    _add_info(manager, ["aiorinnai==0.5.1"])

    async def fake_exec(*args, **kwargs):
        return _FakeProc()

    with patch.object(
        IntegrationManager, "_installed_version", return_value=None
    ), patch.object(
        manager, "_save_integrations"
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec",
        side_effect=fake_exec,
    ) as exec_mock, patch(
        "shim.integrations.manager.importlib.invalidate_caches"
    ):
        assert await manager.install_requirements("test_integration") is True

    assert exec_mock.call_count == 1


@pytest.mark.asyncio
async def test_version_lookup_runs_off_the_event_loop(manager):
    """Installed-version lookups must not touch the filesystem on the loop.

    ``importlib.metadata.version()`` opens the dist-info ``METADATA`` file;
    reading it inline during startup made ``shim.block_async_io`` warn about a
    blocking ``open()`` inside the event loop.
    """
    import threading

    _add_info(manager, ["present==1.0"])
    loop_thread = threading.get_ident()
    lookup_threads = []

    def fake_distribution_version(name):
        lookup_threads.append(threading.get_ident())
        return "1.0"

    with patch(
        "shim.integrations.manager.distribution_version",
        side_effect=fake_distribution_version,
    ), patch.object(
        manager, "_save_integrations"
    ), patch(
        "shim.integrations.manager.asyncio.create_subprocess_exec"
    ) as exec_mock:
        assert await manager.install_requirements("test_integration") is True

    assert lookup_threads, "the installed version was never looked up"
    assert all(
        ident != loop_thread for ident in lookup_threads
    ), "metadata was read on the event loop thread"
    exec_mock.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_domain_is_a_noop(manager):
    """An unknown domain logs and reports success (previous behaviour)."""
    assert await manager.install_requirements("nope") is True
