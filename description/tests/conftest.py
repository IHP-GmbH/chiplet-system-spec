# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Fixtures, sibling-checkout discovery, and the strict-live helper.

Mirrors ``interposer-pnr/tests/conftest.py``: live and answer-key tests are
skip-gated, so a run on a stripped machine can silently degrade to skips and
still look green. ``HCDP_STRICT_LIVE=1`` turns any ITEM-LEVEL skip into a failure. That
qualifier is load-bearing: the hook is ``pytest_runtest_makereport``, which runs
per test item, so a module-level ``importorskip`` raises during COLLECTION and
produces no item for it to fire on. Those skips stay silent under this flag.
Guard an unavailable import with a plain import in a job that guarantees the
tool instead, so there is nothing left to skip. There is
no by-design allowlist (every live dependency -- the sibling checkouts and the
openroad binary -- is expected present at a checkpoint), so the allowlist count
printed at session end must be 0.

Two markers name the seams. ``needs_sibling_sources`` is a case that reads a
sibling checkout; ``live_openroad`` is a case that starts the openroad binary.
The default ``addopts`` deselects ``live_openroad``, so a bare ``pytest`` starts
no tool, and a live tier is ``pytest -m live_openroad`` under
``HCDP_STRICT_LIVE=1``. The seam helpers below hold each case to its marker: a
case that reaches a seam without it fails, whatever the run selected.

The spec is not a sibling: this package lives in its repository, so
``spec_path`` reads it at ``SPEC_ROOT``, the folder above this one, through no
variable and with no skip. A tree without it is a broken checkout and fails.

Each sibling is read where its own route points first: ``INTERCONNECT_PDK_ROOT``,
and for interposer-pnr the checkout its importable package was installed from.
A set variable IS the sibling, so a path missing under it is missing, never read
from the umbrella instead; an empty value counts as unset. Without its own route
a sibling sits under ``HCDP_ROOT`` when that is set, and beside the repository
otherwise. Under ``HCDP_CI=1`` nothing is guessed beside the repository, and a
case whose sibling neither route finds fails rather than skips. Nothing here is
refused at import, so such a run still collects.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

import pytest

#: This package, description/ inside the spec repository.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PACKAGE_ROOT / "tests" / "fixtures"
#: The spec repository this package lives in.
SPEC_ROOT = PACKAGE_ROOT.parent


def spec_path(rel: str) -> Path:
    """The path ``rel`` inside the spec, which must exist."""
    path = SPEC_ROOT / rel
    if not path.exists():
        pytest.fail(f"not in the spec repository: {rel} ({path})", pytrace=False)
    return path


#: The siblings with a variable of their own, by their directory under the umbrella.
SIBLING_ENV = {
    "interconnect_pdk": "INTERCONNECT_PDK_ROOT",  # the method manifest
}
INTERPOSER_PNR = "interposer-pnr"


def _umbrella() -> Optional[Path]:
    """``HCDP_ROOT`` when set, else the directory holding the spec repository; a
    CI run never guesses that, so it has no umbrella without ``HCDP_ROOT``."""
    root = os.environ.get("HCDP_ROOT", "").strip()
    if root:
        return Path(root)
    return None if ci_run() else SPEC_ROOT.parent


def _interposer_pnr_checkout() -> Optional[Path]:
    """The checkout the importable interposer_pnr comes from, when it is one."""
    try:
        import interposer_pnr
    except Exception:
        return None
    root = Path(interposer_pnr.__file__).resolve().parents[2]  # src/interposer_pnr/
    return root if (root / "tests" / "fixtures").is_dir() else None


def sibling(name: str) -> Tuple[Optional[Path], str]:
    """``(root, where)`` for the sibling ``name``: its own route when it has one,
    else the umbrella. ``root`` is None when neither route gives one."""
    env = SIBLING_ENV.get(name)
    value = os.environ.get(env, "").strip() if env else ""
    if value:
        return Path(value), f"${env}"
    own = f"${env}" if env else "the interposer_pnr import"
    if name == INTERPOSER_PNR:
        found = _interposer_pnr_checkout()
        if found is not None:
            return found, own
    umbrella = _umbrella()
    if umbrella is None:
        return None, f"neither {own} nor $HCDP_ROOT gives it (HCDP_CI=1 looks nowhere else)"
    return umbrella / name, f"the umbrella ({umbrella})"


GOLDEN_CHIPLET = FIXTURES_DIR / "ihp_two_die_route.chiplet"
PINS_JSON = FIXTURES_DIR / "two_die_route.pins.json"
WRONG_6V4 = FIXTURES_DIR / "wrong_6v4.chiplet"
#: A byte copy of the interposer PDK's stack descriptor (PROVENANCE.md beside it).
#: The examples read their mount plane under INTERPOSER_PDK_ROOT, so the fixtures
#: that build them point it here, and a clean checkout builds them without the PDK.
INTERPOSER_PDK_FIXTURE = FIXTURES_DIR / "interposer_pdk"

ROUTING_TECH = "ihp_sg13g2_intm4tm2"


SIBLING_MARKER = "needs_sibling_sources"
LIVE_MARKER = "live_openroad"

#: The case running now, so a seam helper can read its markers. Set for its
#: setup, call and teardown, which is where its fixtures run too.
_current_item: Optional[pytest.Item] = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global _current_item
    _current_item = item
    try:
        yield
    finally:
        _current_item = None


def _hold_to_marker(marker: str, seam: str) -> None:
    item = _current_item
    if item is not None and item.get_closest_marker(marker) is None:
        pytest.fail(f"{item.nodeid} reaches {seam} but is not marked {marker}",
                    pytrace=False)


