# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""SystemBuilder.connect() eager checks and System-level build() invariants."""
from __future__ import annotations

import pytest

from chiplet_system_description import (
    CUPILLAR_OPT1,
    INTM4TM2,
    SG13G2,
    Die,
    DiePin,
    Dimensions,
    DuplicateId,
    DuplicateNet,
    InterconnectMethod,
    InterfaceDefinition,
    InterfaceTypeMismatch,
    Interposer,
    LaneCountMismatch,
    Port,
    PortAlreadyBound,
    Position,
    Role,
    SystemBuilder,
    SystemSpecError,
    System,
    Technology,
    Tie,
    UnknownInstance,
    UnknownPort,
    UnsupportedUnits,
)

# Every pin carries a pad centre: the MissingBumpGeometry check refuses a bound
# lane with no x_um/y_um, and these pins are bound as port lanes throughout.
PINS4 = (
    tuple(DiePin(f"P{i}", x_um=float(i), y_um=0.0) for i in range(4))
    + tuple(DiePin(f"Q{i}", x_um=float(i), y_um=1.0) for i in range(4))
)
PINS6 = (
    tuple(DiePin(f"A{i}", x_um=float(i), y_um=0.0) for i in range(6))
    + tuple(DiePin(f"B{i}", x_um=float(i), y_um=1.0) for i in range(6))
    + (DiePin("VDD", x_um=0.0, y_um=2.0), DiePin("GND", x_um=1.0, y_um=2.0))
)


def _interp():
    return Interposer(technology="intm4tm2", top_cell="I",
                      dimensions=Dimensions(10.0, 10.0), position=Position(5.0, 5.0))


def _builder():
    b = SystemBuilder("s", interposer=_interp())
    b.add_technology(INTM4TM2)
    b.add_technology(SG13G2)
    return b


def _die(ports):
    return Die(name="d", technology="sg13g2", top_cell="DIE",
               dimensions=Dimensions(1.0, 1.0), pins=PINS6, ports=ports)


def test_connect_orients_a_to_manager_and_binds():
    d = InterfaceDefinition("gp", 6)
    u1 = _die((Port("d2d", d, Role.MANAGER, tuple(f"A{i}" for i in range(6))),))
    u2 = _die((Port("d2d", d, Role.SUBORDINATE, tuple(f"B{i}" for i in range(6))),))
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.add_die(u2, "U2", Position(2.0, 2.0))
    # pass SUBORDINATE first; connect() must normalize a -> MANAGER
    link = b.connect(("U2", "d2d"), ("U1", "d2d"), net_prefix="sig")
    assert link.a.instance == "U1" and link.b.instance == "U2"


def test_connect_unknown_instance_and_port():
    d = InterfaceDefinition("gp", 6)
    u1 = _die((Port("d2d", d, Role.MANAGER, tuple(f"A{i}" for i in range(6))),))
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    with pytest.raises(UnknownInstance):
        b.connect(("NOPE", "d2d"), ("U1", "d2d"), net_prefix="s")
    with pytest.raises(UnknownPort):
        b.connect(("U1", "nope"), ("U1", "d2d"), net_prefix="s")


def test_connect_interface_type_mismatch():
    ga = InterfaceDefinition("gp-a", 6)
    gb = InterfaceDefinition("gp-b", 6)
    u1 = _die((Port("d2d", ga, Role.MANAGER, tuple(f"A{i}" for i in range(6))),))
    u2 = _die((Port("d2d", gb, Role.SUBORDINATE, tuple(f"B{i}" for i in range(6))),))
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.add_die(u2, "U2", Position(2.0, 2.0))
    with pytest.raises(InterfaceTypeMismatch):
        b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="s")


def test_connect_role_conflict_two_managers():
    d = InterfaceDefinition("gp", 6)
    u1 = _die((Port("d2d", d, Role.MANAGER, tuple(f"A{i}" for i in range(6))),))
    u2 = _die((Port("d2d", d, Role.MANAGER, tuple(f"B{i}" for i in range(6))),))
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.add_die(u2, "U2", Position(2.0, 2.0))
    from chiplet_system_description import RoleConflict

    with pytest.raises(RoleConflict):
        b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="s")


def test_connect_port_already_bound():
    d = InterfaceDefinition("gp", 6)
    u1 = _die((Port("d2d", d, Role.MANAGER, tuple(f"A{i}" for i in range(6))),))
    u2 = Die(name="d", technology="sg13g2", top_cell="DIE",
             dimensions=Dimensions(1.0, 1.0), pins=PINS6,
             ports=(Port("s0", d, Role.SUBORDINATE, tuple(f"B{i}" for i in range(6))),
                    Port("s1", d, Role.SUBORDINATE, tuple(f"A{i}" for i in range(6)))))
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.add_die(u2, "U2", Position(2.0, 2.0))
    b.connect(("U1", "d2d"), ("U2", "s0"), net_prefix="x")
    with pytest.raises(PortAlreadyBound):
        b.connect(("U1", "d2d"), ("U2", "s1"), net_prefix="y")


