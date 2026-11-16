# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Apply a physically reconciled board edit to existing typed system intent.

Geometry/physical identity belong to the board adapter. This module owns the
different question: can its logical connections still be expressed by the
original ports, ties and supplies? It never turns typed links into raw nets.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from .elaborate import elaborate
from .errors import SystemSpecError
from .model import Link, Role, System, resolve_port


class BoardRevisionError(SystemSpecError):
    """A board edit cannot preserve the source system's declared contracts.

    ``code`` says which kind, for a caller that words it: ``report_malformed``
    (the reconciliation is not the shape it claims), ``connection_changed``
    (which ports connect moved), ``carrier_net_changed`` (a carrier pad's net or
    an external net), ``frame_changed`` (a die's anchor or orientation).
    """

    def __init__(self, message: str, code: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class _Net:
    name: str
    net_class: str
    connections: tuple[tuple[str, str, str], ...]

    @property
    def endpoints(self):
        return {(component, pin) for component, pin, _ in self.connections}

    @property
    def signature(self):
        return self.net_class, tuple(sorted(Counter(self.connections).items()))


def _net_index(rows):
    by_name, by_endpoint = {}, {}
    if not isinstance(rows, (tuple, list)):
        raise BoardRevisionError("netlist.nets must be a list", "report_malformed")
    for row in rows:
        if not isinstance(row, Mapping):
            raise BoardRevisionError("each net must be an object", "report_malformed")
        name = row.get("name")
        if not isinstance(name, str) or not name or name in by_name:
            raise BoardRevisionError(f"empty or duplicate net name: {name!r}", "report_malformed")
        net_class = row.get("class", "signal")
        if not isinstance(net_class, str) or not net_class:
            raise BoardRevisionError(f"net {name!r}: invalid class", "report_malformed")
        if row.get("external", False) is not False:
            raise BoardRevisionError(f"net {name!r}: external nets are not representable", "carrier_net_changed")
        connections = row.get("connections")
        if not isinstance(connections, (tuple, list)) or not connections:
            raise BoardRevisionError(f"net {name!r}: connections must be nonempty", "report_malformed")
        records = []
        for conn in connections:
            if not isinstance(conn, Mapping) or any(
                not isinstance(conn.get(key), str) or not conn[key]
                for key in ("component", "pin", "layer")
            ):
                raise BoardRevisionError(f"net {name!r}: invalid connection {conn!r}", "report_malformed")
            records.append((conn["component"], conn["pin"], conn["layer"]))
        net = _Net(name, net_class, tuple(records))
        for endpoint in net.endpoints:
            if endpoint in by_endpoint:
                raise BoardRevisionError(f"endpoint {endpoint!r} belongs to multiple nets", "report_malformed")
            by_endpoint[endpoint] = net
        by_name[name] = net
    return by_name, by_endpoint


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _placements(system, rows):
    if not isinstance(rows, (tuple, list)):
        raise BoardRevisionError("placements must be a list", "report_malformed")
    updates = {}
    by_id = system.instances_by_id()
    for row in rows:
        if not isinstance(row, Mapping):
            raise BoardRevisionError("each placement must be an object", "report_malformed")
        name = row.get("component")
        if not isinstance(name, str) or name not in by_id or name in updates:
            raise BoardRevisionError(f"unknown or repeated placed die: {name!r}", "report_malformed")
        inst = by_id[name]
        xy, angle = row.get("position_um"), row.get("rotation_deg")
        if not isinstance(xy, (tuple, list)) or len(xy) != 2:
            raise BoardRevisionError(f"die {name!r}: position_um must contain x and y", "report_malformed")
        if not all(_finite_number(v) for v in (*xy, angle)):
            raise BoardRevisionError(f"die {name!r}: placement must be finite numeric values", "report_malformed")
        for key in ("anchor", "orientation"):
            if row.get(key, getattr(inst.die, key)) != getattr(inst.die, key):
                raise BoardRevisionError(f"die {name!r}: {key} cannot change", "frame_changed")
        updates[name] = replace(inst, position=replace(inst.position, x=xy[0], y=xy[1]),
                                rotation_z=angle)
    if set(updates) != set(by_id):
        raise BoardRevisionError(f"missing placements: {sorted(set(by_id) - set(updates))}", "report_malformed")
    return tuple(updates[inst.id] for inst in system.instances)


def _fixed_net(old, observed_by_endpoint):
    """A tie/supply may be renamed, never split, merged or reclassified."""
    candidates = {observed_by_endpoint[ep].name for ep in old.endpoints}
    if len(candidates) != 1:
        raise BoardRevisionError(f"tie/supply {old.name!r}: split endpoint group", "connection_changed")
    new = observed_by_endpoint[next(iter(old.endpoints))]
    if new.signature != old.signature:
        raise BoardRevisionError(f"tie/supply {old.name!r}: endpoints, class or layer changed", "connection_changed")
    return new.name


def _links(system, observed_by_endpoint):
    instances = system.instances_by_id()
    linked_lanes = {}
    for link in system.links:
        for ref in (link.a, link.b):
            _, port = resolve_port(instances, ref)
            for lane, pin in enumerate(port.lanes):
                key = (ref.instance, pin)
                if key in linked_lanes:
                    raise BoardRevisionError(f"ambiguous linked endpoint {key!r}", "connection_changed")
                linked_lanes[key] = (ref, port, lane)
    result = []
    for link in system.links:
        _, first = resolve_port(instances, link.a)
        manager = link.a if first.role is Role.MANAGER else link.b
        _, port = resolve_port(instances, manager)
        peers, names = [], []
        for lane, pin in enumerate(port.lanes):
            endpoint = (manager.instance, pin)
            net = observed_by_endpoint[endpoint]
            if len(net.connections) != 2 or len(net.endpoints) != 2:
                raise BoardRevisionError(f"link net {net.name!r}: needs exactly two endpoints", "connection_changed")
            other = next(iter(net.endpoints - {endpoint}))
            binding = linked_lanes.get(other)
            if binding is None or binding[1].role is not Role.SUBORDINATE:
                raise BoardRevisionError(f"link net {net.name!r}: no existing subordinate port", "connection_changed")
            if binding[2] != lane:
                raise BoardRevisionError(f"link net {net.name!r}: lane order changed", "connection_changed")
            peers.append(binding[0])
            names.append(net.name)
        if len(set(peers)) != 1:
            raise BoardRevisionError(f"port {manager!r}: lanes split across multiple ports", "connection_changed")
        prefix = names[0] if len(names) == 1 else names[0][:-1]
        if len(names) > 1 and names != [f"{prefix}{i}" for i in range(len(names))]:
            raise BoardRevisionError(f"port {manager!r}: net names need one lane-indexed prefix",
                                     "rename_no_field")
        a, b = (manager, peers[0]) if manager == link.a else (peers[0], manager)
        result.append(Link(a, b, prefix))
    return tuple(result)


def revise_system(system: System, *, placements: Sequence[Mapping], nets: Sequence[Mapping]) -> System:
    """Return a new System, preserving all unedited contracts and metadata.

    ``placements`` are canonical micrometre positions from physical reconciliation;
    ``nets`` use the existing .chiplet netlist shape. Physical qualification is NOT
    established here. New/missing endpoints, altered rails, lane permutations and
    connections that the typed model cannot express refuse rather than guess.
    """
    original, old_endpoints = _net_index(elaborate(system)["netlist"]["nets"])
    observed, new_endpoints = _net_index(nets)
    if set(old_endpoints) != set(new_endpoints):
        missing = sorted(set(old_endpoints) - set(new_endpoints))
        added = sorted(set(new_endpoints) - set(old_endpoints))
        raise BoardRevisionError(f"assigned endpoint set changed: missing={missing}, added={added}", "connection_changed")
    ties = tuple(replace(tie, net=_fixed_net(original[tie.net], new_endpoints))
                 for tie in system.ties)
    supplies = tuple(replace(supply, net=_fixed_net(original[supply.net], new_endpoints))
                     for supply in system.supplies)
    # Carrier pads are outside this board-edit slice: refuse a rename/rewire
    # affecting their association instead of silently moving package terminals.
    for pad in system.interposer.io_pads:
        old, new = original.get(pad.net), observed.get(pad.net)
        if ((old is None) != (new is None)
                or (old is not None and old.signature != new.signature)):
            raise BoardRevisionError(f"interposer IO pad {pad.id!r}: bound net {pad.net!r} changed", "carrier_net_changed")
    result = replace(system, instances=_placements(system, placements),
                     links=_links(system, new_endpoints), ties=ties, supplies=supplies)
    actual, _ = _net_index(elaborate(result)["netlist"]["nets"])
    if {n: v.signature for n, v in actual.items()} != {
        n: v.signature for n, v in observed.items()
    }:
        raise BoardRevisionError("edited netlist changes layer/class or cannot be represented by typed links", "connection_changed")
    return result
