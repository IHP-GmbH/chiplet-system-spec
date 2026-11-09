# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Board and schematic edits of a ``system.chiplet``, as edits its one writer applies.

A flow verb reads the file once and passes its bytes here with what was edited:

- :func:`board_edits` takes an ``interposer_pnr.board_revision`` v1 report (the
  reconciled board: placements and the netlist it observed). The board moves
  dies and renames nets; :func:`chiplet_system_description.revision.revise_system` admits
  exactly that, and refuses any change to which ports connect.
- :func:`schematic_edits` takes a ``chiplet_system.connectivity`` form of a
  system sheet. A generated sheet is the one a run drew from its
  ``design.chiplet``; a blank sheet (form 1.1) is one a person drew from the
  symbol library, against the file itself, which may still leave ports
  unaccounted. The sheet changes which ports connect: its nets are read with
  the schematic import's checks (generator report or designators, pin lists,
  nets bound to the carrier held), then lifted back to items by the
  converter's rules. A classed net is a rail, a net joining two ports lane by
  lane a link (or a bus lane it keeps), a port alone on nets named after its
  pads ``alone``, a port on no net ``open``. On a blank sheet a port on no net
  is refused instead, unless the file already states it ``open``: the sheet is
  what wires the dies, so a port it leaves bare is a connection not drawn yet.
- :func:`classic_edits` (:mod:`chiplet_system_description.classic_import`,
  exported here) takes the form of a classic sheet (form 1.2), drawn from
  blank with no file behind it, and writes the whole system it draws into its
  project file; :func:`schematic_edits` hands a classic form there.

