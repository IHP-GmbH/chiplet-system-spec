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
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, Union

from ..builder import SystemBuilder
from ..errors import SystemSpecError
from ..model import (
    Die,
    Dimensions,
    InterconnectMethod,
    InterfaceDefinition,
    Interposer,
    InterposerIoPad,
    Port,
    Position,
    Role,
    System,
    Technology,
)
from .document import Finding, Source, SourceError, read_source
from .plan import Plan, plan


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
    """
    source = read_source(path)
    topology = plan(source)
    refusals = topology.sheet_base_refusals if sheet_base else topology.refusals
    if refusals:
        raise SourceError(source.path, refusals)
    try:
        system = build(source, topology, resolver)
    except SourceError:
        raise
    except SystemSpecError as exc:
        # The model's own refusal, after every file rule passed: name the file,
        # since the model knows no line.
        raise SourceError(source.path, [Finding(0, f"{type(exc).__name__}: {exc}", tier=1)]
                          ) from exc
    data = source.data
    flow = data.get("flow")
    extras = Extras(
        interposer_adapter=(data.get("interposer") or {}).get("adapter"),
        io_profile=source.external.profile if source.external else None,
        dies=tuple((d.id, source.folder / d.die) for d in source.dies),
        flow=flow.get("packaging_flow") if isinstance(flow, dict) else None,
        fill_keepout=dict(source.fill_keepout.halo_um) if source.fill_keepout else None,
        notes=tuple(f.render(source.path) for f in (*source.notes, *topology.notes)),
    )
    return system, extras
