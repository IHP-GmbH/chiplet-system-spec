# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""A source elaborated to a frozen :class:`~chiplet_system_description.model.System`.

Everything the file and the die folders state is derived here, through
:func:`~chiplet_system_description.source.plan.plan`: the topology and its order, each die's
top cell, box and technology, and the instance placements. What depends on the
host the run happens on comes through a :class:`Resolver` the flow implements:
the technology records (with the viewer paths of that host), each method's
record and connection stack, the attach plane, and the external pad row. A die
sits at the plane plus its method's stack, added in decimal so the sum is the
one the frame contract names; the carrier is centred, at z 0, its thickness the
plane (``design.chiplet`` has no attachment-surface field).

A refusal of this package is a :class:`SourceError` carrying the file and the
line; an error the resolver raises passes through untranslated, so the flow
keeps its own refusals. The source's notes (tier 1, never refusing) come back on
:class:`Extras` with what else the flow takes from the file and the model has
no field for.

The carrier must be IntM4TM2 (``carrier_unsupported_technology``, its message
led by ``CARRIER_UNSUPPORTED_TECHNOLOGY``, the id packaging-flow and the plugin
refuse the same carrier with). The System's routing layer is TopMetal2 and the
flow downstream reads IntM4TM2's files, so another carrier would elaborate to a
System that looks valid. The refusal is temporary: it goes when a carrier is
read from its technology package.

A source that leaves its placement open (format 1.3) never elaborates through
:func:`elaborate_source`. :func:`elaborate_provisional` elaborates it for one
reader, interposer-pnr's ``place``, which decides the dies from their sizes,
pins and links: each die with no ``at`` sits at a provisional position (in one
row, file order, rotation 0, above every die the file places, edge to edge), and
an unsized carrier is sized to the far corner of those boxes. A kept die whose
box leaves the first quadrant is refused: move it to non-negative coordinates
or size the carrier.
On a carrier whose size the file states the row stays inside it: it runs from
the origin when there is no room above, and a die that would leave past the
east edge is drawn back to it, over other dies if need be, since place seeds
every unplaced die again; a die that fits that carrier only turned sits at
rotation 90, and one that fits it neither way is refused (``carrier_too_small``). The ports in ``unplaced.pins`` are on no net (a rail
there keeps its net between the dies). External pads are written only on a
carrier whose size the file states, since a pad's position follows from that
size. The document carries the format's own intermediate marker, ``_metadata:
{finalize_required: true, finalizer: "packaging-flow place"}``: every reader
refuses it unless the caller allows intermediate files, and the finalizer names
the verb that turns it into a placed design, so nothing mistakes it for one.
:attr:`Extras.provisional` names the dies at provisional positions; the others
are where the file puts them.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, Union

from ..builder import SystemBuilder
from ..elaborate import elaborate
from ..errors import SystemSpecError
from ..model import (
    Die,
    Dimensions,
    InterconnectMethod,
    InterfaceDefinition,
    Interposer,
    InterposerIoPad,
    INTM4TM2,
    Port,
    Position,
    Role,
    System,
    Technology,
)
from .document import Finding, Source, SourceError, read_source
from .rail_requirement import RequiredRail
from .geometry import _grid_extent, die_box, turned
from .plan import NO_DIES, Plan, plan
from .scalars import number, quantized
from .unplaced import PLACE

#: The ``_metadata`` of a provisional elaboration: the format's intermediate
#: marker, naming the verb that places it.
PROVISIONAL_METADATA = {"finalize_required": True, "finalizer": PLACE}
#: The code of a carrier other than IntM4TM2 (the module docstring).
CARRIER_UNSUPPORTED = "carrier_unsupported_technology"


class Resolver(Protocol):
    """What a source needs from the host it is elaborated on."""

    def technology(self, id: str) -> Technology:
        """The record of technology ``id``."""

    def method(self, id: str, registry: Optional[Path]
               ) -> Tuple[InterconnectMethod, Decimal]:
        """Method ``id`` and its connection stack height in um, from ``registry``
        (absolute, already held to the path rule) or the host's own when None."""

    def plane(self) -> Decimal:
        """The attach surface the dies mount on, in um."""

    def io_pads(self, profile: Union[str, Path], body: Tuple[float, float],
                edges: Sequence[Tuple[str, Sequence[str]]]) -> Tuple[InterposerIoPad, ...]:
        """The external pad row: ``profile`` is a shipped name, or an absolute
        Path for a ``.json`` the file names; ``body`` is the carrier's width and
        height; ``edges`` is (north|south|west|east, the net of each pad) in
        file order."""


