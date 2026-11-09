# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Explicit supply nets over several pads, and single-lane link naming."""
from __future__ import annotations

import pytest

from chiplet_system_description import (
    INTM4TM2,
    SG13G2,
    Die,
    DiePin,
    Dimensions,
    DuplicateNet,
    InterfaceDefinition,
    Interposer,
    PinAlreadyBound,
    Port,
    Position,
    Role,
    SystemBuilder,
    SystemSpecError,
    elaborate,
)

PINS = (
    DiePin("S0", x_um=0.0, y_um=0.0),
    DiePin("S1", x_um=1.0, y_um=0.0),
    DiePin("VDD_A", x_um=0.0, y_um=1.0),
    DiePin("VDD_B", x_um=1.0, y_um=1.0),
    DiePin("VSS_A", x_um=0.0, y_um=2.0),
)
ONE = InterfaceDefinition("single", 1)


def _die(role):
    return Die(name="d", technology="sg13g2", top_cell="DIE",
               dimensions=Dimensions(1.0, 1.0), pins=PINS,
               ports=(Port("clk", ONE, role, ("S0",)),))


def _pair():
    b = SystemBuilder("s", interposer=Interposer(
        technology="intm4tm2", top_cell="I", dimensions=Dimensions(10.0, 10.0),
        position=Position(5.0, 5.0)))
    b.add_technology(INTM4TM2)
    b.add_technology(SG13G2)
    b.add_die(_die(Role.MANAGER), "U1", Position(1.0, 1.0))
    b.add_die(_die(Role.SUBORDINATE), "U2", Position(3.0, 3.0))
    return b


def _nets(b):
    return {n["name"]: n for n in elaborate(b.build())["netlist"]["nets"]}


def test_supply_elaborates_to_one_net_over_both_dies():
    b = _pair()
    pads = [("U1", "VDD_A"), ("U1", "VDD_B"), ("U2", "VDD_A"), ("U2", "VDD_B")]
    b.supply("VDD", pads)
    net = _nets(b)["VDD"]
    assert net["class"] == "power"
    assert [(c["component"], c["pin"]) for c in net["connections"]] == pads


def test_supply_with_one_endpoint_is_refused():
    b = _pair()
    b.supply("VDD", [("U1", "VDD_A")])
    with pytest.raises(SystemSpecError):
        b.build()


def test_supply_with_signal_class_is_refused():
    b = _pair()
    b.supply("VDD", [("U1", "VDD_A"), ("U2", "VDD_A")], net_class="signal")
    with pytest.raises(SystemSpecError):
        b.build()


def test_supply_on_a_pad_bound_by_a_port_lane_is_refused():
    b = _pair()
    b.supply("VSS", [("U1", "VSS_A"), ("U1", "S0")], net_class="ground")
    with pytest.raises(PinAlreadyBound):
        b.build()


def test_supply_name_colliding_with_a_tie_is_refused():
    b = _pair()
    b.tie("U1", "VSS_A", "VDD", net_class="power")
    b.supply("VDD", [("U1", "VDD_A"), ("U2", "VDD_A")])
    with pytest.raises(DuplicateNet):
        b.build()


def test_two_ties_on_one_net_name_are_still_refused():
    b = _pair()
    b.tie("U1", "VDD_A", "VDD", net_class="power")
    b.tie("U2", "VDD_A", "VDD", net_class="power")
    with pytest.raises(DuplicateNet):
        b.build()


def test_empty_supply_name_and_empty_single_lane_prefix_are_refused():
    b = _pair()
    b.supply("", [("U1", "VDD_A"), ("U2", "VDD_A")])
    with pytest.raises(SystemSpecError):
        b.build()
    b = _pair()
    b.connect(("U1", "clk"), ("U2", "clk"), net_prefix="")
    with pytest.raises(SystemSpecError):
        b.build()


def test_malformed_supply_endpoint_is_refused():
    with pytest.raises(SystemSpecError):
        _pair().supply("VDD", [("U1", "VDD_A", "x"), ("U2", "VDD_A")])


def test_single_lane_link_net_is_named_by_its_prefix():
    b = _pair()
    b.connect(("U1", "clk"), ("U2", "clk"), net_prefix="d2d_fclk")
    net = _nets(b)["d2d_fclk"]
    assert [(c["component"], c["pin"]) for c in net["connections"]] == [
        ("U1", "S0"), ("U2", "S0")]
