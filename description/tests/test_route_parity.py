# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Tier 2 route parity: the same real interposer-pnr pipeline on
the golden and on the emitted .chiplet must agree on the routed/failed
accounting. The golden run is the answer key; absolute counts are never pinned
in this repo. Strict-live only (needs OpenROAD)."""
from __future__ import annotations

import pytest

from chiplet_system_description import emit
from tests import conftest
from tests.conftest import (
    GOLDEN_CHIPLET,
    WRONG_6V4,
    openroad_bin,
    require_strict_live_gate,
    route_report,
)

# The four accounting fields the roadmap row pins as "identical routed/failed".
PARITY_KEYS = ("nets_requested", "nets_routed", "fully_routed")


def _parity_tuple(report: dict):
    return (
        tuple(report[k] for k in PARITY_KEYS),
        tuple(sorted(n["name"] if isinstance(n, dict) else n
                     for n in report.get("unrouted", []))),
    )


@pytest.fixture(scope="module")
def golden_report():
    require_strict_live_gate()
    if openroad_bin() is None:
        pytest.skip("openroad binary not found")
    return route_report(GOLDEN_CHIPLET, conftest.PACKAGE_ROOT / "build" / "parity" / "golden")


@pytest.mark.live_openroad
def test_emitted_matches_golden_route_accounting(golden_report, built_system):
    emitted = emit(built_system, conftest.PACKAGE_ROOT / "build" / "two_die_ihp.chiplet")
    mine = route_report(emitted, conftest.PACKAGE_ROOT / "build" / "parity" / "emitted")
    assert _parity_tuple(mine) == _parity_tuple(golden_report)


# --- prove-it-can-fail: the parity oracle must reject a genuinely different run ---
@pytest.mark.live_openroad
def test_parity_oracle_rejects_wrong_6v4(golden_report):
    """Feeding the hand-flattened 4-lane netlist against the golden report must
    differ (fewer requested nets), proving the comparison can fail."""
    wrong = route_report(WRONG_6V4, conftest.PACKAGE_ROOT / "build" / "parity" / "wrong")
    assert _parity_tuple(wrong) != _parity_tuple(golden_report)