@dataclass(frozen=True)
class Extras:
    """What the flow takes from the source beside the System."""

    interposer_adapter: Optional[str]
    io_profile: Optional[str]
    dies: Tuple[Tuple[str, Path], ...]  # (instance, die folder), in file order
    flow: Optional[Dict[str, Any]]  # flow.packaging_flow, parsed
    fill_keepout: Optional[Dict[str, float]]  # layer -> halo um
    notes: Tuple[str, ...]  # rendered "file:line: note: ...", in line order
    #: The dies a provisional elaboration put at a provisional position, in
    #: file order; empty for every other elaboration.
    provisional: Tuple[str, ...] = ()
    #: (instance, admission file) of each die whose row names one (format
    #: 1.4), in file order; what the admission allows is the flow's to decide.
    admissions: Tuple[Tuple[str, Path], ...] = ()
    #: Each rail that states a routing requirement (format 1.5), in file order;
    #: the width a current needs is the router's to compute.
    rail_requirements: Tuple[RequiredRail, ...] = ()


def _profile(source: Source) -> Union[str, Path]:
    profile = source.external.profile
    if profile.endswith(".json"):
        return source.folder / profile
    return profile


def _num(value) -> float:
    return float(value)


def build(source: Source, topology: Plan, resolver: Resolver) -> System:
    """The System of a source whose plan has no refusal (or, for a blank sheet's
    base, none but unaccounted ports, which are then on no net)."""
    data = source.data
    assembly = data.get("assembly") or {}
    carrier = source.carrier
    if carrier.technology != INTM4TM2.id:
        raise SourceError(source.path, [Finding(
            carrier.line, f"CARRIER_UNSUPPORTED_TECHNOLOGY: carrier: technology is "
                          f"{carrier.technology!r}; IntM4TM2 ({INTM4TM2.id!r}) is the only "
                          f"carrier modelled until carriers are read from their technology "
                          f"package", tier=1, code=CARRIER_UNSUPPORTED)])
    width, height = (_num(v) for v in carrier.size)
    plane = resolver.plane()
    io_pads: Tuple[InterposerIoPad, ...] = ()
    if source.external is not None:
        io_pads = tuple(resolver.io_pads(_profile(source), (width, height), topology.edges))
    interposer = Interposer(
        technology=carrier.technology, top_cell=carrier.top_cell,
        dimensions=Dimensions(width, height, float(plane)),
        position=Position(width / 2.0, height / 2.0), io_pads=io_pads)
    builder = SystemBuilder(str(assembly.get("name", "")), interposer,
                            description=str(assembly.get("description", "") or ""))

    tech_ids: List[str] = [carrier.technology]
    for die in source.dies:
        technology = topology.folders[die.id].technology
        if technology not in tech_ids:
            tech_ids.append(technology)
    for tech_id in tech_ids:
        builder.add_technology(resolver.technology(tech_id))

    registry = source.folder / source.registry if source.registry else None
    stacks: Dict[str, Decimal] = {}
    for die in source.dies:
        if die.method not in stacks:
            method, stack = resolver.method(die.method, registry)
            builder.add_method(method)
            stacks[die.method] = Decimal(stack)

    ports: Dict[str, List[Port]] = {die.id: [] for die in source.dies}
    for link in topology.links:
        definition = InterfaceDefinition(
            f"{link.a.instance}.{link.a.port}-{link.b.instance}.{link.b.port}",
            lane_count=len(link.a_pads))
        ports[link.a.instance].append(Port(link.a.port, definition, Role.MANAGER, link.a_pads))
        ports[link.b.instance].append(Port(link.b.port, definition, Role.SUBORDINATE, link.b_pads))

    for die in source.dies:
        folder = topology.folders[die.id]
        thickness = float(die.thickness) if die.thickness is not None else 0.0
        blueprint = Die(
            name=folder.name, technology=folder.technology, top_cell=folder.top_cell,
            dimensions=Dimensions(folder.width, folder.height, thickness),
            pins=folder.pins, ports=tuple(ports[die.id]), connection=die.method,
            pins_source=str(folder.inventory))
        z = float(plane + stacks[die.method])
        builder.add_die(blueprint, die.id, Position(_num(die.at[0]), _num(die.at[1]), z),
                        rotation_z=float(die.rotation))

    for link in topology.links:
        builder.connect((link.a.instance, link.a.port), (link.b.instance, link.b.port),
                        net_prefix=link.net)
    for tie in topology.ties:
        builder.tie(tie.instance, tie.pad, tie.net, net_class=tie.net_class)
    for supply in topology.supplies:
        builder.supply(supply.net, supply.endpoints, net_class=supply.net_class)
    return builder.build()


