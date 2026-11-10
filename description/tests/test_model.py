# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Unit tests for the frozen model dataclasses and their __post_init__ invariants."""
from __future__ import annotations

import dataclasses

import pytest

from chiplet_system_description import (
    AmbiguousLane,
    Die,
    DiePin,
    Dimensions,
    DuplicateLane,
    InterfaceDefinition,
    LaneCountMismatch,
    Port,
    Position,
    Role,
    SystemSpecError,
    UnknownPin,
    UnsupportedAnchor,
)

# A0-A2 carry a pad centre because the MissingBumpGeometry check refuses a bound
# lane with no x_um/y_um. GND stays geometry-less: it is only ever a tie/ambiguous
# pin here, never a bound lane (an unbound pin may stay geometry-less).
PINS = (
    DiePin("A0", x_um=0.0, y_um=0.0),
    DiePin("A1", x_um=0.0, y_um=1.0),
    DiePin("A2", x_um=0.0, y_um=2.0),
    DiePin("GND"), DiePin("GND"),  # a repeated name (legal as a tie pin)
)


def _die(**kw):
    base = dict(name="d", technology="sg13g2", top_cell="DIE",
               dimensions=Dimensions(1.0, 1.0), pins=PINS)
    base.update(kw)
    return Die(**base)


def test_value_primitives_are_frozen():
    p = Position(1.0, 2.0, 3.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.x = 9.0  # type: ignore[misc]
    assert (p.x, p.y, p.z) == (1.0, 2.0, 3.0)
    assert Dimensions(1.0, 2.0).thickness == 0.0


def test_interface_definition_rejects_nonpositive_lane_count():
    with pytest.raises(SystemSpecError):
        InterfaceDefinition("x", 0)


def test_port_lane_count_must_match_definition():
    d = InterfaceDefinition("generic", 3)
    Port("p", d, Role.MANAGER, ("A0", "A1", "A2"))  # ok
    with pytest.raises(LaneCountMismatch):
        Port("p", d, Role.MANAGER, ("A0", "A1"))


def test_die_unknown_pin_lane():
    d = InterfaceDefinition("generic", 1)
    with pytest.raises(UnknownPin):
        _die(ports=(Port("p", d, Role.MANAGER, ("A6",)),))


def test_die_ambiguous_lane_on_repeated_pin():
    d = InterfaceDefinition("generic", 1)
    with pytest.raises(AmbiguousLane):
        _die(ports=(Port("p", d, Role.MANAGER, ("GND",)),))


def test_port_refuses_duplicate_lane_names():
    """A repeated lane within one port elaborates to a short (two nets on
    one pad, one pin unconnected); refuse it at Port construction."""
    d = InterfaceDefinition("generic", 6)
    with pytest.raises(DuplicateLane):
        Port("p", d, Role.MANAGER, ("A0", "A1", "A2", "A3", "A4", "A4"))


def test_port_accepts_distinct_lanes():
    """Prove-it-can-fail guard: the duplicate-lane check must not fire on a port
    whose lanes are all distinct."""
    d = InterfaceDefinition("generic", 6)
    Port("p", d, Role.MANAGER, ("A0", "A1", "A2", "A3", "A4", "A5"))  # no raise


def test_die_accepts_bbox_center_anchor():
    # This model writes the die geometric centre into position, so bbox_center is
    # the anchor whose reference point IS that centre (the default and the only
    # one it authors).
    d = _die(anchor="bbox_center")
    assert d.anchor == "bbox_center"
    assert _die().anchor == "bbox_center"  # the default


def test_die_refuses_gds_origin_anchor():
    with pytest.raises(UnsupportedAnchor):
        _die(anchor="gds_origin")


def test_die_refuses_noncanonical_orientation():
    with pytest.raises(SystemSpecError):
        _die(orientation="face_down")


def test_die_accepts_clean_ports_and_repeated_nonlane_pins():
    d = InterfaceDefinition("generic", 3)
    die = _die(ports=(Port("p", d, Role.MANAGER, ("A0", "A1", "A2")),))
    assert die.has_pin("GND")  # repeated pin still addressable by ties
    assert die.port("p").role is Role.MANAGER


# --- prove-it-can-fail: the AmbiguousLane oracle must not fire on a unique lane ---
def test_seeded_mutant_unique_lane_does_not_raise_ambiguous():
    """Guards against an AmbiguousLane check that always fires: a unique pin name
    used as a lane must be accepted."""
    d = InterfaceDefinition("generic", 1)
    _die(ports=(Port("p", d, Role.MANAGER, ("A0",)),))  # unique -> must pass
