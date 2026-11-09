# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Interface typing at connect() and the duplicate-id refusals. Positive
guards against always-fire. Tests assert the class, never the message."""
from __future__ import annotations

import pytest

from chiplet_system_description import (
    CUPILLAR_OPT1,
    INTM4TM2,
    SG13G2,
    Die,
    Dimensions,
    DuplicateId,
    InterconnectMethod,
    InterfaceContractMismatch,
    InterfaceDefinition,
    InterfaceVersionMismatch,
    Interposer,
    Port,
    Position,
    Role,
    SystemBuilder,
    SystemSpecError,
    Technology,
    load_pins_json,
)
from tests.conftest import PINS_JSON

PINS = load_pins_json(PINS_JSON)


def _interp():
    return Interposer(technology="intm4tm2", top_cell="I",
                      dimensions=Dimensions(6492.312, 5602.001, 13.83),
                      attachment_surface_z=10.8103,
                      position=Position(3246.156, 2801.0))


def _builder():
    b = SystemBuilder("s", interposer=_interp())
    b.add_technology(INTM4TM2)
    b.add_technology(SG13G2)
    return b


def _die(top_cell, port):
    return Die(name=top_cell.lower(), technology="sg13g2", top_cell=top_cell,
               dimensions=Dimensions(1000.0, 2000.0, 750.0), pins=PINS, ports=(port,))


def _two_dies(d_manager, d_subordinate):
    u1 = _die("DIE_U1", Port("d2d", d_manager, Role.MANAGER,
                             tuple(f"A{i}" for i in range(6))))
    u2 = _die("DIE_U2", Port("d2d", d_subordinate, Role.SUBORDINATE,
                             tuple(f"B{i}" for i in range(6))))
    b = _builder()
    b.add_die(u1, "U1", Position(1500.0, 2801.0, 54.8103))
    b.add_die(u2, "U2", Position(5000.0, 2801.0, 54.8103))
    return b


# --- check 2: version identity (exact string equality) ---
def test_version_mismatch_refused_at_connect():
    d1 = InterfaceDefinition("gp", 6, version="1.0")
    d2 = InterfaceDefinition("gp", 6, version="2.0")  # same name, different version
    b = _two_dies(d1, d2)
    with pytest.raises(InterfaceVersionMismatch):
        b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")


# --- check 4: full structural equality (only max_pitch_um can differ after 1-3) ---
def test_contract_mismatch_on_pitch_envelope_refused_at_connect():
    d1 = InterfaceDefinition("gp", 6, version="1.0", max_pitch_um=75.0)
    d2 = InterfaceDefinition("gp", 6, version="1.0", max_pitch_um=80.0)
    b = _two_dies(d1, d2)
    with pytest.raises(InterfaceContractMismatch):
        b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")


# --- positive guard: a matched v1.0-to-v1.0 connect must NOT raise ---
def test_matched_version_connect_and_build_does_not_raise():
    d = InterfaceDefinition("gp", 6, version="1.0")  # one object, both ends
    b = _two_dies(d, d)
    b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")
    b.build()  # no envelope -> no method needed -> no raise


# --- positive guard: matched pitch envelope at the boundary builds ---
def test_matched_envelope_at_boundary_builds():
    d = InterfaceDefinition("gp", 6, version="1.0", max_pitch_um=75.0)
    b = _two_dies(d, d)
    b.add_method(CUPILLAR_OPT1)  # pitch 75.0 == envelope 75.0, boundary
    b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")
    b.build()  # no raise


# --- duplicate ids and empty ids ---
def test_add_technology_refuses_duplicate_id():
    b = SystemBuilder("s", interposer=_interp())
    b.add_technology(SG13G2)
    with pytest.raises(DuplicateId):
        b.add_technology(SG13G2)


def test_add_method_refuses_duplicate_id():
    b = _builder()
    b.add_method(CUPILLAR_OPT1)
    with pytest.raises(DuplicateId):
        b.add_method(InterconnectMethod("cupillar_opt1", pitch_um=75.0))


def test_empty_technology_id_refused_at_construction():
    with pytest.raises(SystemSpecError):
        Technology("")


def test_unused_declared_method_is_legal():
    """A declared method no pitch envelope consumes is legal, as unused
    technologies are: a system with no envelope builds. The
    method is still resolved by the dies' default connection (cupillar_opt1)
    for the die-process check, which their sg13g2 technology passes."""
    d = InterfaceDefinition("gp", 6, version="1.0")  # no envelope
    b = _two_dies(d, d)
    b.add_method(CUPILLAR_OPT1)  # declared; no envelope consumes its pitch
    b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")
    b.build()  # no raise