def test_connect_lane_count_mismatch_two_definitions_same_name():
    """The 6-vs-4 route through connect(): two definitions share a name, differ in
    lane_count, so the mismatch fires at connect() (not at Port construction)."""
    d6 = InterfaceDefinition("gp", 6)
    d4 = InterfaceDefinition("gp", 4)
    u1 = _die((Port("d2d", d6, Role.MANAGER, tuple(f"A{i}" for i in range(6))),))
    u2 = Die(name="d", technology="sg13g2", top_cell="DIE",
             dimensions=Dimensions(1.0, 1.0), pins=PINS4,
             ports=(Port("d2d", d4, Role.SUBORDINATE, tuple(f"Q{i}" for i in range(4))),))
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.add_die(u2, "U2", Position(2.0, 2.0))
    with pytest.raises(LaneCountMismatch):
        b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="s")


def test_add_die_duplicate_and_interposer_collision():
    d = InterfaceDefinition("gp", 6)
    u1 = _die((Port("d2d", d, Role.MANAGER, tuple(f"A{i}" for i in range(6))),))
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    with pytest.raises(DuplicateId):
        b.add_die(u1, "U1", Position(2.0, 2.0))
    with pytest.raises(DuplicateId):
        b.add_die(u1, "interposer", Position(3.0, 3.0))


def test_build_duplicate_net_link_vs_tie():
    d = InterfaceDefinition("gp", 6)
    u1 = _die((Port("d2d", d, Role.MANAGER, tuple(f"A{i}" for i in range(6))),))
    u2 = _die((Port("d2d", d, Role.SUBORDINATE, tuple(f"B{i}" for i in range(6))),))
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.add_die(u2, "U2", Position(2.0, 2.0))
    b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")
    b.tie("U1", "VDD", "sig0")  # collides with generated link net sig0
    with pytest.raises(DuplicateNet):
        b.build()


def test_direct_system_undeclared_technology_and_units():
    d = InterfaceDefinition("gp", 6)
    u1 = _die((Port("d2d", d, Role.MANAGER, tuple(f"A{i}" for i in range(6))),))
    inst = __import__("chiplet_system_description").DieInstance(
        id="U1", die=u1, position=Position(1.0, 1.0))
    with pytest.raises(SystemSpecError):
        System(name="s", technologies=(SG13G2,), interposer=_interp(),
               instances=(inst,))  # interposer tech intm4tm2 undeclared
    with pytest.raises(UnsupportedUnits):
        System(name="s", technologies=(INTM4TM2, SG13G2), interposer=_interp(),
               instances=(inst,), units="nm")


def test_direct_system_duplicate_technology_id_refused():
    """Defense in depth: a direct System(...) that bypasses the builder must
    refuse two same-id technologies, not silently last-wins (technology_ids() is
    a set). Mirrors the builder's add_technology refusal."""
    dup = Technology("intm4tm2", "a second entry sharing the intm4tm2 id")
    with pytest.raises(DuplicateId):
        System(name="s", technologies=(INTM4TM2, dup), interposer=_interp(),
               instances=())


def test_direct_system_duplicate_method_id_refused():
    """Defense in depth: a direct System(...) with two same-id interconnect
    methods must be refused, not silently last-wins in the methods_by_id dict.
    Mirrors the builder's add_method refusal. Two distinct method objects that
    share an id (not the same object twice) prove id-based dedup rather than
    object-identity dedup, mirroring the duplicate-technology test above."""
    dup = InterconnectMethod("cupillar_opt1", pitch_um=CUPILLAR_OPT1.pitch_um + 1.0)
    with pytest.raises(DuplicateId):
        System(name="s", technologies=(INTM4TM2,), interposer=_interp(),
               instances=(), methods=(CUPILLAR_OPT1, dup))


def test_tie_unknown_instance_and_pin():
    d = InterfaceDefinition("gp", 6)
    u1 = _die((Port("d2d", d, Role.MANAGER, tuple(f"A{i}" for i in range(6))),))
    b = _builder()
    b.add_die(u1, "U1", Position(1.0, 1.0))
    b.tie("NOPE", "VDD", "n1")
    with pytest.raises(UnknownInstance):
        b.build()
    b2 = _builder()
    b2.add_die(u1, "U1", Position(1.0, 1.0))
    b2.tie("U1", "NOPIN", "n2")
    with pytest.raises(__import__("chiplet_system_description").UnknownPin):
        b2.build()
