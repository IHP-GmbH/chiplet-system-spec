# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Typed non-redundancy: the blindness is *definitional*
and proved as document equality. Both a v1.0 and a v2.0 system, and both an
envelope-free and an envelope-75 system, flatten to projections identical to each
other and to the golden. Therefore the flattened form of the *mismatched* intent
is indistinguishable from the golden, and no .chiplet consumer can refuse what it
cannot see; only the eDSL, at connect()/build(), can. No wrong_*.chiplet fixture
is needed: the wrong system's honest flattening IS the golden."""
from __future__ import annotations

import pytest

from chiplet_system_description import (
    CUPILLAR_OPT1,
    INTM4TM2,
    SG13G2,
    Die,
    Dimensions,
    InterfaceDefinition,
    InterfaceVersionMismatch,
    Interposer,
    InterposerIoPad,
    PitchClassIncompatible,
    Port,
    Position,
    Role,
    SystemBuilder,
    elaborate,
    lanes_from_pins,
    load_pins_json,
)
from tests.conftest import GOLDEN_CHIPLET, PINS_JSON
from chiplet_system_description._cfio import cfio

# Reuse the golden projection verbatim (do not reimplement).
from tests.test_elaborate_golden import _floats_close, _project

PINS = load_pins_json(PINS_JSON)


def _system(*, version="1.0", max_pitch_um=None, add_method=False,
            manager_version=None, subordinate_version=None):
    """The canonical two-die system, parameterised for the blindness argument.

    ``manager_version``/``subordinate_version`` override per die to author the
    mismatched intent; otherwise both ends share ``version``.
    """
    mv = manager_version or version
    sv = subordinate_version or version
    d_m = InterfaceDefinition("generic-parallel", 6, version=mv, max_pitch_um=max_pitch_um)
    d_s = InterfaceDefinition("generic-parallel", 6, version=sv, max_pitch_um=max_pitch_um)

    def die(name, top_cell, role, prefix, d):
        return Die(name=name, technology="sg13g2", top_cell=top_cell,
                   dimensions=Dimensions(1000.0, 2000.0, 750.0), pins=PINS,
                   ports=(Port("d2d", d, role, lanes_from_pins(PINS, prefix=prefix, count=6)),))

    u1 = die("u1", "DIE_U1", Role.MANAGER, "A", d_m)
    u2 = die("u2", "DIE_U2", Role.SUBORDINATE, "B", d_s)
    b = SystemBuilder("ihp_two_die_route", interposer=Interposer(
        technology="intm4tm2", top_cell="INTERPOSER",
        dimensions=Dimensions(6492.312, 5602.001, 10.8103),
        position=Position(3246.156, 2801.0), attachment_surface_z=10.8103,
        io_pads=(InterposerIoPad("J_VDD", "VDD", Position(300.0, 2801.0), 80.0, 80.0),
                 InterposerIoPad("J_GND", "GND", Position(6192.0, 2801.0), 80.0, 80.0))),
        description="Two SG13G2 dies on an IntM4TM2 interposer; cupillar_opt1; routable netlist")
    b.add_technology(INTM4TM2)
    b.add_technology(SG13G2)
    if add_method:
        b.add_method(CUPILLAR_OPT1)
    b.add_die(u1, "U1", Position(1500.0, 2801.0, 54.8103))
    b.add_die(u2, "U2", Position(5000.0, 2801.0, 54.8103))
    return b


def _golden_projection():
    return _project(cfio.load(str(GOLDEN_CHIPLET)))


# --- (a) version blindness ---
def test_version_v1_and_v2_flatten_identically_to_golden():
    b1 = _system(version="1.0")
    b1.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")
    b1.tie("U1", "VDD", "VDD", net_class="power")
    b1.tie("U2", "GND", "GND", net_class="ground")
    b2 = _system(version="2.0")
    b2.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")
    b2.tie("U1", "VDD", "VDD", net_class="power")
    b2.tie("U2", "GND", "GND", net_class="ground")

    p1 = _project(elaborate(b1.build()))
    p2 = _project(elaborate(b2.build()))
    golden = _golden_projection()
    assert _floats_close(p1, p2)   # version leaves no trace in the flat form
    assert _floats_close(p1, golden)
    assert _floats_close(p2, golden)


def test_mismatched_version_intent_refused_only_by_the_edsl():
    """The mismatched intent (v1 on U1, v2 on U2) would flatten to the golden;
    it is caught only at connect() by InterfaceVersionMismatch."""
    b = _system(manager_version="1.0", subordinate_version="2.0")
    with pytest.raises(InterfaceVersionMismatch):
        b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")


# --- (b) pitch-class blindness ---
def test_envelope_free_and_envelope_75_flatten_identically_to_golden(built_system):
    # built_system is the untyped, envelope-free system.
    b75 = _system(max_pitch_um=75.0, add_method=True)
    b75.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")
    b75.tie("U1", "VDD", "VDD", net_class="power")
    b75.tie("U2", "GND", "GND", net_class="ground")

    p_free = _project(elaborate(built_system))
    p_75 = _project(elaborate(b75.build()))
    golden = _golden_projection()
    assert _floats_close(p_free, p_75)   # the envelope leaves no trace either
    assert _floats_close(p_75, golden)


def test_fine_pitch_intent_refused_only_by_the_edsl():
    """A 55 um-class ballout on a cupillar_opt1 (75) die would flatten to the
    golden; it is caught only at build() by PitchClassIncompatible."""
    b = _system(max_pitch_um=55.0, add_method=True)
    b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")
    with pytest.raises(PitchClassIncompatible):
        b.build()


# --- Tier 2 (strict-live): the live half of the blindness argument ---
@pytest.mark.live_openroad
def test_blindness_linkage_golden_run_accepts_the_flattened_intent(golden_route_report):
    """Because the mismatched intents flatten to the golden document, the golden
    parity run IS the proof the whole pipeline accepts and routes them. This ties
    the blindness proof to the golden run's verdict without burning a redundant route."""
    assert golden_route_report["fully_routed"] is True
    assert golden_route_report["nets_routed"] == golden_route_report["nets_requested"]