Each returns the :mod:`~chiplet_system_description.source.patch` edits that write the
change, after proving them: the patched text is elaborated again and must give
the revised System's design bytes (the board, placements on the 1 nm grid), or
the sheet's nets with everything else unchanged (the sheet). A change the
file cannot hold raises :class:`EditRefused` with one finding per reason, each
with a code (the contract), the line, the port when there is one, and a
sentence. Whether the bytes are the ones a run recorded is the caller's check,
on the bytes it read, before it calls anything here; :func:`patch_system`
returns their digest so the caller can check them again just before it writes.
"""
from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from .._cfio import cfio
from ..elaborate import elaborate
from ..errors import SystemSpecError
from ..model import System
from ..revision import BoardRevisionError, revise_system
from .document import BusItem, Source, check_text, ref_text
from .elaborate import Resolver, build
from .patch import (
    Change,
    Delete,
    Edit,
    EditFinding,
    EditRefused,
    Insert,
    Patch,
    Place,
    Replace,
    SetSize,
    decode,
    item_names,
    not_a_source,
    patch_system,
    ref_list,
)
from .lanes import read_lanes
from .plan import Plan, plan, tie_net
from .scalars import quantized
from .writer import SourceWriteError, _bus_affixes

__all__ = ["Change", "Delete", "Edit", "EditFinding", "EditRefused", "Insert", "Patch",
           "Place", "Replace", "SetSize", "board_edits", "classic_edits", "patch_system",
           "revision_edits", "schematic_edits"]


def classic_edits(data: bytes, *, path: Union[str, Path], form: Union[str, Path],
                  resolver: Optional[Any] = None,
                  previous: Optional[Union[str, Path]] = None):
    """Classic-sheet edits. Imported from :mod:`chiplet_system_description.classic_import`
    on the call, so importing this module does not import that one."""
    from ..classic_import import classic_edits as _classic_edits
    return _classic_edits(data, path=path, form=form, resolver=resolver, previous=previous)

BOARD_REPORT = "interposer_pnr.board_revision"


def _rotation(angle: float, line: int, instance: str) -> int:
    turned = math.fmod(angle, 360.0)
    if turned < 0:
        turned += 360.0
    nearest = round(turned / 90.0) * 90 % 360
    if abs(turned - nearest) > 1e-9 and abs(turned - 360.0 - nearest) > 1e-9:
        raise SourceWriteError(line, f"{instance} is turned {angle:g} degrees on the board; a die "
                                     "turns by 0, 90, 180 or 270 degrees.",
                               code="rotation_not_manhattan", port=None)
    return int(nearest)


def revision_edits(source: Source, topology: Plan, old: System, new: System) -> Tuple[Edit, ...]:
    """The edits that write ``new`` into ``source``, which elaborated to ``old``.

    ``new`` keeps ``old``'s structure (revise_system guarantees it): links, ties
    and supplies are matched by position, which elaboration and revision both
    keep. What the file has no field for raises :class:`SourceWriteError`.
    """
    if (len(old.links), len(old.ties), len(old.supplies)) != (
            len(topology.links), len(topology.ties), len(topology.supplies)) or (
            (len(new.links), len(new.ties), len(new.supplies))
            != (len(old.links), len(old.ties), len(old.supplies))):
        raise SourceWriteError(0, "The board changes which ports connect; a board edit moves dies "
                                  "and renames nets only. Change connections in the schematic.",
                               code="connection_changed")
    names = item_names(source.text)
    edits: List[Edit] = []

    # placements
    new_by_id = new.instances_by_id()
    for die in source.dies:
        inst = new_by_id.get(die.id)
        if inst is None:
            raise SourceWriteError(die.line, f"{die.id} is not on the board.",
                                   code="connection_changed")
        xy = (inst.position.x, inst.position.y)
        if any(quantized(v) != quantized(w) for v, w in zip(xy, die.at)):
            edits.append(Replace("dies", die.id, "at", tuple(quantized(v) for v in xy)))
        turned = _rotation(inst.rotation_z, die.line, die.id)
        if turned != die.rotation:
            edits.append(Replace("dies", die.id, "rotation", turned))

    # link nets: a link item's net, or a whole bus's affixes
    bus_nets: Dict[int, Dict[str, str]] = {}
    renamed_bus: Dict[int, List[str]] = {}
    link_lines: Dict[int, List[Tuple[Any, Any, Any]]] = {}
    for planned, before, after in zip(topology.links, old.links, new.links):
        if any(l.line == planned.line for l in source.links):
            link_lines.setdefault(planned.line, []).append((planned, before, after))
            continue
        bus_nets.setdefault(planned.line, {})[planned.a.port] = after.net_prefix
        if before.net_prefix != after.net_prefix:
            renamed_bus.setdefault(planned.line, []).append(planned.a.port)
    for line, lanes in link_lines.items():
        moved = [(p, b, a) for p, b, a in lanes if b.net_prefix != a.net_prefix]
        if not moved:
            continue
        planned = moved[0][0]
        if len(lanes) > 1:
            raise SourceWriteError(line, f"The net of {ref_text(planned.a)} was renamed; this "
                                         "link line names a range of links with one net range. "
                                         "Write its links one per line to rename one.",
                                   code="rename_no_field", port=ref_text(planned.a))
        item = next(l for l in source.links if l.line == line)
        default = f"{item.a.instance}_{item.a.port}"
        net = moved[0][2].net_prefix
        edits.append(Replace("links", names[("links", line)], "net",
                             None if net == default else net))
    for line, renamed in renamed_bus.items():
        bus = next(b for b in source.buses if b.line == line)
        affixes = _bus_affixes(bus.ports, [bus_nets[line][p] for p in bus.ports])
        lane = f"{bus.ends[0]}.{sorted(renamed)[0]}"
        if affixes is None:
            raise SourceWriteError(line, f"Lane {lane} was renamed alone; the bus "
                                         f"{bus.ends[0]}-{bus.ends[1]} names all its lanes with "
                                         "one prefix and suffix. Rename every lane, or none.",
                                   code="rename_no_field", port=lane)
        name = names[("buses", line)]
        for key, value in zip(("net_prefix", "net_suffix"), affixes):
            edits.append(Replace("buses", name, key, value or None))

    # tie and supply nets: only a rail has a name to write
    renamed_rails: Dict[int, str] = {}
    for planned, before, after in zip(topology.ties, old.ties, new.ties):
        if before.net == after.net:
            continue
        rail = next((r for r in source.rails if r.line == planned.line), None)
        if rail is None:
            ref = f"{planned.instance}.{planned.port}"
            raise SourceWriteError(planned.line, f"The net of {ref} was renamed to {after.net}; a "
                                                 f"port on its own keeps the name {planned.net}. "
                                                 "Rename it back, or connect it as a link.",
                                   code="rename_no_field", port=ref)
        renamed_rails[rail.line] = after.net
    for planned, before, after in zip(topology.supplies, old.supplies, new.supplies):
        if before.net != after.net:
            renamed_rails[planned.line] = after.net
    taken = {r.name for r in source.rails}
    for line, net in renamed_rails.items():
        rail = next(r for r in source.rails if r.line == line)
        if net in taken - {rail.name}:
            raise SourceWriteError(line, f"Rail {rail.name} was renamed to {net}, which another "
                                         "rail already has.", code="rename_taken")
        edits.append(Replace("rails", rail.name, "name", net))
    return tuple(edits)


# ----------------------------------------------------------------------
# The board
# ----------------------------------------------------------------------
def _read(text: str, path: Union[str, Path], *, sheet_base: bool = False
          ) -> Tuple[Source, Plan]:
    source, findings = check_text(text, path)
    if findings or source is None:
        raise not_a_source(findings)
    topology = plan(source)
    refusals = topology.sheet_base_refusals if sheet_base else topology.refusals
    if refusals:
        raise not_a_source(refusals)
    return source, topology


def _refused(exc: Union[SourceWriteError, BoardRevisionError], line: int = 0) -> EditRefused:
    if isinstance(exc, SourceWriteError):
        return EditRefused([EditFinding(exc.code, exc.line, exc.port, exc.message)])
    return EditRefused([EditFinding(exc.code, line, None, _BOARD_WORDS.get(exc.code, "")
                                    + str(exc))])


#: The sentence a board refusal opens with, by code; the model's detail follows.
_BOARD_WORDS = {
    "report_malformed": "The board report is not a board_revision v1 document: ",
    "connection_changed": "The board changes which ports connect; a board edit moves dies and "
                          "renames nets only. Change connections in the schematic. Detail: ",
    "carrier_net_changed": "A carrier pad's net changed on the board; the external interface is "
                           "edited in the file, not on the board. Detail: ",
    "frame_changed": "A die's anchor or orientation changed on the board: ",
    "rename_no_field": "A link's lanes were renamed apart; a link names its lanes with one "
                       "prefix. Detail: ",
}


def _on_grid(system: System) -> System:
    """``system`` with each placement on the 1 nm grid and its rotation in [0, 360)."""
    instances = tuple(dataclasses.replace(
        inst, position=dataclasses.replace(inst.position, x=float(quantized(inst.position.x)),
                                           y=float(quantized(inst.position.y))),
        rotation_z=float(_rotation(inst.rotation_z, 0, inst.id)))
        for inst in system.instances)
    return dataclasses.replace(system, instances=instances)


def _prove(data: bytes, path, resolver: Resolver, want: System) -> None:
    text, _ = decode(data)
    source, topology = _read(text, path)
    got = build(source, topology, resolver)
    if cfio.dumps(elaborate(got)) != cfio.dumps(elaborate(want)):
        raise SystemSpecError(f"{path}: the patched file does not elaborate to the edited system; "
                              "this is a defect of the writer, nothing was written")


def board_edits(data: bytes, *, path: Union[str, Path], resolver: Resolver,
                reconciliation: Mapping) -> Tuple[Edit, ...]:
    """The edits that write a reconciled board into the source ``data``.

    ``path`` is where ``data`` was read from: the die folders resolve from its
    folder and findings name it. ``reconciliation`` is the parsed
    ``interposer_pnr.board_revision`` v1 report.
    """
    text, _ = decode(data)
    source, topology = _read(text, path)
    report = reconciliation if isinstance(reconciliation, Mapping) else {}
    shape = (report.get("format"), report.get("version"), report.get("units"),
             report.get("frame"), report.get("copper_policy"))
    if shape != (BOARD_REPORT, 1, "um", "gds_y_up", "reroute") or isinstance(
            report.get("version"), bool) or not isinstance(report.get("netlist"), Mapping):
        raise EditRefused([EditFinding(
            "report_malformed", 0, None,
            f"The board report is not a {BOARD_REPORT} v1 document in um, gds_y_up, with a "
            "netlist and copper_policy reroute.")])
    old = build(source, topology, resolver)
    try:
        new = revise_system(old, placements=report.get("placements"),
                            nets=report["netlist"].get("nets"))
        edits = revision_edits(source, topology, old, new)
    except (SourceWriteError, BoardRevisionError) as exc:
        raise _refused(exc) from exc
    _prove(patch_system(data, edits).text, path, resolver, _on_grid(new))
    return edits


# ----------------------------------------------------------------------
# The sheet
# ----------------------------------------------------------------------
Port = Tuple[str, str]  # (instance, port name)

#: The sentence an import refusal opens with when the edit words it its own way.
_SHEET_WORDS = {
    "sheet_other_run": "The sheet was drawn from a different run.",
    "carrier_net_changed": "A net bound outside the dies (a carrier pad, an external net) "
                           "changed on the sheet; the external interface is edited in the file, "
                           "not on the sheet.",
}
_SHEET_CODES = {"base_digest_mismatch": "sheet_other_run",
                "bound_net_changed": "carrier_net_changed"}


def _terms(row: Mapping) -> frozenset:
    return frozenset((c["component"], c["pin"]) for c in row["connections"])


def _connectivity(rows) -> set:
    return {(r["name"], r.get("class", "signal"), _terms(r)) for r in rows}


class _Lift:
    """The items a sheet's nets ask for, against the items the file has."""

    def __init__(self, source: Source, topology: Plan, sheet, *, blank: bool = False):
        self.source, self.topology, self.sheet, self.blank = source, topology, sheet, blank
        self.names = item_names(source.text)
        self.findings: List[EditFinding] = []
        self.edits: List[Edit] = []
        self.order = {d.id: n for n, d in enumerate(source.dies)}
        self.ports: Dict[Port, Any] = {}
        self.port_of: Dict[Tuple[str, str], Port] = {}
        self.rank: Dict[Port, Tuple[int, int]] = {}
        for die in source.dies:
            for n, port in enumerate(topology.folders[die.id].ports):
                self.ports[(die.id, port.name)] = port
                self.rank[(die.id, port.name)] = (self.order[die.id], n)
                for pad in port.pads:
                    self.port_of[(die.id, pad)] = (die.id, port.name)
        held = sheet.held
        self.held_pads = {t for r in sheet.base_nets if r["name"] in held for t in _terms(r)}
        self.nets = {r["name"]: r for r in sheet.nets if r["name"] not in held}

    def refuse(self, code: str, port: Optional[Port], message: str, line: int = 0) -> None:
        self.findings.append(EditFinding(code, line, port and f"{port[0]}.{port[1]}", message))

    @staticmethod
    def text(port: Port) -> str:
        return f"{port[0]}.{port[1]}"

    # --- what the sheet asks for ----------------------------------------------
    def wanted(self):
        rails: Dict[str, Tuple[str, List[Port]]] = {}
        lanes: Dict[frozenset, List[Tuple[str, frozenset]]] = {}
        alone_pads, net_of = set(), {}
        for name, row in self.nets.items():
            terms = sorted(t for t in _terms(row) if t in self.port_of)
            for t in terms:
                net_of[t] = name
            ports = sorted({self.port_of[t] for t in terms}, key=self.rank.get)
            net_class = row.get("class", "signal")
            if net_class != "signal":
                for port in ports:
                    rest = [p for p in self.ports[port].pads if (port[0], p) not in terms]
                    if rest:
                        self.refuse("pad_split", port,
                                    f"Pad {rest[0]} of {self.text(port)} is not on net {name} "
                                    f"with the other pads of {self.text(port)}; a port is "
                                    "wired whole.")
                rails[name] = (net_class, ports)
            elif len(terms) == 1:
                (inst, pad), = terms
                if name == tie_net(inst, pad):
                    alone_pads.add(terms[0])
                else:
                    self.refuse("rename_no_field", ports[0],
                                f"The net of {self.text(ports[0])} was renamed to {name}; a port "
                                f"on its own keeps the name {tie_net(inst, pad)}. Rename it back, "
                                "or connect it as a link.")
            elif len(terms) == 2 and len(ports) == 2 and ports[0][0] == ports[1][0]:
                self.refuse("not_expressible", ports[0],
                            f"Net {name} joins {self.text(ports[0])} and {self.text(ports[1])}, "
                            "on one die; a link joins two dies.")
            elif len(terms) == 2 and len(ports) == 2:
                lanes.setdefault(frozenset(ports), []).append((name, frozenset(terms)))
            else:
                joined = ", ".join(f"{i}.{p}" for i, p in terms)
                self.refuse("not_expressible", ports[0] if ports else None,
                            f"Net {name} joins {joined}; a signal net joins two ports lane by "
                            "lane, and a net over more needs the power or ground class.")
        links, found = read_lanes(lanes, lambda p: self.ports[p].pads, self.rank.get)
        for f in found:
            self.refuse(f.code, f.port, f.message[0].upper() + f.message[1:] + ".")
        pairs = {key: net for key, (_, _, net) in links.items()}
        alone, open_ = set(), set()
        stated_alone = {(r.instance, r.port) for item in self.source.alone for r in item.refs}
        for port, spec in self.ports.items():
            pads = [(port[0], p) for p in spec.pads]
            if any(t in self.held_pads for t in pads):
                if port in stated_alone:
                    alone.add(port)     # held on a net of its own: still alone
                continue
            on = [net_of.get(t) for t in pads]
            if all(n is None for n in on):
                open_.add(port)
            elif all(t in alone_pads for t in pads):
                alone.add(port)
            elif any(n is None for n in on) or any(t in alone_pads for t in pads):
                self.refuse("pad_split", port,
                            f"Some pads of {self.text(port)} moved without the others; a port "
                            "is wired whole.")
        return rails, pairs, alone, open_

    # --- the edits --------------------------------------------------------------
    def rails(self, wanted: Dict[str, Tuple[str, List[Port]]]) -> None:
        for rail in self.source.rails:
            if rail.name in self.sheet.held:
                continue
            name = self.sheet.renamed.get(rail.name, rail.name)
            if name not in wanted:
                self.edits.append(Delete("rails", rail.name))
                continue
            _, ports = wanted.pop(name)
            if name != rail.name:
                self.edits.append(Replace("rails", rail.name, "name", name))
            have = [(r.instance, r.port) for r in rail.ports]
            if set(have) != set(ports):
                keep = [p for p in have if p in ports] + [p for p in ports if p not in have]
                self.edits.append(Replace("rails", rail.name, "ports",
                                          [self.text(p) for p in keep]))
        for name, (net_class, ports) in wanted.items():
            self.edits.append(Insert("rails", (("name", name), ("class", net_class),
                                               ("ports", [self.text(p) for p in ports]))))

    def pairs(self, wanted: Dict[frozenset, str]) -> None:
        by_line: Dict[int, List[Any]] = {}
        for planned in self.topology.links:
            by_line.setdefault(planned.line, []).append(planned)
        lines = {b.line: b for b in self.source.buses}
        lines.update({l.line: l for l in self.source.links})
        for line, planned in by_line.items():
            item = lines[line]
            keys = [frozenset(((p.a.instance, p.a.port), (p.b.instance, p.b.port)))
                    for p in planned]
            bases = [p.net for p in planned]
            if isinstance(item, BusItem):
                kept = [(item_port, wanted.pop(k)) for item_port, k in zip(
                    (p.a.port for p in planned), keys) if k in wanted]
                self._bus(item, planned, kept)
                continue
            if len(planned) > 1:
                if any(wanted.get(k) != b for k, b in zip(keys, bases)):
                    self.refuse("not_expressible", None,
                                f"Line {line} writes a range of links, and the sheet changes "
                                "one of them; write its links one per line first.", line)
                for k in keys:
                    wanted.pop(k, None)
                continue
            name = self.names[("links", line)]
            if keys[0] not in wanted:
                self.edits.append(Delete("links", name))
                continue
            net = wanted.pop(keys[0])
            if net != bases[0]:
                default = f"{item.a.instance}_{item.a.port}"
                self.edits.append(Replace("links", name, "net", None if net == default else net))
        for key, net in sorted(wanted.items(), key=lambda kv: sorted(map(self.rank.get, kv[0]))):
            a, b = sorted(key, key=lambda p: (self.ports[p].direction != "OUTPUT",
                                              self.rank[p]))
            self.edits.append(Insert("links", (("a", self.text(a)), ("b", self.text(b)),
                                               ("net", net))))

    def _bus(self, bus: BusItem, planned, kept: List[Tuple[str, str]]) -> None:
        name = self.names[("buses", bus.line)]
        if not kept:
            self.edits.append(Delete("buses", name))
            return
        ports = [p for p, _ in kept]
        if ports != [p.a.port for p in planned]:
            # the bus's port names are the a end's and the b end's alike
            self.edits.append(Replace("buses", name, "ports", ports))
        affixes = _bus_affixes(ports, [net for _, net in kept])
        if affixes is None:
            moved = next(p for p, net in kept
                         if net != f"{bus.net_prefix}{p}{bus.net_suffix}")
            lane = f"{bus.ends[0]}.{moved}"
            self.refuse("rename_no_field", (bus.ends[0], moved),
                        f"Lane {lane} was renamed alone; the bus {bus.ends[0]}-{bus.ends[1]} "
                        "names all its lanes with one prefix and suffix. Rename every lane, or "
                        "none.", bus.line)
            return
        for key, value, was in zip(("net_prefix", "net_suffix"), affixes,
                                   (bus.net_prefix, bus.net_suffix)):
            if (value or "") != was:
                self.edits.append(Replace("buses", name, key, value or None))

    def refs(self, block: str, items, wanted: set) -> None:
        put: List[Port] = []
        for item in items:
            ports = [(r.instance, r.port) for r in item.refs]
            if all(p in wanted for p in ports):
                wanted -= set(ports)
                continue
            self.edits.append(Delete(block, self.names[(block, item.line)]))
            for p in ports:
                if p in wanted:
                    put.append(p)
                    wanted.discard(p)
        put.extend(wanted)
        for ref in ref_list([self.text(p) for p in sorted(put, key=self.rank.get)]):
            self.edits.append(Insert(block, ref))

    def run(self) -> Tuple[Edit, ...]:
        rails, pairs, alone, open_ = self.wanted()
        if self.blank:
            stated = {(r.instance, r.port) for item in self.source.open for r in item.refs}
            for port in sorted(open_ - stated, key=self.rank.get):
                self.refuse("pin_not_wired", port,
                            f"{self.text(port)} is not wired; draw its connection from the "
                            "diagram", _line(self.topology, self.source, port))
        if self.findings:
            raise EditRefused(self.findings)
        self.rails(rails)
        self.pairs(pairs)
        self.refs("alone", self.source.alone, alone)
        self.refs("open", self.source.open, open_)
        if self.findings:
            raise EditRefused(self.findings)
        return tuple(self.edits)


