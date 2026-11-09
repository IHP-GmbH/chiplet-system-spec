# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Lane-to-bump binding checks (Die ctor and build()): the
fail-fast mirrors (PinAlreadyBound, MissingBumpGeometry) and the blind-spot
method/pitch checks (UnknownMethod, PitchClassIncompatible). Positive guards
against always-fire. Tests assert the class, never the message."""
from __future__ import annotations

import pytest

from chiplet_system_description import (
    CUPILLAR_OPT1,
    CUPILLAR_OPT2,
    CUPILLAR_OPT3,
    INTM4TM2,
    SBUMP_SAC305,
    SG13G2,
    Die,
    DieProcessIncompatible,
    DiePin,
    Dimensions,
    InterconnectMethod,
    InterfaceDefinition,
    Interposer,
    MissingBumpGeometry,
    PinAlreadyBound,
    PitchClassIncompatible,
    Port,
    Position,
    Role,
    SystemBuilder,
    UnknownMethod,
)

D1 = InterfaceDefinition("gp", 1)
D6 = InterfaceDefinition("gp", 6)


def _geo6(prefix):
    return tuple(DiePin(f"{prefix}{i}", pad_index=i, x_um=float(i), y_um=0.0) for i in range(6))


def _interp():
    return Interposer(technology="intm4tm2", top_cell="I",
                      dimensions=Dimensions(10.0, 10.0), position=Position(5.0, 5.0))


def _builder():
    b = SystemBuilder("s", interposer=_interp())
    b.add_technology(INTM4TM2)
    b.add_technology(SG13G2)
    return b


# ============================ Die ctor ============================
def test_missing_bump_geometry_refused_at_die_ctor():
    pins = (DiePin("A0"),)  # no x_um/y_um
    with pytest.raises(MissingBumpGeometry):
        Die(name="d", technology="sg13g2", top_cell="DIE",
            dimensions=Dimensions(1.0, 1.0), pins=pins,
            ports=(Port("p", D1, Role.MANAGER, ("A0",)),))


def test_partial_geometry_x_only_refused_at_die_ctor():
    pins = (DiePin("A0", x_um=1.0),)  # y_um missing
    with pytest.raises(MissingBumpGeometry):
        Die(name="d", technology="sg13g2", top_cell="DIE",
            dimensions=Dimensions(1.0, 1.0), pins=pins,
            ports=(Port("p", D1, Role.MANAGER, ("A0",)),))


def test_cross_port_pin_bound_by_two_ports_refused_at_die_ctor():
    pins = (DiePin("A0", x_um=0.0, y_um=0.0), DiePin("A1", x_um=0.0, y_um=1.0))
    with pytest.raises(PinAlreadyBound):
        Die(name="d", technology="sg13g2", top_cell="DIE",
            dimensions=Dimensions(1.0, 1.0), pins=pins,
            ports=(Port("p0", D1, Role.MANAGER, ("A0",)),
                   Port("p1", D1, Role.SUBORDINATE, ("A0",))))  # same pin, two ports


def test_two_ports_binding_distinct_pins_build_cleanly():
    """Positive guard for the cross-port claim: a die whose two ports bind
    DIFFERENT pins (no pad overlap) is legal and must not fire PinAlreadyBound.
    Companion to the firing test above; guards the cross-port check against
    over-firing on a legitimate two-port die. Constructs and builds, no raise."""
    pins = (DiePin("A0", x_um=0.0, y_um=0.0), DiePin("A1", x_um=0.0, y_um=1.0))
    d = Die(name="d", technology="sg13g2", top_cell="DIE",
            dimensions=Dimensions(1.0, 1.0), pins=pins,
            ports=(Port("p0", D1, Role.MANAGER, ("A0",)),
                   Port("p1", D1, Role.SUBORDINATE, ("A1",))))  # distinct pins
    b = _builder()
    b.add_die(d, "U1", Position(1.0, 1.0))
    b.build()  # no raise


def test_unbound_pin_may_stay_geometry_less():
    """Guard: a geometry-less pin that no lane binds is legal (it is a spare pad)."""
    pins = (DiePin("A0", x_um=0.0, y_um=0.0), DiePin("SPARE"))  # SPARE unbound
    Die(name="d", technology="sg13g2", top_cell="DIE",
        dimensions=Dimensions(1.0, 1.0), pins=pins,
        ports=(Port("p", D1, Role.MANAGER, ("A0",)),))  # no raise


# ============================ build() ============================
def _die(top_cell, port, pins, connection="cupillar_opt1"):
    return Die(name=top_cell.lower(), technology="sg13g2", top_cell=top_cell,
               dimensions=Dimensions(1.0, 1.0), pins=pins, ports=(port,),
               connection=connection)


def test_two_ties_on_singleton_pin_refused_at_build():
    """Claim accounting (a): total claims on a singleton > 1. Closes the gap
    where two ties on a singleton pin built fine and only failed at bumps."""
    pins = _geo6("A") + (DiePin("VDD", pad_index=6, x_um=9.0, y_um=9.0),)
    u1 = _die("DIE_U1", Port("d2d", D6, Role.MANAGER, tuple(f"A{i}" for i in range(6))), pins)
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.tie("U1", "VDD", "n1")  # distinct net names, so no DuplicateNet
    b.tie("U1", "VDD", "n2")
    with pytest.raises(PinAlreadyBound):
        b.build()


def test_repeated_group_claimed_by_two_nets_refused_at_build():
    """Claim accounting (b): an m>1 group claimed by more than one distinct tie
    net (which pad belongs to which net is undecidable without a pad index)."""
    pins = _geo6("A") + tuple(DiePin("GND", pad_index=6 + i) for i in range(3))  # GND x3
    u1 = _die("DIE_U1", Port("d2d", D6, Role.MANAGER, tuple(f"A{i}" for i in range(6))), pins)
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.tie("U1", "GND", "g1")
    b.tie("U1", "GND", "g2")  # two distinct nets on the repeated group
    with pytest.raises(PinAlreadyBound):
        b.build()


def test_many_ground_pads_one_tie_builds():
    """Positive guard: many GND pads with a single ground tie is legal (claims
    1 <= m, one net); the accounting must not over-fire (GND-x-many as today)."""
    pins = _geo6("A") + tuple(DiePin("GND", pad_index=6 + i) for i in range(13))  # GND x13
    u1 = _die("DIE_U1", Port("d2d", D6, Role.MANAGER, tuple(f"A{i}" for i in range(6))), pins)
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.tie("U1", "GND", "GND", net_class="ground")
    b.build()  # no raise


def test_unknown_method_when_envelope_has_no_declared_method():
    """Method resolution: an interface pitch envelope with no method to check it
    against is refused, never silently skipped."""
    d_env = InterfaceDefinition("gp", 6, max_pitch_um=75.0)
    u1 = _die("DIE_U1", Port("d2d", d_env, Role.MANAGER, tuple(f"A{i}" for i in range(6))), _geo6("A"))
    b = _builder()  # no add_method
    b.add_die(u1, "U1", Position(1.0, 1.0))
    with pytest.raises(UnknownMethod):
        b.build()


def test_pitch_class_incompatible_when_method_too_coarse():
    """Blind-spot: cupillar_opt1 pitch 75 cannot reach a 55 um-class ballout."""
    d_fine = InterfaceDefinition("gp", 6, max_pitch_um=55.0)
    u1 = _die("DIE_U1", Port("d2d", d_fine, Role.MANAGER, tuple(f"A{i}" for i in range(6))),
              _geo6("A"), connection="cupillar_opt1")
    b = _builder()
    b.add_method(CUPILLAR_OPT1)  # pitch 75 > envelope 55
    b.add_die(u1, "U1", Position(1.0, 1.0))
    with pytest.raises(PitchClassIncompatible):
        b.build()


# ==================== die process (manifest contract 1.1) ====================
_D2D = Port("d2d", D6, Role.MANAGER, tuple(f"A{i}" for i in range(6)))  # no envelope


def test_die_process_incompatible_when_the_method_does_not_attach_the_technology():
    """An intm4tm2 die mounted with cupillar_opt1, which attaches sg13g2 only,
    is refused at build(). The port has no pitch envelope: the die process is a
    property of the mount, not of a port, so the check does not wait for one."""
    u1 = Die(name="die_u1", technology="intm4tm2", top_cell="DIE_U1",
             dimensions=Dimensions(1.0, 1.0), pins=_geo6("A"), ports=(_D2D,),
             connection="cupillar_opt1")
    b = _builder()
    b.add_method(CUPILLAR_OPT1)
    b.add_die(u1, "U1", Position(1.0, 1.0))
    with pytest.raises(DieProcessIncompatible):
        b.build()


def test_a_declared_method_stating_no_die_process_refuses_every_die():
    """None is not "any": a method that states nothing accepts no die."""
    u1 = _die("DIE_U1", _D2D, _geo6("A"), connection="silent")
    b = _builder()
    b.add_method(InterconnectMethod("silent", pitch_um=75.0))
    b.add_die(u1, "U1", Position(1.0, 1.0))
    with pytest.raises(DieProcessIncompatible):
        b.build()


@pytest.mark.parametrize("method", [CUPILLAR_OPT1, CUPILLAR_OPT2, CUPILLAR_OPT3, SBUMP_SAC305],
                         ids=lambda m: m.id)
def test_an_sg13g2_die_builds_on_every_shipped_method(method):
    """Positive guard: the four shipped methods attach sg13g2, the only die
    process this ecosystem builds today."""
    u1 = _die("DIE_U1", _D2D, _geo6("A"), connection=method.id)
    b = _builder()
    b.add_method(method)
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.build()  # no raise


def test_an_undeclared_method_without_an_envelope_still_builds():
    """As before the die-process check: a connection that matches no declared method resolves to
    nothing, so there is no die-process list to hold the die to; it is refused
    only through a pitch envelope (UnknownMethod), as before this check."""
    u1 = _die("DIE_U1", _D2D, _geo6("A"), connection="cupillar_opt1")
    b = _builder()  # no add_method
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.build()  # no raise
