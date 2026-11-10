# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Elaboration of a :class:`System` to a ``.chiplet`` document.

This module is the ONLY path that serializes a :class:`System`. :func:`emit`
goes through the vendored ``chiplet_format_io.dump`` (which validates). A
repo-local grep lint in the test suite fails on any raw YAML write outside
``_vendor/`` (the write side of the raw-load lint).

``format_version`` is never a literal here: it is read from the vendored
``cfio.SUPPORTED_FORMAT_VERSION``, so the reader owns the one constant. Key
order follows the golden fixture; equivalence to the golden is semantic, not byte-exact
(the shared loader's own doctrine).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ._cfio import cfio
from .model import Interposer, System, link_net_name, resolve_port


def _interposer_component(interp: Interposer) -> Dict[str, Any]:
    comp: Dict[str, Any] = {
        "id": interp.id,
        "type": "interposer",
        "technology": interp.technology,
        "anchor": interp.anchor,
        "top_cell": interp.top_cell,
        "position": {
            "x": interp.position.x,
            "y": interp.position.y,
            "z": interp.position.z,
        },
        "dimensions": {
            "width": interp.dimensions.width,
            "height": interp.dimensions.height,
            "thickness": interp.dimensions.thickness,
        },
    }
    if interp.attachment_surface_z is not None:
        comp["attachment_surface_z"] = interp.attachment_surface_z
    if interp.io_pads:
        comp["io_pads"] = [
            {
                "id": pad.id,
                "io_class": pad.io_class,
                "net": pad.net,
                "position": {"x": pad.position.x, "y": pad.position.y},
                "size": {"x": pad.width_um, "y": pad.height_um},
                "layer": pad.layer,
            }
            for pad in interp.io_pads
        ]
    return comp


def _die_component(inst) -> Dict[str, Any]:
    die = inst.die
    return {
        "id": inst.id,
        "type": "die",
        "technology": die.technology,
        "anchor": die.anchor,
        "orientation": die.orientation,
        "connection": die.connection,
        "top_cell": die.top_cell,
        "position": {
            "x": inst.position.x,
            "y": inst.position.y,
            "z": inst.position.z,
        },
        "dimensions": {
            "width": die.dimensions.width,
            "height": die.dimensions.height,
            "thickness": die.dimensions.thickness,
        },
        "rotation": {"z": inst.rotation_z},
    }


def _nets(system: System) -> List[Dict[str, Any]]:
    by_id = system.instances_by_id()
    nets: List[Dict[str, Any]] = []
    # links first: link order, lane order within each link, manager endpoint first
    for link in system.links:
        _, a_port = resolve_port(by_id, link.a)
        _, b_port = resolve_port(by_id, link.b)
        lane_count = a_port.definition.lane_count
        for i in range(lane_count):
            nets.append(
                {
                    "name": link_net_name(link, lane_count, i),
                    "connections": [
                        {
                            "component": link.a.instance,
                            "pin": a_port.lanes[i],
                            "layer": system.routing_layer,
                        },
                        {
                            "component": link.b.instance,
                            "pin": b_port.lanes[i],
                            "layer": system.routing_layer,
                        },
                    ],
                }
            )
    # ties, in declaration order
    for tie in system.ties:
        net: Dict[str, Any] = {"name": tie.net}
        if tie.net_class != "signal":
            net["class"] = tie.net_class
        net["connections"] = [
            {
                "component": tie.instance,
                "pin": tie.pin,
                "layer": tie.layer or system.routing_layer,
            }
        ]
        nets.append(net)
    # supply nets, in declaration order; class always written, endpoints in order
    for supply in system.supplies:
        nets.append(
            {
                "name": supply.net,
                "class": supply.net_class,
                "connections": [
                    {
                        "component": ep.instance,
                        "pin": ep.pin,
                        "layer": supply.layer or system.routing_layer,
                    }
                    for ep in supply.endpoints
                ],
            }
        )
    return nets


def elaborate(system: System) -> Dict[str, Any]:
    """Return the ``.chiplet`` mapping for ``system``, key-ordered as the golden fixture is."""
    assembly: Dict[str, Any] = {"name": system.name}
    if system.description:
        assembly["description"] = system.description
    assembly["units"] = system.units

    technologies: Dict[str, Any] = {}
    for tech in system.technologies:
        entry: Dict[str, Any] = {}
        if tech.description:
            entry["description"] = tech.description
        if tech.layer_properties is not None:
            entry["layer_properties"] = tech.layer_properties
        if tech.stackup is not None:
            entry["stackup"] = tech.stackup
        entry["dbu"] = tech.dbu
        technologies[tech.id] = entry

    components: List[Dict[str, Any]] = [_interposer_component(system.interposer)]
    components.extend(_die_component(inst) for inst in system.instances)

    return {
        "format_version": cfio.SUPPORTED_FORMAT_VERSION,
        "assembly": assembly,
        "technologies": technologies,
        "components": components,
        "netlist": {"nets": _nets(system)},
    }


def emit(system: System, path) -> Path:
    """Write ``system`` as a ``.chiplet`` through the vendored writer; return the path."""
    path = Path(path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    cfio.dump(elaborate(system), str(path))
    return path
