# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""A source ``.chiplet`` from a System a Python spec built.

The System says what is wired; the die folders say which port each pad serves.
:func:`source_from_system` lifts one onto the other: links whose two ends share a
port name and one net affix become buses, the rest links; ties named
``<instance>_<pad>`` become ``alone`` (or an external edge entry when a carrier
pad takes the net), listed by die and then by name, so indexed runs collapse;
a one-pad tie with a rail class and every supply become
rails; ports nothing wires become ``open``; carrier pads become external edges,
each on the carrier edge it sits nearest, in the System's order.

What a source has no way to say is refused with the record that says it: a tie
named after anything but its pad, a supply that takes some of a port's pads and
not the rest, a rotation off the four. The lift keeps the System's order where
elaboration has one (a bus runs before any link, so links lift to buses only
until the first that cannot), and :func:`same_design` is the proof: it
elaborates the written file and compares the design bytes with the System's.
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ..errors import SystemSpecError
from ..model import Link, PortRef, System
from .document import Carrier, DieItem, EdgeItem, External
from .inventory import DieFolder, read_die_folder
from .plan import natural, nearest_edge
from .writer import _bus_affixes, dumps_source

class ConversionError(SystemSpecError):
    """A System record the source format cannot hold."""


def _pads(folder: DieFolder) -> Dict[str, str]:
    return {pad: port.name for port in folder.ports for pad in port.pads}


def _lift_links(links: Sequence[Link]):
    """Buses for the leading run of links a bus can name, then links for the rest."""
    buses: List[Tuple[Tuple[str, str], List[str], str, str]] = []
    rest: List[Tuple[PortRef, PortRef, Optional[str]]] = []
    run: List[Link] = []

    def close():
        if run:
            ports = [l.a.port for l in run]
            prefix, suffix = _bus_affixes(ports, [l.net_prefix for l in run])
            buses.append(((run[0].a.instance, run[0].b.instance), ports, prefix, suffix))
            run.clear()

    for link in links:
        if not rest and link.a.port == link.b.port:
            same_ends = run and ({link.a.instance, link.b.instance}
                                 == {run[0].a.instance, run[0].b.instance})
            if same_ends and _bus_affixes([l.a.port for l in run] + [link.a.port],
                                          [l.net_prefix for l in run] + [link.net_prefix]):
                run.append(link)
                continue
            if _bus_affixes([link.a.port], [link.net_prefix]):
                close()
                run.append(link)
                continue
        close()
        rest.append((link.a, link.b, link.net_prefix))
    close()
    return buses, rest


