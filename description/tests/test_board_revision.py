# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
from copy import deepcopy
from dataclasses import replace

import pytest

from chiplet_system_description import (
    INTM4TM2, SG13G2, Die, DieInstance, DiePin, Dimensions, InterfaceDefinition,
    Interposer, InterposerIoPad, Link, PinRef, Port, PortRef, Position, Role,
    Supply, System, SystemSpecError, Tie, elaborate,
)
from chiplet_system_description.revision import BoardRevisionError, revise_system


def system_with_links(width=2):
    interface = InterfaceDefinition("bus", width)
    lanes = tuple(f"S{i}" for i in range(width))
    pins = tuple(DiePin(pin, x_um=i, y_um=0) for i, pin in enumerate(lanes))
    instances = []
    for name, role in (("A", Role.MANAGER), ("B", Role.SUBORDINATE),
                       ("C", Role.MANAGER), ("D", Role.SUBORDINATE)):
        die = Die(name=name, technology="sg13g2", top_cell=name,
                  dimensions=Dimensions(10, 10),
                  pins=pins + (DiePin("VDD"), DiePin("PROBE")),
                  ports=(Port("bus", interface, role, lanes),))
        instances.append(DieInstance(name, die, Position(20 * len(instances), 20, 57)))
    return System(name="revision", technologies=(INTM4TM2, SG13G2),
                  interposer=Interposer("intm4tm2", "INTERPOSER", Dimensions(100, 100), Position(50, 50)),
                  instances=tuple(instances),
                  links=(Link(PortRef("A", "bus"), PortRef("B", "bus"), "ab"),
                         Link(PortRef("C", "bus"), PortRef("D", "bus"), "cd")),
                  ties=(Tie("probe", "A", "PROBE"),),
                  supplies=(Supply("power", (PinRef("A", "VDD"), PinRef("B", "VDD"))),))


def inputs(system):
    return dict(placements=[dict(component=i.id, position_um=[i.position.x, i.position.y],
                                rotation_deg=i.rotation_z, anchor=i.die.anchor,
                                orientation=i.die.orientation) for i in system.instances],
                nets=elaborate(system)["netlist"]["nets"])


def test_unchanged_preserves_intent_and_inputs():
    system = system_with_links()
    args = inputs(system)
    original = deepcopy(args)
    result = revise_system(system, **args)
    assert result == system
    assert args == original
    assert result.instances[0].die is system.instances[0].die


def test_canonical_real_example_round_trip(built_system):
    assert revise_system(built_system, **inputs(built_system)) == built_system


def test_placement_preserves_z_and_all_other_contracts():
    system = system_with_links()
    args = inputs(system)
    args["placements"][0].update(position_um=[31, 42], rotation_deg=90)
    result = revise_system(system, **args)
    assert result.instances[0] == replace(system.instances[0], position=Position(31, 42, 57), rotation_z=90)
    assert result.instances[1:] == system.instances[1:]
    assert result.interposer is system.interposer
    assert result.links == system.links


@pytest.mark.parametrize("width", [1, 2, 12])
def test_rewire_complete_compatible_ports(width):
    system = system_with_links(width)
    args = inputs(system)
    for net in args["nets"][:2 * width]:
        for conn in net["connections"]:
            if conn["component"] in ("B", "D"):
                conn["component"] = {"B": "D", "D": "B"}[conn["component"]]
    result = revise_system(system, **args)
    assert result.links[0].b.instance == "D"
    assert result.links[1].b.instance == "B"


def test_reversed_link_declaration_preserved():
    system = system_with_links()
    system = replace(system, links=tuple(Link(l.b, l.a, l.net_prefix) for l in system.links))
    assert revise_system(system, **inputs(system)) == system


def test_explicit_net_renames_preserve_class():
    system = system_with_links()
    args = inputs(system)
    for net in args["nets"]:
        net["name"] = "new_" + net["name"]
    result = revise_system(system, **args)
    assert result.supplies[0].net == "new_power"
    assert result.supplies[0].net_class == "power"
    assert result.ties[0].net == "new_probe"
    assert result.links[0].net_prefix == "new_ab"


@pytest.mark.parametrize("mutation,match", [
    (lambda a: a["placements"].pop(), "missing placements"),
    (lambda a: a["placements"].append(a["placements"][0]), "repeated placed"),
    (lambda a: a["placements"][0].update(component="alien"), "unknown"),
    (lambda a: a["placements"][0].update(rotation_deg=float("nan")), "finite"),
    (lambda a: a["placements"][0].update(position_um=[True, 1]), "finite"),
    (lambda a: a["placements"][0].update(position_um=[10**10000, 1]), "finite"),
    (lambda a: a["placements"][0].update(orientation="face_up"), "orientation"),
    (lambda a: a["nets"].pop(), "endpoint set"),
    (lambda a: a["nets"][0].update(name=""), "net name"),
    (lambda a: a["nets"][0].update(external=True), "external"),
    (lambda a: a["nets"][0].update(name="arbitrary"), "lane-indexed"),
    (lambda a: a["nets"][0].update(**{"class": "power"}), "layer/class"),
    (lambda a: a["nets"][0]["connections"][0].update(layer="Metal4"), "layer/class"),
    (lambda a: a["nets"][-1].update(**{"class": "ground"}), "tie/supply"),
])
def test_rejects_unsupported_edits(mutation, match):
    args = inputs(system_with_links())
    mutation(args)
    with pytest.raises(BoardRevisionError, match=match):
        revise_system(system_with_links(), **args)


def test_lane_swap_refuses_instead_of_flattening_interface():
    system = system_with_links()
    args = inputs(system)
    args["nets"][0]["connections"][1]["pin"] = "S1"
    args["nets"][1]["connections"][1]["pin"] = "S0"
    with pytest.raises(BoardRevisionError, match="lane order"):
        revise_system(system, **args)


def test_partial_port_rewire_refuses():
    system = system_with_links()
    args = inputs(system)
    args["nets"][0]["connections"][1]["component"] = "D"
    args["nets"][2]["connections"][1]["component"] = "B"
    with pytest.raises(BoardRevisionError, match="split across"):
        revise_system(system, **args)


def test_rewire_still_checks_interface_types():
    system = system_with_links(1)
    instances = list(system.instances)
    for i in (2, 3):
        die = instances[i].die
        port = replace(die.ports[0], definition=InterfaceDefinition("different", 1))
        instances[i] = replace(instances[i], die=replace(die, ports=(port,)))
    system = replace(system, instances=tuple(instances))
    args = inputs(system)
    args["nets"][0]["connections"][1]["component"] = "D"
    args["nets"][1]["connections"][1]["component"] = "B"
    with pytest.raises(SystemSpecError, match="different"):
        revise_system(system, **args)


def test_io_pad_net_cannot_be_renamed_silently():
    system = system_with_links()
    pad = InterposerIoPad("J1", "probe", Position(2, 3), 1, 1)
    system = replace(system, interposer=replace(system.interposer, io_pads=(pad,)))
    args = inputs(system)
    args["nets"][-2]["name"] = "renamed"
    with pytest.raises(BoardRevisionError, match="IO pad"):
        revise_system(system, **args)


def test_unbound_io_pad_cannot_be_bound_by_net_rename():
    system = system_with_links()
    pad = InterposerIoPad("J_FLOAT", "floating", Position(2, 3), 1, 1)
    system = replace(system, interposer=replace(system.interposer, io_pads=(pad,)))
    args = inputs(system)
    assert revise_system(system, **args) == system
    args["nets"][-2]["name"] = "floating"
    with pytest.raises(BoardRevisionError, match="IO pad"):
        revise_system(system, **args)
