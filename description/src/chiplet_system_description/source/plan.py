# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Tier 1: a source read against its dies, and the topology it elaborates to.

:func:`plan` resolves every port ref against the die folders' inventories and
derives the nets in the order the format fixes (the spec, "System (format
1.1)"): buses then links, each in file order, a lane oriented OUTPUT end first;
then one tie per pad of every ``alone`` port and every port ref on an external
edge, in inventory order (dies in ``dies`` order, ports as the inventory first
names them, pads by ordinal); then the one-pad rails as ties of their class, in
``rails`` order; then every other rail as a supply, in ``rails`` order, its pads
in the order its ports are written.

The same walk is the checker and the elaborator's input, so a file that passes
tier 1 is the file that elaborates. Every defect is a :class:`Finding` on the
line that causes it; a die folder that cannot be read is one finding on its
``dies`` line, and its ports are then not checked one by one.

A port the file leaves unaccounted is a finding too, with a line ready to paste
(:class:`Suggestion`). The guess, in this order: a signal port served on another
die whose same-named port is also unaccounted, with the opposite direction and
the same pad count, goes in ``buses`` on that pair; a POWER or GROUND port goes
in ``rails``, added to the rail named after its inventory net if one of that
class exists, else as a new rail of that name; any other port goes in
``alone``. When the file has an ``external`` block, a rail no edge names is a
finding (nothing outside the carrier supplies it), so the rail guess changes:
the named rail only if an edge carries it, else the one rail of its class an
edge carries; when several are carried, one line per rail, of which the author
pastes one, since the file holds no voltage to choose by; with none, a new rail
together with the edge line that carries it, on the carrier edge nearest the die. Nothing is guessed into ``open``. Runs of indexed names collapse into
one ``[a..b]``.

An unplaced source (format 1.3: a die with no ``at``, a carrier with no
``size``, entries in ``unplaced.pins``, or no dies yet) is refused here, one
finding per unplaced thing, each naming the fix, unless the caller opts in with
``plan(source, unplaced=True)``: then each is a note, and every other rule runs
on what is placed. An ``unplaced.pins`` entry is accounted for as an edge entry
is, and a rail it carries escapes the carrier; where no edge can be chosen yet
(no carrier size, or the die is not placed) a new rail's line goes in
``unplaced.pins`` instead of on an edge.

A die's admission file (format 1.4) is read relative to the source, and one
that is absent or malformed is one finding on its ``dies`` line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Iterator, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from ..model import PortRef
from .document import (
    Finding,
    RailItem,
    Source,
    SourceError,
    entry_text,
    expand,
    ref_text,
)
from .admission import read_admissions
from .geometry import die_box, overlaps, turned
from .inventory import DieFolder, InventoryError, InventoryPort, read_die_folder
from .scalars import scalar
from .unplaced import PLACE

#: The note a source with no dies carries. Elaboration promotes it to a refusal.
NO_DIES = "no_dies"

#: Directions a lane end may have, and which of them drives.
_DRIVES = {"OUTPUT": True, "INPUT": False}


@dataclass(frozen=True)
class PlannedLink:
    """One typed link: ``a`` drives (the MANAGER end), lanes paired by ordinal."""

    a: PortRef
    b: PortRef
    a_pads: Tuple[str, ...]
    b_pads: Tuple[str, ...]
    net: str  # the link's net name; lane-indexed when it has two or more lanes
    line: int


#: Where a tie sits. A rail or a port that does not leave the carrier has none.
EDGE: Literal["edge"] = "edge"
UNPLACED: Literal["unplaced"] = "unplaced"
ALONE: Literal["alone"] = "alone"
Origin = Literal["edge", "unplaced", "alone"]


class _Leaves:
    """Whether this tie leaves the carrier. The one predicate."""

    origin: Optional[Origin]

    @property
    def leaves(self) -> bool:
        return self.origin in (EDGE, UNPLACED)


@dataclass(frozen=True)
class PlannedTie(_Leaves):
    instance: str
    pad: str
    net: str
    net_class: str
    line: int
    port: str
    #: ``edge``, ``unplaced`` or ``alone``. A one-pad rail that does not leave
    #: the carrier has none.
    origin: Optional[Origin]


@dataclass(frozen=True)
class PlannedSupply(_Leaves):
    net: str
    net_class: str
    endpoints: Tuple[Tuple[str, str], ...]  # (instance, pad)
    line: int
    #: ``edge`` or ``unplaced`` when the rail leaves the carrier, else none.
    origin: Optional[Origin] = None


