# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Non-redundancy: the eDSL refuses the wrong system at
connect(), while the same wrong netlist, hand-flattened as a .chiplet, sails
through every existing stage. Neither half alone makes the case."""
from __future__ import annotations

import json

import pytest

from chiplet_system_description import (
    Die,
    DiePin,
    Dimensions,
    InterfaceDefinition,
    Interposer,
    LaneCountMismatch,
    Port,
    Position,
    Role,
    RoleConflict,
    SystemBuilder,
    UnknownPin,
    INTM4TM2,
    SG13G2,
    load_pins_json,
)
from tests import conftest
from tests.conftest import (
    PINS_JSON,
    WRONG_6V4,
    ROUTING_TECH,
    openroad_bin,
    require_strict_live_gate,
    run_ipnr,
    route_report,
)

PINS = load_pins_json(PINS_JSON)


def _interposer():
    return Interposer(technology="intm4tm2", top_cell="INTERPOSER",
                      dimensions=Dimensions(6492.312, 5602.001, 13.83),
                      position=Position(3246.156, 2801.0))


def _builder():
    b = SystemBuilder("ihp_two_die_route", interposer=_interposer())
    b.add_technology(INTM4TM2)
    b.add_technology(SG13G2)
    return b


def _die(top_cell, port):
    return Die(name=top_cell.lower(), technology="sg13g2", top_cell=top_cell,
               dimensions=Dimensions(1000.0, 2000.0, 750.0), pins=PINS, ports=(port,))


# --- positive controls: the typed refusals (hermetic) ---
def test_six_vs_four_refused_at_connect():
    d6 = InterfaceDefinition("generic-parallel", 6)
    d4 = InterfaceDefinition("generic-parallel", 4)  # same name, fewer lanes
    u1 = _die("DIE_U1", Port("d2d", d6, Role.MANAGER, tuple(f"A{i}" for i in range(6))))
    u2 = _die("DIE_U2", Port("d2d", d4, Role.SUBORDINATE, tuple(f"B{i}" for i in range(4))))
    b = _builder()
    b.add_die(u1, "U1", Position(1500.0, 2801.0, 57.83))
    b.add_die(u2, "U2", Position(5000.0, 2801.0, 57.83))
    with pytest.raises(LaneCountMismatch):
        b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")


def test_six_vs_four_refused_when_one_definition_reused():
    """The other route: reusing one definition object makes the mismatch fire at
    Port construction (still LaneCountMismatch)."""
    d6 = InterfaceDefinition("generic-parallel", 6)
    with pytest.raises(LaneCountMismatch):
        Port("d2d", d6, Role.SUBORDINATE, tuple(f"B{i}" for i in range(4)))


def test_direction_conflict_refused_at_connect():
    d6 = InterfaceDefinition("generic-parallel", 6)
    u1 = _die("DIE_U1", Port("d2d", d6, Role.MANAGER, tuple(f"A{i}" for i in range(6))))
    u2 = _die("DIE_U2", Port("d2d", d6, Role.MANAGER, tuple(f"B{i}" for i in range(6))))
    b = _builder()
    b.add_die(u1, "U1", Position(1500.0, 2801.0, 57.83))
    b.add_die(u2, "U2", Position(5000.0, 2801.0, 57.83))
    with pytest.raises(RoleConflict):
        b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")


def test_pin_existence_refused_at_die_construction():
    d6 = InterfaceDefinition("generic-parallel", 6)
    with pytest.raises(UnknownPin):
        _die("DIE_U1", Port("d2d", d6, Role.MANAGER,
                            ("A0", "A1", "A2", "A3", "A4", "A6")))  # A6 absent


# --- prove-it-can-fail: the correct 6-lane connect must NOT raise ---
def test_correct_six_lane_connect_does_not_raise():
    """Guards against a check that always fires: the well-formed system builds."""
    d6 = InterfaceDefinition("generic-parallel", 6)
    u1 = _die("DIE_U1", Port("d2d", d6, Role.MANAGER, tuple(f"A{i}" for i in range(6))))
    u2 = _die("DIE_U2", Port("d2d", d6, Role.SUBORDINATE, tuple(f"B{i}" for i in range(6))))
    b = _builder()
    b.add_die(u1, "U1", Position(1500.0, 2801.0, 57.83))
    b.add_die(u2, "U2", Position(5000.0, 2801.0, 57.83))
    b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")
    b.build()  # no raise


# --- the other half (strict-live): the wrong netlist is invisible downstream ---
@pytest.mark.live_openroad
def test_wrong_6v4_passes_every_existing_stage():
    """place-check is clean and route reports fully_routed on the hand-flattened
    wrong netlist: no flat-netlist consumer can tell six lanes were meant."""
    require_strict_live_gate()
    if openroad_bin() is None:
        pytest.skip("openroad binary not found")

    work = conftest.PACKAGE_ROOT / "build" / "wrong"
    work.mkdir(parents=True, exist_ok=True)
    placement = work / "placement.json"
    r = run_ipnr("place-check", "--chiplet", str(WRONG_6V4), "--out", str(placement))
    assert r.returncode == 0, f"place-check nonzero:\n{r.stdout}\n{r.stderr}"
    assert json.loads(placement.read_text())["clean"] is True

    report = route_report(WRONG_6V4, work)
    assert report["fully_routed"] is True, report