def _line(topology: Plan, source: Source, port: Optional[Port] = None,
          instance: Optional[str] = None) -> int:
    """The line a finding about ``port`` (or die ``instance``) points at: the
    line that accounts for the port, else the die's line, else 0."""
    if port is not None and port in topology.accounted:
        return topology.accounted[port]
    die = port[0] if port is not None else instance
    return next((d.line for d in source.dies if d.id == die), 0)


def _from_import(r: Mapping, topology: Plan, source: Source) -> EditFinding:
    code = _SHEET_CODES.get(r["code"], r["code"])
    port, line = None, 0
    comp, term = r.get("component"), r.get("terminal")
    if comp in topology.folders and term:
        name = next((q.name for q in topology.folders[comp].ports if term in q.pads), None)
        if name is not None:
            port = f"{comp}.{name}"
            line = _line(topology, source, (comp, name))
        else:
            line = _line(topology, source, instance=comp)
    elif comp in topology.folders:
        line = _line(topology, source, instance=comp)
    elif comp and term:
        port = f"{comp}.{term}"
    words = _SHEET_WORDS.get(code)
    return EditFinding(code, line, port, f"{words} {r['message']}." if words else r["message"])


def _form_refused(refusals, text: str, path, blank: bool) -> EditRefused:
    """A form that does not load, refused for its own reasons whatever the file
    is. A classic project file is unplaced and does not plan, so where the file
    does not read as a sheet base the refusals land on the die's line, else 0."""
    try:
        source, topology = _read(text, path, sheet_base=blank)
    except EditRefused:
        source, _ = check_text(text, path)
        lines = {d.id: d.line for d in source.dies} if source is not None else {}
        out = []
        for r in refusals:
            code = _SHEET_CODES.get(r["code"], r["code"])
            comp, term = r.get("component"), r.get("terminal")
            words = _SHEET_WORDS.get(code)
            out.append(EditFinding(code, lines.get(comp, 0),
                                   r.get("port") or (f"{comp}.{term}" if comp and term else None),
                                   f"{words} {r['message']}." if words else r["message"]))
        return EditRefused(out)
    return EditRefused([_from_import(r, topology, source) for r in refusals])