@dataclass(frozen=True)
class Suggestion:
    """A line ready to paste for ports the file leaves unaccounted."""

    ports: Tuple[str, ...]  # the refs this line accounts for
    block: str
    line: str  # the item line, with the block's indentation
    replace_line: Optional[int] = None
    #: Set on each of several lines of which one is to be pasted: the sentence
    #: that says so, the same on every alternative.
    choice: Optional[str] = None

    def records(self) -> List[Dict[str, object]]:
        return [{"port": port, "block": self.block, "line": self.line,
                 "replace_line": self.replace_line, "choice": self.choice}
                for port in self.ports]


def _owned(suggestions: Sequence[Suggestion]) -> Iterator[Tuple[Suggestion, Tuple[str, ...]]]:
    """Each suggestion with the ports it is the first to account for: a port that
    several alternatives, or a new rail and its edge, account for is the first's."""
    seen = set()
    for s in suggestions:
        refs = tuple(ref for ref in dict.fromkeys(s.ports) if ref not in seen)
        seen.update(refs)
        yield s, refs


def unaccounted_ports(suggestions: Sequence[Suggestion]) -> List[str]:
    """Each unaccounted port a suggested line takes, once, in the lines' order."""
    return [ref for _, refs in _owned(suggestions) for ref in refs]


@dataclass(frozen=True)
class Plan:
    source: Source
    folders: Dict[str, DieFolder]
    links: Tuple[PlannedLink, ...]
    ties: Tuple[PlannedTie, ...]
    supplies: Tuple[PlannedSupply, ...]
    #: Per external edge, in file order: (edge, the net of each pad, in row order).
    edges: Tuple[Tuple[str, Tuple[str, ...]], ...]
    findings: Tuple[Finding, ...]
    suggestions: Tuple[Suggestion, ...] = ()
    #: The line that first names each (instance, port) the file accounts for.
    accounted: Mapping[Tuple[str, str], int] = field(default_factory=dict)
    #: The findings that only say ports a bus between two dies would take are
    #: unaccounted (part of ``findings``): what a blank sheet draws.
    drawable: Tuple[Finding, ...] = ()

    @property
    def refusals(self) -> Tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.note)

    @property
    def sheet_base_refusals(self) -> Tuple[Finding, ...]:
        """The refusals of a file read as the base of a blank sheet, which may
        leave unaccounted the ports a bus between two dies would take: the sheet
        is what wires them. A rail port or a port with no partner is still the
        file's to state."""
        return tuple(f for f in self.refusals if f not in self.drawable)

    @property
    def notes(self) -> Tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.note)


def link_net(net: str, lane_count: int, lane: int) -> str:
    """The net of one lane, as :func:`chiplet_system_description.model.link_net_name` names it."""
    return net if lane_count == 1 else f"{net}{lane}"


def tie_net(instance: str, pad: str) -> str:
    """The net of one pad of a port on its own (``alone``, or an external
    entry), named after the pad."""
    return f"{instance}_{pad}"


@dataclass(frozen=True)
class PlannedNet(_Leaves):
    """One net :func:`plan` names. Endpoints are die pins only.

    ``origin`` is the tie's: ``edge`` or ``unplaced`` when the net leaves the
    carrier, ``alone`` when the pad stays on the die, and none for a link or a
    rail that does not leave. ``leaves`` is that fact (:class:`_Leaves`). A
    sheet net of a leaving net holds one carrier pad.
    """

    name: str
    net_class: str
    endpoints: FrozenSet[Tuple[str, str]]
    origin: Optional[Origin]


class PlanRefused(SourceError):
    """:func:`plan` refused the source, so no net list is returned."""


def planned_nets(source: Source, *, unplaced: bool = False, sheet_base: bool = False
                 ) -> Tuple[Plan, Tuple[PlannedNet, ...]]:
    """The nets :func:`plan` names, or :class:`PlanRefused` when it refuses.

    ``sheet_base`` keeps the refusals a blank sheet may leave undrawn out of
    the check (:attr:`Plan.sheet_base_refusals`). Nothing of the connectivity
    form is read here.
    """
    topology = plan(source, unplaced=unplaced)
    refusals = topology.sheet_base_refusals if sheet_base else topology.refusals
    if refusals:
        raise PlanRefused(source.path, refusals)
    nets: List[PlannedNet] = []
    for link in topology.links:
        lanes = len(link.a_pads)
        for i, (a, b) in enumerate(zip(link.a_pads, link.b_pads)):
            nets.append(PlannedNet(
                link_net(link.net, lanes, i), "signal",
                frozenset({(link.a.instance, a), (link.b.instance, b)}), None))
    for tie in topology.ties:
        nets.append(PlannedNet(
            tie.net, tie.net_class, frozenset({(tie.instance, tie.pad)}), tie.origin))
    for supply in topology.supplies:
        nets.append(PlannedNet(
            supply.net, supply.net_class, frozenset(supply.endpoints), supply.origin))
    return topology, tuple(nets)