def source_from_system(system: System, folders: Mapping[str, Union[str, Path]], *,
                       base: Union[str, Path], profile: Optional[str] = None,
                       registry: Optional[str] = None,
                       interposer_adapter: Optional[str] = None,
                       fill_keepout: Union[Mapping[str, float],
                                           Sequence[Tuple[str, float]], None] = None,
                       flow_text: Optional[str] = None,
                       comment: Optional[str] = None) -> str:
    """The source text for ``system``.

    ``folders`` gives each instance id its die folder; ``base`` is the folder the
    source will live in, and each die path is written relative to it. ``profile``
    is the carrier pad profile, required when the carrier has pads.
    ``fill_keepout`` is the halo width per metal, um, as a mapping or as
    (metal, width) pairs, written in the order given.
    """
    base = Path(base)
    read: Dict[str, DieFolder] = {}
    rel: Dict[str, str] = {}
    for inst in system.instances:
        if inst.id not in folders:
            raise ConversionError(f"instance {inst.id}: no die folder given")
        folder = Path(folders[inst.id])
        absolute = folder if folder.is_absolute() else base / folder
        loaded = read_die_folder(absolute)
        read[inst.id] = loaded
        rel[inst.id] = Path(os.path.relpath(absolute, base)).as_posix()
    port_of = {i: _pads(f) for i, f in read.items()}

    def port(instance: str, pad: str, what: str) -> str:
        try:
            return port_of[instance][pad]
        except KeyError:
            raise ConversionError(f"{what}: {instance} pad {pad} serves no port in "
                                  f"{read[instance].inventory}") from None

    carrier = system.interposer
    dies = []
    for inst in system.instances:
        turned = inst.rotation_z % 360.0
        if turned not in (0.0, 90.0, 180.0, 270.0):
            raise ConversionError(f"instance {inst.id}: rotation {inst.rotation_z:g} is not "
                                  "0, 90, 180 or 270")
        # 0.0 is the model's "not given": the line is left out, and elaboration
        # gives 0.0 back with its note, rather than the file stating a 0 um die.
        thickness = inst.die.dimensions.thickness or None
        dies.append(DieItem(inst.id, rel[inst.id], (inst.position.x, inst.position.y),
                            inst.die.connection, int(turned), thickness, 0))

    accounted: Dict[str, set] = {i: set() for i in read}
    for link in system.links:
        for ref in (link.a, link.b):
            accounted[ref.instance].add(ref.port)
    buses, links = _lift_links(system.links)

    # ties: by the port their pad serves
    pad_nets = {pad.net for pad in carrier.io_pads}
    alone: List[PortRef] = []
    escaping: Dict[str, PortRef] = {}
    tie_rails: List[Tuple[str, str, List[PortRef]]] = []
    tied: Dict[Tuple[str, str], List[str]] = {}
    for tie in system.ties:
        name = port(tie.instance, tie.pin, f"tie {tie.net}")
        ref = PortRef(tie.instance, name)
        pads = read[tie.instance].port(name).pads
        if tie.net_class != "signal":
            if len(pads) != 1:
                raise ConversionError(f"tie {tie.net}: a {tie.net_class} tie on one of "
                                      f"{len(pads)} pads of {tie.instance}.{name}; a rail "
                                      "takes every pad of a port")
            tie_rails.append((tie.net, tie.net_class, [ref]))
        else:
            if tie.net != f"{tie.instance}_{tie.pin}":
                raise ConversionError(f"tie {tie.net}: {tie.instance} pad {tie.pin} is tied to "
                                      f"a net not named {tie.instance}_{tie.pin}; a source "
                                      "names a lone port's nets after its pads")
            if tie.net in pad_nets:
                escaping[tie.net] = ref
            elif (tie.instance, name) not in tied:
                alone.append(ref)
            tied.setdefault((tie.instance, name), []).append(tie.pin)
        accounted[tie.instance].add(name)
    for (instance, name), pins in tied.items():
        if sorted(pins) != sorted(read[instance].port(name).pads):
            raise ConversionError(f"port {instance}.{name}: ties take pads {sorted(pins)} of "
                                  f"{list(read[instance].port(name).pads)}; a lone port ties "
                                  "every pad")

    rails: List[Tuple[str, str, List[PortRef]]] = []
    for supply in system.supplies:
        refs: List[PortRef] = []
        taken: Dict[PortRef, List[str]] = {}
        for end in supply.endpoints:
            ref = PortRef(end.instance, port(end.instance, end.pin, f"supply {supply.net}"))
            if ref not in taken:
                refs.append(ref)
            taken.setdefault(ref, []).append(end.pin)
            accounted[end.instance].add(ref.port)
        for ref, pins in taken.items():
            if sorted(pins) != sorted(read[ref.instance].port(ref.port).pads):
                raise ConversionError(f"supply {supply.net}: takes pads {sorted(pins)} of "
                                      f"{ref.instance}.{ref.port}, which has "
                                      f"{list(read[ref.instance].port(ref.port).pads)}")
        rails.append((supply.net, supply.net_class, refs))
    rails += tie_rails
    rail_names = {name for name, _, _ in rails}

    external = None
    if carrier.io_pads:
        if not profile:
            raise ConversionError("the carrier has pads, and no profile names how they are laid")
        edges: Dict[str, List] = {}
        last = None
        for pad in carrier.io_pads:
            edge = nearest_edge((pad.position.x, pad.position.y),
                                (carrier.dimensions.width, carrier.dimensions.height))
            if edge in edges and edge != last:
                raise ConversionError(f"carrier pad {pad.id}: edge {edge} comes back after "
                                      f"{last}; a source writes each edge once")
            last = edge
            if pad.net in rail_names:
                entry = pad.net
            elif pad.net in escaping:
                entry = escaping[pad.net]
            else:
                raise ConversionError(f"carrier pad {pad.id}: net {pad.net} is neither a rail "
                                      "nor a lone port's net")
            entries = edges.setdefault(edge, [])
            if entry not in entries:
                entries.append(entry)
        external = External(profile, tuple(EdgeItem(e, tuple(v), 0) for e, v in edges.items()), 0)

    # Elaboration ties alone ports in inventory order whatever order the block
    # lists them in, so the block is free to read well: by die, then by name.
    order = {inst.id: n for n, inst in enumerate(system.instances)}
    alone.sort(key=lambda r: (order[r.instance], natural(r.port)))
    open_refs = [PortRef(i, p.name) for i, f in read.items() for p in f.ports
                 if p.name not in accounted[i]]
    return dumps_source(
        name=system.name, description=system.description,
        carrier=Carrier(carrier.technology, carrier.top_cell,
                        (carrier.dimensions.width, carrier.dimensions.height), 0),
        dies=dies, registry=registry, buses=buses, links=links, rails=rails, alone=alone,
        open=open_refs, external=external, interposer_adapter=interposer_adapter,
        fill_keepout=(list(fill_keepout.items()) if isinstance(fill_keepout, Mapping)
                      else list(fill_keepout) if fill_keepout is not None else None),
        flow_text=flow_text, comment=comment)


def same_design(system: System, path: Union[str, Path], resolver) -> Optional[str]:
    """None when the source at ``path`` elaborates to ``system``'s design bytes,
    else the unified diff (System first)."""
    from .._cfio import cfio
    from ..elaborate import elaborate
    from .elaborate import elaborate_source

    written, _ = elaborate_source(path, resolver)
    want, got = cfio.dumps(elaborate(system)), cfio.dumps(elaborate(written))
    if want == got:
        return None
    return "".join(difflib.unified_diff(want.splitlines(True), got.splitlines(True),
                                        "system", str(path)))