def elaborate_source(path: Union[str, Path], resolver: Resolver, *,
                     sheet_base: bool = False) -> Tuple[System, Extras]:
    """Read, check (tiers 0 and 1) and elaborate the source at ``path``.

    ``sheet_base`` is for the base of a blank system sheet only: a file whose
    one refusal is that die ports are unaccounted elaborates, with those ports
    on no net, since the sheet drawn against it is what accounts for them. Every
    other caller leaves it off, and such a file is refused as tier 1 refuses it.
    A source that leaves its placement open (format 1.3) is refused whatever
    ``sheet_base`` says, one finding per unplaced thing, each naming the fix:
    a run never places by itself.
    """
    source = read_source(path)
    topology = plan(source)
    refusals = topology.sheet_base_refusals if sheet_base else topology.refusals
    if refusals:
        raise SourceError(source.path, refusals)
    return _built(source, topology, resolver), _extras(source, topology)


def _built(source: Source, topology: Plan, resolver: Resolver) -> System:
    try:
        return build(source, topology, resolver)
    except SourceError:
        raise
    except SystemSpecError as exc:
        # The model's own refusal, after every file rule passed: name the file,
        # since the model knows no line.
        raise SourceError(source.path, [Finding(0, f"{type(exc).__name__}: {exc}", tier=1)]
                          ) from exc


def _extras(source: Source, topology: Plan, provisional: Tuple[str, ...] = ()) -> Extras:
    data = source.data
    flow = data.get("flow")
    return Extras(
        interposer_adapter=(data.get("interposer") or {}).get("adapter"),
        io_profile=source.external.profile if source.external else None,
        dies=tuple((d.id, source.folder / d.die) for d in source.dies),
        flow=flow.get("packaging_flow") if isinstance(flow, dict) else None,
        fill_keepout=dict(source.fill_keepout.halo_um) if source.fill_keepout else None,
        notes=tuple(f.render(source.path) for f in (*source.notes, *topology.notes)),
        provisional=provisional,
        admissions=tuple((d.id, source.folder / d.admission) for d in source.dies
                         if d.admission is not None),
        rail_requirements=tuple(RequiredRail(r.name, *r.requirement, r.line)
                                for r in source.rails if r.requirement),
    )


# ----------------------------------------------------------------------
# The provisional elaboration
# ----------------------------------------------------------------------
def _turned(source: Source, sizes, stated) -> Dict[str, int]:
    """The rotation each unplaced die takes in the provisional row on a stated
    carrier: 0 when it fits as drawn, 90 when it fits only turned, since place
    turns every die anyway. A die that fits neither way is refused."""
    turns, larger = {}, []
    for die in source.dies:
        if die.id not in sizes:
            continue
        w, h = sizes[die.id]
        if w <= stated[0] and h <= stated[1]:
            turns[die.id] = 0
        elif h <= stated[0] and w <= stated[1]:
            turns[die.id] = 90
        else:
            larger.append(Finding(die.line, f"dies: {die.id} is {number(w)} x {number(h)} um, "
                                            f"larger than the {number(stated[0])} x "
                                            f"{number(stated[1])} um carrier the file states "
                                            f"either way it turns; state a larger size, or "
                                            f"drop size to let {PLACE} choose",
                                  tier=1, code="carrier_too_small"))
    if larger:
        raise SourceError(source.path, larger)
    return turns


def _kept_boxes(source: Source, topology: Plan):
    """Each kept die and its box. Width and height are on the nanometre grid
    before the box is taken, which is what the row is laid out on."""
    boxes = []
    for die in source.dies:
        if die.at is None:
            continue
        at = (quantized(die.at[0]), quantized(die.at[1]))
        boxes.append((die, die_box(_grid_extent(topology.folders[die.id]), at, die.rotation)))
    return boxes