def _given_dies(source: Source, topology: Plan):
    from ..schematic_import import GivenDie

    dies = {}
    for die in source.dies:
        folder = topology.folders[die.id]
        dies[die.id] = GivenDie(folder.top_cell, frozenset(p.name for p in folder.pins),
                                {pad: port.name for port in folder.ports for pad in port.pads})
    return dies


def schematic_edits(data: bytes, *, path: Union[str, Path], resolver: Resolver,
                    form: Union[str, Path], design: Optional[bytes] = None,
                    previous: Optional[Union[str, Path]] = None
                    ) -> Tuple[Edit, ...]:
    """The edits that write an edited system sheet into the source ``data``.

    ``path`` is where ``data`` was read from. ``form`` is the connectivity
    form's file. For a generated sheet its generator report and pin lists
    resolve from its folder, and ``design`` is the bytes of the run's
    ``design.chiplet``, the sheet's base: the form must name that digest, and
    ``data`` must elaborate to that design's nets; whether ``design`` is the
    run's own is the caller's check. A blank sheet is read against ``data``
    itself, elaborated as a sheet base, and takes no ``design``; a sheet of the
    other kind than ``design`` says is refused (``sheet_kind``). ``previous``
    is the form that produced the current source, passed on to a classic sheet.
    On a blank or generated sheet it is refused (``sheet_kind``).
    """
    text, _ = decode(data)
    from ..schematic_import import (SchematicImportRefused, load_form, read_blank_sheet,
                                    read_sheet)

    blank = design is None
    try:
        drawn = load_form(form)
    except SchematicImportRefused as exc:
        raise _form_refused(exc.refusals, text, path, blank) from exc
    if drawn.classic:
        if design is not None:
            raise EditRefused([EditFinding(
                "sheet_kind", 0, None,
                f"{Path(form).name} is a classic sheet, which is read into its project file, "
                "not against a run's design.chiplet.")])
        return classic_edits(data, path=path, form=form, resolver=resolver, previous=previous)
    if previous is not None:
        raise EditRefused([EditFinding(
            "sheet_kind", 0, None,
            f"{Path(form).name} is a {'blank' if drawn.blank else 'generated'} sheet; "
            "previous is only read for a classic sheet.")])
    drawn_blank = drawn.blank
    if drawn_blank != blank:
        raise EditRefused([EditFinding(
            "sheet_kind", 0, None,
            f"{Path(form).name} is a {'blank' if drawn_blank else 'generated'} sheet, which "
            + ("is read against the file it was drawn on, not a run's design.chiplet."
               if drawn_blank else "is read against the run's design.chiplet."))])
    source, topology = _read(text, path, sheet_base=blank)
    old_design = elaborate(build(source, topology, resolver))
    try:
        if blank:
            sheet = read_blank_sheet(form, Path(path).name,
                                     cfio.dumps(old_design).encode("utf-8"),
                                     _given_dies(source, topology))
        else:
            sheet = read_sheet(form, "design.chiplet", design)
    except SchematicImportRefused as exc:
        raise EditRefused([_from_import(r, topology, source) for r in exc.refusals]) from exc
    if not blank and _connectivity(old_design["netlist"]["nets"]) != \
            _connectivity(sheet.base_nets):
        raise EditRefused([EditFinding(
            "design_not_of_source", 0, None,
            "The run's design.chiplet does not have the nets this system.chiplet elaborates to; "
            "run the flow again on this file, then edit the sheet it draws.")])
    edits = _Lift(source, topology, sheet, blank=blank).run()
    if edits:
        _prove_sheet(patch_system(data, edits).text, path, resolver, old_design, sheet.nets)
    return edits


def _prove_sheet(data: bytes, path, resolver: Resolver, old: Mapping, nets) -> None:
    text, _ = decode(data)
    source, findings = check_text(text, path)
    topology = plan(source) if source is not None and not findings else None
    if topology is None or topology.refusals:
        found = findings or topology.refusals
        raise EditRefused([EditFinding("not_expressible", f.line, None,
                                       "The file cannot hold the sheet: " + f.message)
                           for f in found])
    got = elaborate(build(source, topology, resolver))
    outside = lambda d: {k: v for k, v in d.items() if k != "netlist"}  # noqa: E731
    if _connectivity(got["netlist"]["nets"]) != _connectivity(nets) or outside(got) != outside(old):
        raise SystemSpecError(f"{path}: the patched file does not elaborate to the sheet's nets; "
                              "this is a defect of the writer, nothing was written")