def strict_live() -> bool:
    return os.environ.get("HCDP_STRICT_LIVE") == "1"


def ci_run() -> bool:
    return os.environ.get("HCDP_CI") == "1"


def require_sibling(name: str, rel: str = "") -> Path:
    """The path ``rel`` inside the sibling ``name``, which must exist."""
    _hold_to_marker(SIBLING_MARKER, f"the sibling {name}")
    root, where = sibling(name)
    path = root / rel if root is not None and rel else root
    if path is None or not path.exists():
        shown = f"{name}/{rel}" if rel else name
        message = (f"sibling checkout not present: {shown} ({where})" if path is None
                   else f"sibling checkout not present: {shown} ({path}, from {where})")
        if ci_run():
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    return path


def require_interposer_pnr() -> None:
    """The interposer_pnr package, installed or else a sibling checkout."""
    _hold_to_marker(SIBLING_MARKER, "the sibling interposer-pnr")
    if not interposer_pnr_importable():
        require_sibling(INTERPOSER_PNR)
        message = "interposer_pnr not importable"
        if ci_run():
            pytest.fail(message, pytrace=False)
        pytest.skip(message)


def require_strict_live_gate() -> None:
    """Skip a live-tool test outside strict-live mode (keeps Tier 1 hermetic)."""
    _hold_to_marker(LIVE_MARKER, "the openroad binary")
    if not strict_live():
        pytest.skip("live-tool test; runs only under HCDP_STRICT_LIVE=1")


def openroad_bin() -> Optional[str]:
    _hold_to_marker(LIVE_MARKER, "the openroad binary")
    from interposer_pnr.router import find_openroad

    return find_openroad()


def interposer_pnr_importable() -> bool:
    try:
        import interposer_pnr  # noqa: F401
    except Exception:
        return False
    return True


def run_ipnr(*args: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Invoke ``python -m interposer_pnr`` in this interpreter's venv."""
    _hold_to_marker(LIVE_MARKER, "the interposer_pnr command")
    cmd = [sys.executable, "-m", "interposer_pnr", *args]
    return subprocess.run(
        cmd, cwd=str(cwd or PACKAGE_ROOT), capture_output=True, text=True
    )


def route_report(chiplet: Path, work: Path) -> dict:
    """Run bumps + route on ``chiplet`` and return the parsed route report."""
    import json

    work.mkdir(parents=True, exist_ok=True)
    bumps = work / "bumps.json"
    r = run_ipnr(
        "bumps", "--chiplet", str(chiplet),
        "--pins", f"U1={PINS_JSON}", "--pins", f"U2={PINS_JSON}",
        "--out", str(bumps),
    )
    assert r.returncode == 0, f"bumps failed:\n{r.stdout}\n{r.stderr}"
    report = work / "route.json"
    r = run_ipnr(
        "route", "--chiplet", str(chiplet), "--tech", ROUTING_TECH,
        "--bumps", str(bumps), "--work-dir", str(work),
        "--out", str(work / "traces.json"), "--report", str(report),
    )
    assert r.returncode == 0, f"route failed:\n{r.stdout}\n{r.stderr}"
    return json.loads(report.read_text())


@pytest.fixture()
def built_system():
    sys.path.insert(0, str(PACKAGE_ROOT))
    from examples.two_die_ihp import build_system

    return _on_the_pdk_fixture(build_system)


@pytest.fixture()
def built_typed_system():
    sys.path.insert(0, str(PACKAGE_ROOT))
    from examples.two_die_typed import build_system

    return _on_the_pdk_fixture(build_system)


def _on_the_pdk_fixture(build_system):
    """Build with INTERPOSER_PDK_ROOT on the vendored descriptor, only while building,
    so no interposer-pnr run a test starts afterwards sees it."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("INTERPOSER_PDK_ROOT", str(INTERPOSER_PDK_FIXTURE))
        return build_system()


@pytest.fixture(scope="session")
def golden_route_report():
    """One strict-live golden route run, shared by the typed-parity and
    blindness-linkage tests so neither burns a redundant OpenROAD run."""
    require_strict_live_gate()
    if openroad_bin() is None:
        pytest.skip("openroad binary not found")
    return route_report(
        GOLDEN_CHIPLET, PACKAGE_ROOT / "build" / "parity" / "golden_r2"
    )


# --- strict-live: any skip is a failure at a checkpoint (empty allowlist) ---
STRICT_SKIP_ALLOWLIST: tuple = ()
_strict_allowlisted_skips = 0


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not report.skipped or not strict_live():
        return
    if hasattr(report, "wasxfail"):
        return
    if isinstance(report.longrepr, tuple):
        reason = str(report.longrepr[2])
    else:
        reason = str(report.longrepr)
    for reason_part, nodeid_part in STRICT_SKIP_ALLOWLIST:
        if reason_part in reason and nodeid_part in item.nodeid:
            global _strict_allowlisted_skips
            _strict_allowlisted_skips += 1
            return
    report.outcome = "failed"
    report.longrepr = (
        f"HCDP_STRICT_LIVE=1: {item.nodeid} SKIPPED ({reason}) but strict live "
        "mode requires every live/answer-key test to actually run at a checkpoint"
    )


def pytest_terminal_summary(terminalreporter):
    if os.environ.get("HCDP_STRICT_LIVE") == "1":
        terminalreporter.write_line(
            f"HCDP_STRICT_LIVE allowlisted skips: {_strict_allowlisted_skips}"
        )