def _lay_out(source: Source, topology: Plan):
    """The dies in one provisional row, their boxes, and which dies moved.

    Unplaced dies sit edge to edge, rotation 0 unless a stated carrier only
    holds one turned, above every die the file places. On a stated carrier the
    row runs from the origin when there is no room above, and a die that would
    leave past the east edge is drawn back to it."""
    kept = _kept_boxes(source, topology)
    boxes = [box for _, box in kept]
    sizes = {d.id: _grid_extent(topology.folders[d.id])
             for d in source.dies if d.at is None}
    x, top = Decimal(0), max((b[3] for b in boxes), default=Decimal(0))
    stated, turns = source.carrier.size, {}
    if stated is not None and sizes:
        stated = (quantized(stated[0]), quantized(stated[1]))
        turns = _turned(source, sizes, stated)
        if top + max(turned(wh, turns[i])[1] for i, wh in sizes.items()) > stated[1]:
            top = Decimal(0)    # no room above: the row runs from the origin
    dies, moved = [], []
    for die in source.dies:
        if die.at is None:
            rotation = turns.get(die.id, 0)
            w, h = turned(sizes[die.id], rotation)
            bx = x if stated is None else min(x, stated[0] - w)
            center = (bx + w / 2, top + h / 2)
            die = replace(die, at=(float(center[0]), float(center[1])), rotation=rotation)
            boxes.append(die_box(sizes[die.id], center, rotation))
            moved.append(die.id)
            x += w
        dies.append(die)
    return dies, boxes, kept, tuple(moved)


def _size_carrier(source: Source, boxes, kept):
    """The carrier and the external block after the row is laid out.

    A stated size stays. An unsized carrier takes the far corner of the boxes,
    and a kept die whose box leaves the first quadrant is refused: the size
    ``(max x, max y)`` would not cover it, and place would then legalize the
    free dies against a position the carrier does not hold."""
    if source.carrier.size is not None:
        return source.carrier, source.external
    outside = []
    for die, box in kept:
        if box[0] < 0 or box[1] < 0:
            outside.append(Finding(
                die.line,
                f"dies: {die.id} lies outside the first quadrant of an unsized carrier; "
                f"move it to non-negative coordinates or size the carrier",
                tier=1, code="kept_outside"))
    if outside:
        raise SourceError(source.path, outside)
    size = (float(max(b[2] for b in boxes)), float(max(b[3] for b in boxes)))
    return replace(source.carrier, size=size), None


def _drop_unplaced_ties(source: Source, topology: Plan, placed: Source) -> Plan:
    """``topology`` on ``placed``, with the ports in ``unplaced.pins`` on no net.
    A rail named there keeps its net between the dies."""
    held = set()
    for entry in (source.unplaced.pins if source.unplaced else ()):
        if not isinstance(entry, str):
            pads = topology.folders[entry.instance].port(entry.port).pads
            held.update((entry.instance, pad) for pad in pads)
    ties = tuple(t for t in topology.ties if (t.instance, t.pad) not in held)
    return replace(topology, source=placed, ties=ties)


def elaborate_provisional(path: Union[str, Path], resolver: Resolver
                          ) -> Tuple[Dict[str, Any], Extras]:
    """The ``.chiplet`` mapping of the source at ``path`` elaborated
    provisionally, for interposer-pnr's ``place`` (the module docstring), with
    :data:`PROVISIONAL_METADATA` as its ``_metadata``, and the Extras naming
    the dies at provisional positions.

    The source is read as tier 1 reads an unplaced one: what it leaves to
    placement is a note, and any other finding refuses, as do no dies at all.
    A placed source elaborates the same way, with no die provisional, so a
    re-placement reads it too.
    """
    source = read_source(path)
    topology = plan(source, unplaced=True)
    refusals = list(topology.refusals)
    if not source.dies:
        refusals += [replace(f, note=False) for f in topology.notes if f.code == NO_DIES]
    if refusals:
        raise SourceError(source.path, refusals)
    dies, boxes, kept, moved = _lay_out(source, topology)
    carrier, external = _size_carrier(source, boxes, kept)
    placed = replace(source, dies=tuple(dies), carrier=carrier, external=external,
                     unplaced=None)
    provisional = _drop_unplaced_ties(source, topology, placed)
    document = elaborate(_built(placed, provisional, resolver))
    document = {"format_version": document.pop("format_version"),
                "_metadata": dict(PROVISIONAL_METADATA), **document}
    return document, _extras(source, topology, moved)
