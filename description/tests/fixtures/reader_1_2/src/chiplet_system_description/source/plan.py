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
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

from ..model import PortRef
from .document import (
    Finding,
    RailItem,
    Source,
    expand,
    ref_text,
)
from .inventory import DieFolder, InventoryError, InventoryPort, read_die_folder
from .scalars import scalar

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


@dataclass(frozen=True)
class PlannedTie:
    instance: str
    pad: str
    net: str
    net_class: str
    line: int


@dataclass(frozen=True)
class PlannedSupply:
    net: str
    net_class: str
    endpoints: Tuple[Tuple[str, str], ...]  # (instance, pad)
    line: int


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


class _Planner:
    def __init__(self, source: Source, folders: Dict[str, DieFolder],
                 broken: Dict[str, str]):
        self.source = source
        self.folders = folders
        self.broken = broken  # instance -> why its folder could not be read
        self.findings: List[Finding] = []
        self.accounted: Dict[Tuple[str, str], int] = {}
        self.drawable: List[Finding] = []

    def finding(self, line: int, message: str, *, note: bool = False) -> Finding:
        self.findings.append(Finding(line, message, tier=1, note=note))
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

    # --- single-pad nets ------------------------------------------------------
    def singles(self) -> Tuple[List[PlannedTie], List[Tuple[str, str, Tuple[str, ...]]]]:
        """The alone and external-port ties, in inventory order, and the edges."""
        wanted: Dict[Tuple[str, str], int] = {}
        for item in self.source.alone:
            for ref in item.refs:
                if self.signal_single(ref, item.line, "alone"):
                    wanted[(ref.instance, ref.port)] = item.line
        external = self.source.external
        edges: List[Tuple[str, Tuple[str, ...]]] = []
        for edge in (external.edges if external else ()):
            nets: List[str] = []
            for entry in edge.entries:
                if isinstance(entry, str):
                    nets.append(entry)
                    continue
                port = self.signal_single(entry, edge.line, f"external.edges.{edge.edge}")
                if port is None:
                    continue
                if len(port.pads) != 1:
                    self.finding(edge.line, f"external.edges.{edge.edge}: {ref_text(entry)} has "
                                            f"{len(port.pads)} pads; an edge entry is one pad")
                    continue
                wanted[(entry.instance, entry.port)] = edge.line
                nets.append(f"{entry.instance}_{port.pads[0]}")
            edges.append((edge.edge, tuple(nets)))
        ties = []
        for die in self.source.dies:
            if die.id in self.broken:
                continue
            for port in self.folders[die.id].ports:
                line = wanted.get((die.id, port.name))
                if line is None:
                    continue
                for pad in port.pads:
                    ties.append(PlannedTie(die.id, pad, f"{die.id}_{pad}", "signal", line))
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
            if len(endpoints) == 1:
                ties.append(PlannedTie(*endpoints[0], rail.name, rail.net_class, rail.line))
            elif endpoints:
                supplies.append(PlannedSupply(rail.name, rail.net_class, tuple(endpoints),
                                              rail.line))
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
    def escaping(self) -> Optional[set]:
        """The rail names an external edge carries, or None with no external block."""
        if self.source.external is None:
            return None
        return {e for edge in self.source.external.edges for e in edge.entries
                if isinstance(e, str)}

    def rails_escape(self) -> None:
        escaping = self.escaping()
        if escaping is None:
            return
        for rail in self.source.rails:
            if rail.name not in escaping:
                self.finding(rail.line, f"rails: {rail.name!r} is on no external edge, so "
                                        "nothing outside the carrier supplies it; name it "
                                        "on an edge in external")

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
        carrier = self.source.carrier
        boxes = []
        for die in self.source.dies:
            if die.id in self.broken:
                continue
            folder = self.folders[die.id]
            w, h = folder.width, folder.height
            if die.rotation in (90, 270):
                w, h = h, w
            box = (die.at[0] - w / 2, die.at[1] - h / 2, die.at[0] + w / 2, die.at[1] + h / 2)
            if carrier is not None:
                cw, ch = carrier.size
                if box[0] < 0 or box[1] < 0 or box[2] > cw or box[3] > ch:
                    self.finding(die.line, f"dies: {die.id} ({w:g} x {h:g} um at "
                                           f"{die.at[0]:g}, {die.at[1]:g}) leaves the carrier "
                                           f"(0, 0 to {cw:g}, {ch:g})")
            for other_id, other in boxes:
                if (box[0] < other[2] and other[0] < box[2]
                        and box[1] < other[3] and other[1] < box[3]):
                    self.finding(die.line, f"dies: {die.id} overlaps {other_id}")
            boxes.append((die.id, box))

    def thickness(self) -> None:
        for die in self.source.dies:
            if die.thickness is None:
                self.finding(die.line, f"dies: {die.id}: thickness not given; 0.0 used",
                             note=True)

    # --- accounting and suggestions ------------------------------------------
    def edge_lines(self, rail_new, lines: List[str]) -> List[Suggestion]:
        """The external edge lines that carry each new rail: on the carrier edge
        nearest the first die it serves, appended to that edge's line if it has one."""
        width, height = self.source.carrier.size
        by_edge: Dict[str, List[Tuple[str, Tuple[str, ...]]]] = {}
        for (name, _), refs in rail_new.items():
            x, y = self.source.die(refs[0].split(".", 1)[0]).at
            gaps = (x, width - x, y, height - y)
            edge = _NEAREST[min(range(4), key=lambda i: gaps[i])]
            by_edge.setdefault(edge, []).append((name, tuple(refs)))
        out: List[Suggestion] = []
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
        escaping = self.escaping()
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
        return lines.get(block) or lines.get("dies", 0)


_NEAREST = ("west", "east", "south", "north")


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
        else:
            folders[die.id] = got
    return folders, broken, findings


def plan(source: Source) -> Plan:
    """Tier 1 over ``source``: its topology and every finding, in line order."""
    folders, broken, findings = read_folders(source)
    planner = _Planner(source, folders, broken)
    planner.findings.extend(findings)
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
    planner.thickness()
    suggestions = planner.unaccounted()
    return Plan(source=source, folders=folders, links=tuple(links), ties=tuple(ties),
                supplies=tuple(supplies), edges=tuple(edges),
                findings=tuple(sorted(planner.findings, key=lambda f: f.line)),
                suggestions=tuple(suggestions), accounted=dict(planner.accounted),
                drawable=tuple(planner.drawable))