def collapse(names: Sequence[str]) -> List[str]:
    """``names`` with each run of consecutive indexed names as one ``[a..b]``."""
    out: List[str] = []
    i = 0
    split = [re.fullmatch(r"(.*?)(0|[1-9][0-9]*)(\D*)", n) for n in names]
    while i < len(names):
        m = split[i]
        j = i
        if m:
            while (j + 1 < len(names) and split[j + 1]
                   and split[j + 1].group(1) == m.group(1)
                   and split[j + 1].group(3) == m.group(3)
                   and int(split[j + 1].group(2)) == int(split[j].group(2)) + 1):
                j += 1
        if j > i:
            out.append(f"{m.group(1)}[{m.group(2)}..{split[j].group(2)}]{m.group(3)}")
        else:
            out.append(names[i])
        i = j + 1
    return out


def natural(name: str):
    """Sort key putting ``x2`` before ``x10``."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def _listed(names: Sequence[str], verb: str, rest: str) -> str:
    """``names`` and ``verb`` agreeing in number with them, then ``rest``."""
    return f"{', '.join(names)} {verb}{'s' if len(names) == 1 else ''} {rest}"


@dataclass(frozen=True)
class _Departures:
    """One walk of alone, the external rows and ``unplaced.pins``.

    A port keeps the last place that names it. A rail name keeps the first.
    ``escaping`` is the rail names that leave, or None when the file has no
    external block, even if ``unplaced.pins`` names a rail.
    """

    wanted: Dict[Tuple[str, str], Tuple[int, Origin]]
    rail_at: Dict[str, Origin]
    edges: Tuple[Tuple[str, Tuple[str, ...]], ...]
    escaping: Optional[FrozenSet[str]]


class _Planner:
    def __init__(self, source: Source, folders: Dict[str, DieFolder],
                 broken: Dict[str, str]):
        self.source = source
        self.folders = folders
        self.broken = broken  # instance -> why its folder could not be read
        self.findings: List[Finding] = []
        self.accounted: Dict[Tuple[str, str], int] = {}
        self.drawable: List[Finding] = []
        # The first walk to name a port sets its line in accounted.
        self.departed = self._departures()

    def finding(self, line: int, message: str, *, note: bool = False,
                code: Optional[str] = None) -> Finding:
        self.findings.append(Finding(line, message, tier=1, note=note, code=code))
        return self.findings[-1]

    def port(self, ref: PortRef, line: int, where: str) -> Optional[InventoryPort]:
        """The inventory port ``ref`` names, or a finding and None."""
        if ref.instance in self.broken:
            return None
        folder = self.folders[ref.instance]
        self.accounted.setdefault((ref.instance, ref.port), line)
        if not folder.has_port(ref.port):
            self.finding(line, f"{where}: {ref_text(ref)}: die {folder.name} serves no port "
                               f"{ref.port!r}")
            return None
        return folder.port(ref.port)

    # --- signal pairs -------------------------------------------------------
    def orient(self, a: PortRef, pa: InventoryPort, b: PortRef, pb: InventoryPort,
               line: int, where: str) -> Optional[Tuple[PortRef, InventoryPort, PortRef, InventoryPort]]:
        ok = True
        for ref, port in ((a, pa), (b, pb)):
            if port.role != "SIGNAL":
                self.finding(line, f"{where}: {ref_text(ref)} is a {port.role} port; "
                                   "a lane joins two signal ports (a supply goes in rails)")
                ok = False
        if len(pa.pads) != len(pb.pads):
            self.finding(line, f"{where}: {ref_text(a)} has {len(pa.pads)} pad(s) and "
                               f"{ref_text(b)} has {len(pb.pads)}; the lane counts differ")
            ok = False
        if not ok:
            return None
        da, db = _DRIVES.get(pa.direction), _DRIVES.get(pb.direction)
        # INOUT may be either end, but not both: the pair must still say which
        # end drives, since a lane is written output end first.
        if da is None and db is None or da is not None and da == db:
            self.finding(line, f"{where}: {ref_text(a)} is {pa.direction or 'undirected'} and "
                               f"{ref_text(b)} is {pb.direction or 'undirected'}; a lane needs "
                               "one output end and one input end")
            return None
        if da is True or db is False:
            return a, pa, b, pb
        return b, pb, a, pa

    def buses(self) -> List[PlannedLink]:
        out = []
        for bus in self.source.buses:
            for name in bus.ports:
                a, b = PortRef(bus.ends[0], name), PortRef(bus.ends[1], name)
                pa, pb = self.port(a, bus.line, "buses"), self.port(b, bus.line, "buses")
                if pa is None or pb is None:
                    continue
                pair = self.orient(a, pa, b, pb, bus.line, "buses")
                if pair is None:
                    continue
                x, px, y, py = pair
                out.append(PlannedLink(x, y, px.pads, py.pads,
                                       f"{bus.net_prefix}{name}{bus.net_suffix}", bus.line))
        return out

    def links(self) -> List[PlannedLink]:
        out = []
        for link in self.source.links:
            pa = self.port(link.a, link.line, "links")
            pb = self.port(link.b, link.line, "links")
            if pa is None or pb is None:
                continue
            pair = self.orient(link.a, pa, link.b, pb, link.line, "links")
            if pair is None:
                continue
            x, px, y, py = pair
            net = link.net if link.net is not None else f"{link.a.instance}_{link.a.port}"
            out.append(PlannedLink(x, y, px.pads, py.pads, net, link.line))
        return out

    def _departures(self) -> "_Departures":
        """One walk of alone, the external rows and ``unplaced.pins``.

        A port keeps the last place that names it (alone, then an edge, then
        unplaced). A rail name keeps the first: the edges are walked before
        unplaced, and a later row does not move it. A row carries its edge
        name or the unplaced label, and its origin. The rail names that leave
        are collected on the same walk.
        """
        wanted: Dict[Tuple[str, str], Tuple[int, Origin]] = {}
        for item in self.source.alone:
            for ref in item.refs:
                if self.signal_single(ref, item.line, "alone"):
                    wanted[(ref.instance, ref.port)] = (item.line, ALONE)
        rail_at: Dict[str, Origin] = {}
        external = self.source.external
        edges: List[Tuple[str, Tuple[str, ...]]] = []
        escaping: Optional[set] = None if external is None else set()
        rows = [(edge.edge, edge.entries, edge.line, EDGE)
                for edge in (external.edges if external else ())]
        if self.source.unplaced is not None:
            unplaced = self.source.unplaced
            rows.append(("unplaced.pins", unplaced.pins, unplaced.pins_line, UNPLACED))
        for label, entries, line, origin in rows:
            where = label if origin == UNPLACED else f"external.edges.{label}"
            edge_name = None if origin == UNPLACED else label
            nets: List[str] = []
            for entry in entries:
                if isinstance(entry, str):
                    rail_at.setdefault(entry, origin)
                    if escaping is not None:
                        escaping.add(entry)
                    nets.append(entry)
                    continue
                port = self.signal_single(entry, line, where)
                if port is None:
                    continue
                if len(port.pads) != 1:
                    self.finding(line, f"{where}: {ref_text(entry)} has "
                                       f"{len(port.pads)} pads; an edge entry is one pad")
                    continue
                wanted[(entry.instance, entry.port)] = (line, origin)
                nets.append(tie_net(entry.instance, port.pads[0]))
            if edge_name is not None:
                edges.append((edge_name, tuple(nets)))
        return _Departures(wanted, rail_at, tuple(edges),
                           None if escaping is None else frozenset(escaping))

    # --- single-pad nets ------------------------------------------------------
    def singles(self) -> Tuple[List[PlannedTie], List[Tuple[str, str, Tuple[str, ...]]]]:
        """The alone and external-port ties, in inventory order, and the edges."""
        wanted, edges = self.departed.wanted, self.departed.edges
        ties = []
        for die in self.source.dies:
            if die.id in self.broken:
                continue
            for port in self.folders[die.id].ports:
                found = wanted.get((die.id, port.name))
                if found is None:
                    continue
                line, origin = found
                for pad in port.pads:
                    ties.append(PlannedTie(die.id, pad, tie_net(die.id, pad), "signal", line,
                                           port.name, origin))
        return ties, edges

    def signal_single(self, ref: PortRef, line: int, where: str) -> Optional[InventoryPort]:
        port = self.port(ref, line, where)
        if port is not None and port.role != "SIGNAL":
            self.finding(line, f"{where}: {ref_text(ref)} is a {port.role} port; it goes "
                               "in rails")
            return None
        return port

    def rails(self) -> Tuple[List[PlannedTie], List[PlannedSupply]]:
        ties, supplies = [], []
        for rail in self.source.rails:
            endpoints: List[Tuple[str, str]] = []
            joined: List[Tuple[PortRef, InventoryPort]] = []
            for ref in rail.ports:
                port = self.port(ref, rail.line, "rails")
                if port is None:
                    continue
                if port.rail_class != rail.net_class:
                    self.finding(rail.line, f"rails: {ref_text(ref)} is a {port.role} port and "
                                            f"rail {rail.name!r} is {rail.net_class}")
                    continue
                endpoints.extend((ref.instance, pad) for pad in port.pads)
                joined.append((ref, port))
            self.volts(rail, joined)
            origin = self.departed.rail_at.get(rail.name)
            if len(endpoints) == 1:
                ref, inv = joined[0]
                ties.append(PlannedTie(ref.instance, inv.pads[0], rail.name, rail.net_class,
                                       rail.line, inv.name, origin))
            elif endpoints:
                supplies.append(PlannedSupply(rail.name, rail.net_class, tuple(endpoints),
                                              rail.line, origin))
        return ties, supplies

    def volts(self, rail: RailItem, joined: Sequence[Tuple[PortRef, InventoryPort]]) -> None:
        """Hold ``rail`` to the volts of each supply domain its ports reach: a
        power rail's domains agree with each other and with the rail's own
        volts, and a ground rail's are 0. Values compare as written, with no
        tolerance, since they are nominal. What is not declared is a note
        naming it, never a refusal, so a source or a contract written before
        volts existed validates as it did."""
        reached, missing = self.reach(joined)
        if rail.net_class != "ground" and rail.volts is None:
            missing.insert(0, "the rail declares no volts")

        def said(values):
            return ", ".join(f"{instance} {domain} {volts:g} V"
                             for (instance, domain), volts in sorted(values.items()))

        if rail.net_class == "ground":
            hot = {key: volts for key, volts in reached.items() if volts != 0}
            if hot:
                self.finding(rail.line, f"rails: {rail.name} is a ground rail and reaches "
                                        f"{said(hot)}")
        elif len(set(reached.values())) > 1:
            self.finding(rail.line, f"rails: {rail.name} joins supply domains of different "
                                    f"volts: {said(reached)}")
        elif reached and rail.volts is not None and {rail.volts} != set(reached.values()):
            self.finding(rail.line, f"rails: {rail.name} is {rail.volts:g} V and reaches "
                                    f"{said(reached)}")
        if missing:
            self.finding(rail.line, f"rails: voltage not checked for {rail.name}: "
                                    f"{'; '.join(missing)}", note=True)

    def reach(self, joined: Sequence[Tuple[PortRef, InventoryPort]]
              ) -> Tuple[Dict[Tuple[str, str], float], List[str]]:
        """The volts of each (instance, domain) the pads of ``joined`` reach,
        and what keeps the others from being known, as the note says it."""
        reached: Dict[Tuple[str, str], float] = {}
        undeclared: List[str] = []  # instances whose contract states no volts
        unnamed: List[str] = []  # pads whose terminal names no domain
        for ref, port in joined:
            folder = self.folders[ref.instance]
            if folder.supply_volts is None:
                undeclared.append(ref.instance)
                continue
            for pad in port.pads:
                domain, volts = folder.supply(pad)
                if domain is None:
                    unnamed.append(f"{ref.instance}.{pad}")
                else:
                    reached[(ref.instance, domain)] = volts
        missing = []
        if undeclared:
            missing.append(_listed(list(dict.fromkeys(undeclared)), "declare", "no supply_domains"))
        if unnamed:
            missing.append(_listed(unnamed, "name", "no supply domain"))
        return reached, missing

    # --- whole-file checks ------------------------------------------------------
    def rails_escape(self) -> None:
        escaping = self.departed.escaping
        if escaping is None:
            return
        # A file that may leave its pins to placement may hold the rail there too.
        carrier = self.source.carrier
        held = self.source.unplaced is not None or (carrier is not None and carrier.size is None)
        for rail in self.source.rails:
            if rail.name not in escaping:
                self.finding(rail.line, f"rails: {rail.name!r} is on no external edge, so "
                                        "nothing outside the carrier supplies it; name it "
                                        "on an edge in external"
                                        + (" or in unplaced.pins" if held else ""))

    def nets_unique(self, links, ties, supplies) -> None:
        seen: Dict[str, int] = {}
        names: List[Tuple[str, int]] = []
        for link in links:
            names.extend((link_net(link.net, len(link.a_pads), i), link.line)
                         for i in range(len(link.a_pads)))
        names.extend((t.net, t.line) for t in ties)
        names.extend((s.net, s.line) for s in supplies)
        for net, line in names:
            if net in seen:
                self.finding(line, f"net {net!r} is also named on line {seen[net]}; "
                                   "net names are unique")
            else:
                seen[net] = line

    def placement(self) -> None:
        """Each placed die inside the carrier, when it has a size, and clear of
        every placed die before it."""
        carrier = self.source.carrier
        boxes = []
        for die in self.source.dies:
            if die.id in self.broken or die.at is None:
                continue
            folder = self.folders[die.id]
            extent = (folder.width, folder.height)
            w, h = turned(extent, die.rotation)
            box = die_box(extent, die.at, die.rotation)
            if carrier is not None and carrier.size is not None:
                cw, ch = carrier.size
                if box[0] < 0 or box[1] < 0 or box[2] > cw or box[3] > ch:
                    self.finding(die.line, f"dies: {die.id} ({w:g} x {h:g} um at "
                                           f"{die.at[0]:g}, {die.at[1]:g}) leaves the carrier "
                                           f"(0, 0 to {cw:g}, {ch:g})")
            for other_id, other in boxes:
                if overlaps(box, other):
                    self.finding(die.line, f"dies: {die.id} overlaps {other_id}")
            boxes.append((die.id, box))

    def unplaced(self, note: bool) -> None:
        """One finding per thing the source leaves to placement, each naming
        the fix: refusals, or notes when the caller reads an unplaced source."""
        source = self.source
        if not source.dies:
            self.finding(self.block_line("dies") or source.key_lines.get("carrier", 0),
                         "system: no dies yet; import the sheet (packaging-flow edit FILE "
                         "--sheet SHEET)", note=note, code=NO_DIES)
        if source.carrier is not None and source.carrier.size is None:
            self.finding(source.carrier.line, f"carrier: no size yet; run {PLACE}, which "
                                              "chooses it, or state size", note=note,
                         code="carrier_unsized")
        for die in source.dies:
            if die.at is None:
                self.finding(die.line, f"dies: {die.id} is not placed; run {PLACE}",
                             note=note, code="die_unplaced")
        if source.unplaced is not None:
            pins = [entry_text(e) for e in source.unplaced.pins]
            self.finding(source.unplaced.pins_line,
                         f"unplaced: {len(pins)} external pin(s) are on no edge yet: "
                         f"{', '.join(pins)}; run {PLACE}, which puts each on the carrier "
                         "edge nearest its die", note=note, code="pins_unplaced")

    def thickness(self) -> None:
        for die in self.source.dies:
            if die.thickness is None:
                self.finding(die.line, f"dies: {die.id}: thickness not given; 0.0 used",
                             note=True)

    # --- accounting and suggestions ------------------------------------------
    def edge_lines(self, rail_new, lines: List[str]) -> List[Suggestion]:
        """The external edge lines that carry each new rail: on the carrier edge
        nearest the first die it serves, appended to that edge's line if it has
        one. With no carrier size, or that die not placed, no edge can be chosen
        yet, and the rail goes in ``unplaced.pins`` for placement to put."""
        size = self.source.carrier.size
        by_edge: Dict[str, List[Tuple[str, Tuple[str, ...]]]] = {}
        for (name, _), refs in rail_new.items():
            at = self.source.die(refs[0].split(".", 1)[0]).at
            if size is None or at is None:
                by_edge.setdefault("unplaced", []).append((name, tuple(refs)))
                continue
            by_edge.setdefault(nearest_edge(at, size), []).append((name, tuple(refs)))
        out: List[Suggestion] = []
        held = by_edge.pop("unplaced", None)
        if held is not None:
            out.append(self.unplaced_line(held, lines))
        existing = {e.edge: e for e in self.source.external.edges}
        for edge, rails in by_edge.items():
            names = "".join(f", {scalar(n)}" for n, _ in rails)
            refs = tuple(r for _, rs in rails for r in rs)
            item = existing.get(edge)
            text = lines[item.line - 1].rstrip("\r").rstrip() if item else ""
            if item is not None and text.endswith("]"):
                out.append(Suggestion(refs, "external", text[:-1] + names + "]",
                                      replace_line=item.line))
            else:
                out.append(Suggestion(refs, "external",
                                      f"      {edge}: [{names[2:]}]"))
        return out

    def unplaced_line(self, rails, lines: List[str]) -> Suggestion:
        """The ``unplaced.pins`` line carrying ``rails``: the file's own with
        them appended, else a new one."""
        names = "".join(f", {scalar(n)}" for n, _ in rails)
        refs = tuple(r for _, rs in rails for r in rs)
        held = self.source.unplaced
        text = lines[held.pins_line - 1].rstrip("\r").rstrip() if held else ""
        if held is not None and text.endswith("]"):
            return Suggestion(refs, "unplaced", text[:-1] + names + "]",
                              replace_line=held.pins_line)
        return Suggestion(refs, "unplaced", f"  pins: [{names[2:]}]")

    def unaccounted(self) -> List[Suggestion]:
        """A finding per unaccounted port, and the lines that account for them."""
        missing: List[Tuple[str, InventoryPort]] = []
        for die in self.source.dies:
            if die.id in self.broken:
                continue
            for port in self.folders[die.id].ports:
                if (die.id, port.name) not in self.accounted:
                    missing.append((die.id, port))
        if not missing:
            return []
        open_ports = {(inst, port.name): port for inst, port in missing}
        order = [d.id for d in self.source.dies if d.id not in self.broken]
        buses: Dict[Tuple[str, str], List[str]] = {}
        taken = set()
        rail_new: Dict[Tuple[str, str], List[str]] = {}
        rail_add: Dict[int, List[str]] = {}
        alone: Dict[str, List[str]] = {}
        rails_by_name = {r.name: r for r in self.source.rails}
        escaping = self.departed.escaping
        #: Ports of a class two or more edge-carried rails could take: the
        #: file holds no voltage, so the author picks the rail.
        rail_pick: Dict[Tuple[int, ...], List[str]] = {}
        for inst, port in missing:
            if (inst, port.name) in taken:
                continue
            ref = f"{inst}.{port.name}"
            if port.rail_class:
                rail = rails_by_name.get(port.net)
                if rail is not None and (rail.net_class != port.rail_class or (
                        escaping is not None and rail.name not in escaping)):
                    rail = None
                if rail is None and escaping is not None:
                    fed = [r for r in self.source.rails
                           if r.net_class == port.rail_class and r.name in escaping]
                    if len(fed) > 1:
                        rail_pick.setdefault(tuple(r.line for r in fed), []).append(ref)
                        continue
                    rail = fed[0] if fed else None
                if rail is not None:
                    rail_add.setdefault(rail.line, []).append(ref)
                else:
                    name = port.net or port.name
                    if name in rails_by_name:
                        name = f"{inst}_{name}"
                    rail_new.setdefault((name, port.rail_class), []).append(ref)
                continue
            partner = None
            if port.role == "SIGNAL" and port.direction in _DRIVES:
                for other in order:
                    peer = open_ports.get((other, port.name))
                    if (other != inst and peer is not None and (other, port.name) not in taken
                            and peer.role == "SIGNAL" and peer.direction in _DRIVES
                            and peer.direction != port.direction
                            and len(peer.pads) == len(port.pads)):
                        partner = other
                        break
            if partner is not None:
                ends = tuple(sorted((inst, partner), key=order.index))
                buses.setdefault(ends, []).append(port.name)
                taken.add((inst, port.name))
                taken.add((partner, port.name))
            else:
                alone.setdefault(inst, []).append(port.name)
        out: List[Suggestion] = []
        for (a, b), names in buses.items():
            names = sorted(names, key=natural)
            ports = ", ".join(scalar(n) for n in collapse(names))
            out.append(Suggestion(tuple(f"{e}.{n}" for n in names for e in (a, b)), "buses",
                                  f"    - {{ends: [{scalar(a)}, {scalar(b)}], ports: [{ports}]}}"))
        lines = self.source.text.split("\n")
        def joined(rail, added) -> Optional[str]:
            text = lines[rail.line - 1].rstrip("\r")
            close = rail.ports_close
            if 0 <= close < len(text) and text[close] == "]":
                return text[:close] + "".join(f", {scalar(r)}" for r in added) + text[close:]
            return None

        for rail in self.source.rails:
            added = rail_add.get(rail.line)
            if not added:
                continue
            line = joined(rail, added)
            if line is not None:
                out.append(Suggestion(tuple(added), "rails", line, replace_line=rail.line))
            else:
                rail_new.setdefault((f"{rail.name}_more", rail.net_class), []).extend(added)
        by_line = {r.line: r for r in self.source.rails}
        for candidates, refs in rail_pick.items():
            rails = [by_line[n] for n in candidates]
            sentence = (f"{', '.join(refs)} {'joins' if len(refs) == 1 else 'join'} one of "
                        f"{', '.join(r.name for r in rails)}; the file holds no voltage, so "
                        "pick the one the die needs.")
            for rail in rails:
                line = joined(rail, rail_add.get(rail.line, []) + refs)
                if line is not None:
                    out.append(Suggestion(tuple(refs), "rails", line, replace_line=rail.line,
                                          choice=sentence))
        for (name, net_class), refs in rail_new.items():
            ports = ", ".join(scalar(r) for r in refs)
            out.append(Suggestion(tuple(refs), "rails",
                                  f"    - {{name: {scalar(name)}, class: {scalar(net_class)}, "
                                  f"ports: [{ports}]}}"))
        if escaping is not None and rail_new:
            out.extend(self.edge_lines(rail_new, lines))
        for inst, names in alone.items():
            names = sorted(names, key=natural)
            for name in collapse(names):
                out.append(Suggestion(tuple(f"{inst}.{n}" for n in _members(name, names)), "alone",
                                      f"    - {scalar(f'{inst}.{name}')}"))
        self.unaccounted_findings(out, [f"{i}.{p.name}" for i, p in missing])
        return out

    def unaccounted_findings(self, suggestions: Sequence[Suggestion],
                             refs: Sequence[str]) -> None:
        """One finding per suggested line, on the line it replaces, else on the
        key of the block it goes in, so each points where the ports go; a port
        two lines account for (a new rail and its edge) is reported once. A port
        no suggested line takes (a choice between rails none of whose lines can
        be extended as written) is reported on the rails key; the paste block,
        which counts what its lines take, does not count it."""
        reported = set()
        for s, owned in _owned(suggestions):
            if not owned:
                continue
            reported.update(owned)
            why = f"; {s.choice}" if s.choice else ""
            found = self.finding(s.replace_line or self.block_line(s.block),
                                 f"{len(owned)} die port(s) are not accounted for: "
                                 f"{', '.join(owned)}{why}")
            if s.block == "buses":
                self.drawable.append(found)
        rest = [ref for ref in refs if ref not in reported]
        if rest:
            self.finding(self.block_line("rails"),
                         f"{len(rest)} die port(s) are not accounted for: {', '.join(rest)}; "
                         "no line is suggested, so add each to the rail it joins")

    def block_line(self, block: str) -> int:
        """The line of ``block``'s key, or of ``dies`` when the file has no such
        block yet."""
        lines = self.source.key_lines
        if block == "unplaced" and self.source.unplaced is not None:
            return self.source.unplaced.line
        return lines.get(block) or lines.get("dies", 0)


_NEAREST = ("west", "east", "south", "north")


def nearest_edge(at: Sequence, size: Sequence) -> str:
    """The carrier edge nearest a point ``at`` on a carrier of ``size`` (um, the
    carrier frame): the one rule that puts an external entry on an edge, for the
    planner's suggested lines, placement and conversion alike. A tie goes to the
    first of west, east, south, north."""
    x, y = at
    gaps = (x, size[0] - x, y, size[1] - y)
    return _NEAREST[min(range(4), key=lambda i: gaps[i])]


def _members(written: str, names: Sequence[str]) -> List[str]:
    """The names of ``names`` one collapsed entry stands for."""
    expanded = set(expand(written))
    return [n for n in names if n in expanded]


def read_folders(source: Source) -> Tuple[Dict[str, DieFolder], Dict[str, str], List[Finding]]:
    """Each instance's die folder, resolved against the source's folder."""
    folders: Dict[str, DieFolder] = {}
    broken: Dict[str, str] = {}
    findings: List[Finding] = []
    cache: Dict[Path, Union[DieFolder, str]] = {}
    for die in source.dies:
        path = source.folder / die.die
        if path not in cache:
            try:
                cache[path] = read_die_folder(path)
            except InventoryError as exc:
                cache[path] = str(exc)
        got = cache[path]
        if isinstance(got, str):
            broken[die.id] = got
            findings.append(Finding(die.line, f"dies: {die.id}: {got}", tier=1))
            continue
        folders[die.id] = got
    return folders, broken, findings


def plan(source: Source, *, unplaced: bool = False) -> Plan:
    """Tier 1 over ``source``: its topology and every finding, in line order.

    ``unplaced`` reads a source that leaves its placement to ``packaging-flow
    place`` (format 1.3): each unplaced thing is a note instead of a refusal.
    Only a reader that never elaborates passes it (``check``, validate, place);
    such a plan's topology is not one to elaborate.
    """
    folders, broken, findings = read_folders(source)
    planner = _Planner(source, folders, broken)
    planner.findings.extend(findings)
    planner.findings.extend(read_admissions(source))
    links = planner.buses() + planner.links()
    ties, edges = planner.singles()
    rail_ties, supplies = planner.rails()
    ties = ties + rail_ties
    for item in source.open:
        for ref in item.refs:
            planner.port(ref, item.line, "open")
    planner.nets_unique(links, ties, supplies)
    planner.rails_escape()
    planner.placement()
    planner.unplaced(note=unplaced)
    planner.thickness()
    suggestions = planner.unaccounted()
    return Plan(source=source, folders=folders, links=tuple(links), ties=tuple(ties),
                supplies=tuple(supplies), edges=tuple(edges),
                findings=tuple(sorted(planner.findings, key=lambda f: f.line)),
                suggestions=tuple(suggestions), accounted=dict(planner.accounted),
                drawable=tuple(planner.drawable))
