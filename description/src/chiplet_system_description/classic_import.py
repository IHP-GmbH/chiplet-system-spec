# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Read a classic sheet into its project file: the system a person drew from blank.

A classic sheet (connectivity form 1.2 or 1.3) says what the system is, and nothing
geometric is drawn on it; no given file stands behind it. It is read into the
project file it belongs to, a source (format 1.3) that holds what a sheet does
not draw (the carrier's technology and top cell, the registry, the external
profile, the fill keep-out, the flow block) and, on a re-import, the dies the
last import wrote with whatever placement ``packaging-flow place`` gave them.
:func:`read_classic_sheet` checks the form against the die folders its symbols
name and returns the system it draws; :func:`classic_edits` turns that into the
:mod:`~chiplet_system_description.source.patch` edits that write it into the
project file, so ``packaging-flow edit FILE --sheet SHEET`` writes a classic
sheet the way it writes any other
(:func:`chiplet_system_description.source.edit.schematic_edits` hands a classic
form here).

What the sheet says:

- A die is a symbol whose ``fields["Die"]`` names a die folder, relative to the
  project file as a source's ``die:`` is. Its designator is the instance id
  (``unmappable_name`` when it is no ``[A-Za-z][A-Za-z0-9_]*``), its part the
  folder's top cell (``wrong_die``), its pins the numbers of the die contract's
  terminals (``terminal_set_changed``; ``contract_pads_differ`` when the contract
  and the inventory disagree on the pads or on the port a pad serves), and ``Method`` and
  ``Thickness_um`` (um, a plain
  decimal above 0) are filled in (``method_missing``, ``thickness_missing``;
  the first lists the methods the host's registry offers for the die's
  technology when the host can list them, see :class:`MethodCatalog`). A
  ``Die`` that is no relative path inside the project's folder, or names a
  folder that does not read, is ``foreign_part``; a symbol with no ``Die`` that
  is no ``EXT_PAD`` is ``unknown_symbol``. One folder may be placed under
  several designators.
- An ``EXT_PAD`` symbol (one pin, designator ``J1``, ``J2``, ...) is one
  carrier pad. Its ``Edge`` field puts its entry on that edge row of
  ``external``; empty, the entry waits in ``unplaced.pins`` for placement;
  anything but north, south, west or east is ``bad_edge``.
- A rail is a net named after one of the form's ``rails``: its die ports make
  the ``rails`` line, with the rail's class and volts, and its one ``EXT_PAD``
  the external entry naming the rail.
- Any other net joining two die pins on two dies is a link, and carries a name
  (``unnamed_net`` when KiCad named it); a port of several pads is wired lane
  by lane, its nets named ``<net>0``, ``<net>1``, ... A die pin and one
  ``EXT_PAD`` make the external entry of the pin's port, named or not. A label
  on one die pin only is ``lone_label``, three or more die pins ``multi_drop``,
  and an ``EXT_PAD`` on a net with no die pin, or with more than one,
  ``external_fanout``.
- A port is wired whole (``pad_split``). A die whose inventory has a pad that
  serves no port, or states no port, is ``unported_pads`` (put the pad on a
  port, or regenerate the die). Every other die pin is on a net or flagged
  no-connect, else one
  ``pin_not_wired`` per pin; a port whose every pin is flagged is an ``open``
  line, unless its pads share one name (``open_shared_pads``: connect it or
  tie it to a rail). A flag on no pin of its symbol is ``unknown_terminal``,
  as a net's endpoint is.
- Passed a previous form, the one that produced the current source, a symbol
  whose ``key`` that form has under another reference is ``reference_changed``
  (set the reference back). With no previous form the references are not
  compared.

What it writes. The ``dies``, ``links``, ``rails`` and ``open`` blocks, the
external edge rows and ``unplaced.pins`` are the sheet's; ``alone``, which a
classic sheet does not draw, goes. A die-to-die link is a links line, or a
buses line where the file already writes one, as B16's lift keeps it: a bus
stays while the sheet draws every lane of it under the bus's net names, and
its lanes are not written again as links; any other bus goes, its lanes
written as links. A block the file already
has as the sheet wants it is not touched, and any other is written whole, in
one order: dies by id, links by net name, rails by name (their ports by die,
then as the die's inventory names them), open lines likewise, external
entries by ``EXT_PAD`` designator, every name in natural order (``U2`` before
``U10``). A die keeps the ``at`` and ``rotation`` the file gives it while it
is drawn from the same folder. An entry with no ``Edge`` keeps the edge row
the file puts it on (where ``place`` put it) while its die keeps its place, a
rail's always, and holds its position there; entries new to a row follow.
Every other byte of the file is its own, and the stamp is the lowest minor the
result needs. The same sheet read again writes nothing.

The result must pass tier 1 read as unplaced (``plan(source, unplaced=True)``,
notes allowed): a planner refusal refuses the sheet as ``not_expressible`` on
the patched file's line, and the patched file's nets must be the sheet's, or
the writer is at fault and nothing is returned.

Refusals are collected by stage: the front end's own (its
``duplicate_reference`` named again here, once per shared designator), the
symbols, then the nets and the wiring of every pin; each is a record
``{code, message, component?, terminal?, net?, port?, methods?, was?}``, ``port``
naming the die port or the ``EXT_PAD`` it is about, and ``methods`` (on
``method_missing``) the method ids the host listed. Tests assert codes, never
messages.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import (Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Protocol, Sequence,
                    Set, Tuple, Union, runtime_checkable)

from .connectivity import (
    CLASSIC,
    EXT_PAD,
    GROUND,
    ConnectivityForm,
    SchematicImportRefused,
    Symbol,
    is_name,
    load_form,
    refusal,
)
from .errors import SystemSpecError
from .model import PortRef
from .source.elaborate import Resolver
from .source.document import (EDGES, INSTANCE_ID, Source, check_relative_path, check_text,
                              entry_text, ref_text)
from .source.inventory import (
    CONTRACT, DieFolder, InventoryError, InventoryPort, UnportedPad, read_die_folder)
from .source.patch import (
    Composed,
    Delete,
    Edit,
    EditFinding,
    EditRefused,
    Insert,
    SetEntries,
    decode,
    identity,
    item_line,
    not_a_source,
    patch_system,
)
from .source.lanes import read_lanes
from ._vendor.chiplet_format_io.die_contract import (
    BoundContract, DieContractError, check_terminals, inventory_identities)
from ._vendor.chiplet_format_io.pad_numbers import pad_numbers
from ._vendor.chiplet_format_io.sheet_names import SHEET_NAME_WORDS, is_sheet_name
from .source.plan import PlanRefused, natural, planned_nets, tie_net
from .source.rail_requirement import RailRequirement

Terminal = Tuple[str, str]
Port = Tuple[str, str]  # (instance, port name)
#: A thickness as written: a plain decimal of um, checked above 0 apart.
_THICKNESS = re.compile(r"[0-9]+(?:\.[0-9]+)?")
#: The blocks of items a classic sheet writes whole, in file order; ``alone``
#: is the sheet's to empty. ``buses`` is kept or emptied bus by bus
#: (:func:`_kept_buses`).
_BLOCKS = ("dies", "links", "rails", "alone", "open")


@runtime_checkable
class MethodCatalog(Protocol):
    """A host that lists its interconnect methods. A
    :class:`~.source.elaborate.Resolver` that is one has ``method_missing``
    name the methods a die can take; one that is not leaves the list out."""

    def methods(self, technology: str, registry: Optional[Path]) -> Sequence[str]:
        """The ids of the methods ``registry`` (absolute, already held to the
        path rule, or the host's own when None) offers for dies of
        ``technology``."""


@dataclass(frozen=True)
class ClassicDie:
    id: str
    die: str                # the Die field, a folder relative to the project file
    folder: DieFolder
    method: str
    thickness: Decimal      # um


@dataclass(frozen=True)
class ClassicLink:
    a: PortRef              # the driving end, when one drives
    b: PortRef
    net: str                # one lane: its net; several: lanes <net>0, <net>1, ...


@dataclass(frozen=True)
class ClassicRail:
    name: str
    net_class: str
    volts: Optional[Decimal]
    ports: Tuple[PortRef, ...]


@dataclass(frozen=True)
class ExternalPad:
    ref: str                # the EXT_PAD's designator
    entry: str              # a rail name or a port ref
    edge: Optional[str]     # None: left to placement


@dataclass(frozen=True)
class ClassicSheet:
    """The system a classic sheet draws, each part in the order it is written."""

    dies: Tuple[ClassicDie, ...]
    links: Tuple[ClassicLink, ...]
    rails: Tuple[ClassicRail, ...]
    open: Tuple[PortRef, ...]
    pads: Tuple[ExternalPad, ...]
    #: Every net the system elaborates, named as the planner names it, with
    #: its die terminals: what the written file must give back.
    nets: Mapping[str, FrozenSet[Terminal]]


@dataclass(frozen=True)
class _Part:
    """The item a die terminal's net puts it in: two terminals are wired
    alike when their parts are equal."""

    kind: str       # "rail", "external" or "link"
    of: Any         # the rail's or the net's name; a link's pair of ports


def _natural_pair(item: Tuple[str, Any]):
    return natural(item[0])


class PadsDiffer(Exception):
    """The contract's terminals and the inventory do not name the same pads,
    or do not give a pad the same port."""


def _bound(folder: DieFolder) -> BoundContract:
    """The folder's contract, bound to the inventory document already read.

    Schema admission is not called. ``bind`` would check digests a source
    does not. The document is the one :func:`read_die_folder` parsed.
    """
    path = folder.folder / CONTRACT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DieContractError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DieContractError(f"{path}: not a die contract")
    try:
        return BoundContract(data, path, folder.inventory_document, folder.inventory)
    except (KeyError, TypeError) as exc:
        raise DieContractError(f"{path}: not a die contract") from exc


def _agree(folder: DieFolder) -> None:
    """The contract and the inventory name the same pads and the same ports.

    :func:`pad_numbers` runs first, so a contract with no port identity keeps
    that refusal. The pad sets are compared next, and a difference names the
    first pairs. :func:`check_terminals` still runs, and whatever else it
    raises keeps its own code.
    """
    bound = _bound(folder)
    numbered = pad_numbers(bound)
    identities = inventory_identities(bound.inventory, bound.kind)
    contract = [(pin.name, pin.pad_index) for pin in numbered]
    held = list(identities)
    if set(contract) != set(held):
        missing = [("contract", *pair) for pair in contract if pair not in identities]
        missing += [("inventory", *pair) for pair in held if pair not in set(contract)]
        shown = ", ".join(f"{side} ({name}, {index})" for side, name, index in missing[:3])
        raise PadsDiffer(
            f"{bound.path}: the contract and the inventory do not name the same pads ({shown})")
    check_terminals(bound, require_equality=True)
    for pin in numbered:
        record = identities[(pin.name, pin.pad_index)]
        if (pin.port, pin.ordinal) != (record.get("port"), record.get("ordinal")):
            raise PadsDiffer(
                f"{bound.path}: the contract and the inventory disagree on which "
                "port a pad serves")


def _field(fields: Tuple[Tuple[str, str], ...], name: str) -> Optional[str]:
    """The text of one symbol field, or None when it is absent or empty.

    A classic symbol always carries the field map; an empty string is empty
    and ``~`` is not.
    """
    value = dict(fields).get(name)
    if isinstance(value, str) and value:
        return value
    return None


class _Reader:
    def __init__(self, form: ConnectivityForm, folder: Path, given: str,
                 registry: Optional[str], methods: Optional[Callable[[str], Sequence[str]]],
                 previous: Optional[ConnectivityForm] = None):
        self.form, self.folder, self.given, self.registry = form, folder, given, registry
        #: key -> the reference the previous form had it under. Empty when no
        #: previous form was given, which is what makes the check a no-op.
        self.was = {s.key: s.ref for s in previous.symbols if s.key} if previous else {}
        self.methods = methods
        self.offered: Dict[str, Tuple[str, ...]] = {}   # technology: its methods
        self.library = form.library.name
        self.system = form.system_library.name
        # The import names the symbols that share a designator, with their parts.
        self.out: List[Dict[str, Any]] = [dict(r) for r in form.refusals
                                          if r.get("code") != "duplicate_reference"]
        self.dies: Dict[str, DieFolder] = {}
        self.die_path: Dict[str, str] = {}    # ref: the Die field
        self.fields: Dict[str, Tuple[str, Decimal]] = {}    # ref: (method, thickness)
        self.pads: Dict[str, Tuple[str, Optional[str]]] = {}    # ref: (pin, edge)
        self.cache: Dict[Path, Union[DieFolder, UnportedPad, str]] = {}
        #: Each die terminal's part in the system, and the terminals on a
        #: refused net, which the wiring check leaves to that refusal.
        self.part: Dict[Terminal, _Part] = {}
        self.dropped: Set[Terminal] = set()

    def refuse(self, code: str, message: str, **where: str) -> None:
        self.out.append(refusal(code, message, **where))

    # --- names --------------------------------------------------------------
    def port(self, term: Terminal) -> Port:
        for item in self.dies[term[0]].ports:
            if term[1] in item.pads:
                return term[0], item.name
        raise KeyError(term[1])

    def covers(self, ref: str, pin: str) -> Optional[Tuple[str, ...]]:
        found = tuple(pad for item in self.dies[ref].ports for pad in item.pads if pad == pin)
        return found or None

    def word(self, term: Terminal) -> str:
        return "{}.{}".format(*self.port(term))

    def rank(self, port: Port):
        names = [p.name for p in self.dies[port[0]].ports]
        return natural(port[0]), names.index(port[1])

    def spec(self, port: Port) -> InventoryPort:
        return self.dies[port[0]].port(port[1])

    # --- the symbols ----------------------------------------------------------
    def reference(self, sym: Symbol) -> None:
        old = self.was.get(sym.key) if sym.key is not None else None
        if old is not None and old != sym.ref:
            self.refuse("reference_changed",
                        f"{sym.ref} was {old}; set the reference back",
                        component=sym.ref, was=old)

    def symbols(self) -> None:
        count = Counter(sym.ref for sym in self.form.symbols)
        told = set()
        for sym in self.form.symbols:
            self.reference(sym)
            pad = sym.part.name == EXT_PAD
            if sym.ref.endswith("?"):
                self.refuse("not_annotated",
                            f"a {sym.part.name} symbol is still named {sym.ref}; annotate it "
                            + ("J1, J2, ..." if pad else "with its instance id, as U1"))
                continue
            if count[sym.ref] > 1:
                if sym.ref not in told:
                    told.add(sym.ref)
                    parts = list(dict.fromkeys(s.part.name for s in self.form.symbols
                                               if s.ref == sym.ref))
                    self.refuse("duplicate_reference",
                                f"{count[sym.ref]} symbols are named {sym.ref} "
                                f"({', '.join(parts)}); give each its own designator",
                                component=sym.ref)
                continue
            if pad:
                self.pad(sym)
            else:
                self.die(sym)

    def pad(self, sym: Symbol) -> None:
        if len(sym.pins) != 1:
            self.refuse("terminal_set_changed",
                        f"{sym.ref} is an {EXT_PAD} with pins {list(sym.pins)}; an {EXT_PAD} has "
                        f"one pin, so place it again from {self.system}",
                        component=sym.ref, port=sym.ref)
            return
        edge = _field(sym.fields, "Edge")
        if edge is not None and edge not in EDGES:
            self.refuse("bad_edge",
                        f"{sym.ref} has Edge {edge!r}; write north, south, west or east, or "
                        "leave it empty for packaging-flow place to choose",
                        component=sym.ref, port=sym.ref)
            edge = None
        self.pads[sym.ref] = (sym.pins[0], edge)

    def read(self, die: str) -> Union[DieFolder, UnportedPad, str]:
        path = self.folder / die
        if path not in self.cache:
            try:
                self.cache[path] = read_die_folder(path)
            except UnportedPad as exc:
                self.cache[path] = exc
            except InventoryError as exc:
                self.cache[path] = str(exc)
        return self.cache[path]

    def die(self, sym: Symbol) -> None:
        ref, part, fields = sym.ref, sym.part, sym.fields
        die = _field(fields, "Die")
        again = f"place it again from {self.library}"
        if die is None:
            self.refuse("unknown_symbol",
                        f"{ref} is a {part.lib}:{part.name} symbol, which is neither a die from "
                        f"{self.library} (it has no Die field) nor an {EXT_PAD} from "
                        f"{self.system}; remove it, or {again}", component=ref)
            return
        if not INSTANCE_ID.match(ref):
            self.refuse("unmappable_name",
                        f"designator {ref} is no instance id [A-Za-z][A-Za-z0-9_]*; name the "
                        "die as U1", component=ref)
            return
        why = check_relative_path(die)
        folder = self.read(die) if why is None else why
        if isinstance(folder, UnportedPad):
            self.refuse(
                "unported_pads",
                f"{folder}; put the pad on a port in the die's inventory, or regenerate the die",
                component=ref)
            return
        if isinstance(folder, str):
            self.refuse("foreign_part",
                        f"{ref}'s Die field {die!r} names no die folder of {self.given} "
                        f"({folder}); {again}", component=ref)
            return
        if folder.top_cell != part.name:
            self.refuse("wrong_die",
                        f"{ref} is a {part.name} symbol, but its Die field names {die}, "
                        f"whose top cell is {folder.top_cell}; place the {folder.top_cell} "
                        f"symbol from {self.library} instead", component=ref)
            return
        try:
            _agree(folder)
        except PadsDiffer as exc:
            self.refuse("contract_pads_differ", str(exc), component=ref)
            return
        except DieContractError as exc:
            self.refuse("not_expressible", str(exc), component=ref)
            return
        have, want = set(sym.pins), {pad for item in folder.ports for pad in item.pads}
        if have != want:
            self.refuse("terminal_set_changed",
                        f"{ref} has pins added {sorted(have - want)} and missing "
                        f"{sorted(want - have)} against {folder.top_cell}; {again}",
                        component=ref)
            return
        self.dies[ref] = folder
        self.die_path[ref] = die
        method, thickness = _field(fields, "Method"), _field(fields, "Thickness_um")
        if method is None:
            self.method_missing(ref, folder)
        if thickness is None:
            self.refuse("thickness_missing",
                        f"{ref} ({folder.top_cell}) has no Thickness_um; fill it with the die's "
                        "thickness in um, as 750", component=ref)
        elif not _THICKNESS.fullmatch(thickness) or Decimal(thickness) <= 0:
            self.refuse("thickness_missing",
                        f"{ref}'s Thickness_um is {thickness!r}; write the die's thickness in "
                        "um as a plain number above 0, as 750", component=ref)
            thickness = None
        if method is not None and thickness is not None:
            self.fields[ref] = (method, Decimal(thickness))

    def method_missing(self, ref: str, folder: DieFolder) -> None:
        tech = folder.technology
        where = f" (system.registry, {self.registry})" if self.registry else ""
        message = (f"{ref} ({folder.top_cell}, {tech}) has no Method; fill its Method field "
                   f"with an interconnect method the registry{where} offers for {tech}")
        if self.methods is None:
            self.refuse("method_missing", message, component=ref)
            return
        if tech not in self.offered:
            self.offered[tech] = tuple(self.methods(tech))
        offered = self.offered[tech]
        self.refuse("method_missing",
                    message + (f": {', '.join(offered)}" if offered else ", and it offers none"),
                    component=ref, methods=list(offered))

    # --- the nets --------------------------------------------------------------
    def nets(self):
        """Each named net as (net, die terminals, EXT_PAD designators), with
        every endpoint checked; an unconnected pin's own net is no net."""
        refused = {sym.ref for sym in self.form.symbols} - set(self.dies) - set(self.pads)
        owner: Dict[Terminal, str] = {}
        read = []
        for net in self.form.nets:
            if net.auto_named and len(set(net.endpoints)) == 1:
                continue
            terms, pads = set(), set()
            touched = False
            for ref, pin in net.endpoints:
                if ref in refused:
                    touched = True  # its symbol is refused already, so the net is too
                    continue
                covered = self.covers(ref, pin) if ref in self.dies else None
                if covered is not None:
                    terms.update((ref, pad) for pad in covered)
                elif ref in self.pads and pin == self.pads[ref][0]:
                    pads.add(ref)
                elif ref in self.dies or ref in self.pads:
                    self.refuse("unknown_terminal",
                                f"net {net.name} connects {ref}.{pin}, and {ref} has no pin {pin}",
                                net=net.name, component=ref, terminal=pin)
                else:
                    self.refuse("unknown_ref", f"net {net.name} reaches {ref}, which is no symbol",
                                net=net.name, component=ref)
            for term in sorted(terms | {(r, self.pads[r][0]) for r in pads}):
                if term in owner:
                    what = self.word(term) if term[0] in self.dies else term[0]
                    self.refuse("terminal_on_two_nets",
                                f"{what} is on net {owner[term]} and on net {net.name}",
                                net=net.name, component=term[0], terminal=term[1],
                                port=what)
                else:
                    owner[term] = net.name
            read.append((net, frozenset(terms), frozenset(pads), touched))
        return read

    def at(self, term: Terminal) -> Dict[str, str]:
        return {"component": term[0], "terminal": term[1], "port": self.word(term)}

    def classify(self, read):
        """Rails, external ports and link lanes, by net; every other net refused."""
        rails = {r.name for r in self.form.rails}
        rail_nets: Dict[str, Tuple[FrozenSet[Terminal], FrozenSet[str]]] = {}
        externals: List[Tuple[Port, str, str]] = []      # (port, pad, net)
        lanes: Dict[FrozenSet[Port], List[Tuple[str, FrozenSet[Terminal]]]] = {}
        fanned = set()
        for net, terms, pads, touched in read:
            ports = {self.port(t) for t in terms}
            ends = ", ".join(sorted(self.word(t) for t in terms)) or "no die pin"

            if touched:
                self.dropped |= terms
                fanned.update(pads)
                continue
            if net.name in rails:
                rail_nets[net.name] = (terms, pads)
                self.part.update(dict.fromkeys(terms, _Part("rail", net.name)))
                continue
            if pads:
                if len(pads) == 1 and len(ports) == 1:
                    (port,), (pad,) = ports, pads
                    externals.append((port, pad, net.name))
                    self.part.update(dict.fromkeys(terms, _Part("external", net.name)))
                    continue
                self.dropped |= terms
                for pad in sorted(pads, key=natural):
                    fanned.add(pad)
                    self.refuse("external_fanout",
                                f"{pad} is on net {net.name} with {ends}; an {EXT_PAD} brings out "
                                "one die pin, so wire it to one, or put the net on a rail "
                                "symbol", net=net.name, component=pad, port=pad)
                continue
            if not terms:
                continue
            first = sorted(terms)[0]
            if net.auto_named:
                self.dropped |= terms
                self.refuse("unnamed_net",
                            f"the wire joining {ends} has no label; put the link's net name on it",
                            net=net.name, **self.at(first))
            elif not is_sheet_name(net.name):
                self.dropped |= terms
                self.refuse("unmappable_name",
                            f"net name {net.name!r} is not a {SHEET_NAME_WORDS}",
                            net=net.name, **self.at(first))
            elif len(terms) >= 3:
                self.dropped |= terms
                self.refuse("multi_drop",
                            f"net {net.name} joins {ends}; a link joins two die pins, so a net "
                            "over more is a rail, drawn with a GND or SUPPLY symbol",
                            net=net.name, **self.at(first))
            elif len(terms) == 2 and len(ports) == 2 and len({p[0] for p in ports}) == 2:
                lanes.setdefault(frozenset(ports), []).append((net.name, terms))
                self.part.update(dict.fromkeys(terms, _Part("link", frozenset(ports))))
            elif len(terms) == 2:
                self.dropped |= terms
                self.refuse("not_expressible",
                            f"net {net.name} joins {ends}, on one die; a link joins two dies",
                            net=net.name, **self.at(first))
            else:
                self.dropped |= terms
                self.refuse("lone_label",
                            f"label {net.name} reaches only {ends}; check its spelling at the "
                            "other end", net=net.name, **self.at(first))
        for pad in sorted(self.pads, key=natural):
            used = (pad in fanned or any(pad in p for _, p in rail_nets.values())
                    or any(pad == x for _, x, _ in externals))
            if not used:
                self.refuse("external_fanout",
                            f"{pad} is wired to no die pin; wire it to the one pin it brings out, "
                            "or remove it", component=pad, port=pad)
        return rail_nets, externals, lanes

    def rails(self, rail_nets) -> Tuple[List[ClassicRail], List[Tuple[str, str]]]:
        out, pads = [], []
        for rail in sorted(self.form.rails, key=lambda r: natural(r.name)):
            if not is_name(rail.name) or "." in rail.name:
                self.refuse("unmappable_name",
                            f"rail name {rail.name!r} is not a plain [A-Za-z0-9_+-] name; rename "
                            "its symbol's Value", net=rail.name)
                continue
            if rail.name not in rail_nets:
                continue        # no net carries it: the front end's rail_unused
            terms, on = rail_nets[rail.name]
            ports = sorted({self.port(t) for t in terms}, key=self.rank)
            if not ports:
                self.refuse("not_expressible",
                            f"rail {rail.name} reaches no die pin; put the {rail.net_class} pins "
                            "it supplies on it, or remove it", net=rail.name)
            if len(on) > 1:
                self.refuse("not_expressible",
                            f"rail {rail.name} reaches {', '.join(sorted(on, key=natural))}; a "
                            f"rail leaves the carrier through one {EXT_PAD}, so keep one",
                            net=rail.name)
            volts = None if rail.volts is None or rail.net_class == GROUND else Decimal(rail.volts)
            out.append(ClassicRail(rail.name, rail.net_class, volts,
                                   tuple(PortRef(i, p) for i, p in ports)))
            pads.extend((pad, rail.name) for pad in on)
        return out, pads

    def links(self, lanes) -> List[ClassicLink]:
        """One link per pair of ports, read as B16's lift reads it
        (:func:`~.source.lanes.read_lanes`), the driving end first."""
        found, findings = read_lanes(
            lanes, lambda p: self.spec(p).pads,
            lambda p: (self.spec(p).direction != "OUTPUT", self.rank(p)))
        for f in findings:
            self.refuse(f.code, f.message, component=f.port[0], port="{}.{}".format(*f.port))
        return sorted((ClassicLink(PortRef(*a), PortRef(*b), net) for a, b, net in found.values()),
                      key=lambda link: natural(link.net))

    def wiring(self) -> List[PortRef]:
        """Every port wired whole or flagged open, every pin on a net or
        flagged; the open ports."""
        flagged = set()
        for ref, pin in self.form.open:
            covered = self.covers(ref, pin) if ref in self.dies else None
            if covered is not None:
                flagged.update((ref, pad) for pad in covered)
            elif ref in self.dies or (ref in self.pads and pin != self.pads[ref][0]):
                self.refuse("unknown_terminal",
                            f"a no-connect flag sits on {ref}.{pin}, and {ref} has no pin {pin}",
                            component=ref, terminal=pin)
            elif ref not in self.pads and ref not in {s.ref for s in self.form.symbols}:
                self.refuse("unknown_ref", f"a no-connect flag sits on {ref}, which is no symbol",
                            component=ref)
        opened = []
        for ref in sorted(self.dies, key=natural):
            for port in self.dies[ref].ports:
                terms = [(ref, pad) for pad in port.pads]
                if self.dropped.intersection(terms):
                    continue        # refused with its net
                states = [self.part.get(t) for t in terms]
                both = [t for t, s in zip(terms, states) if s is not None and t in flagged]
                for t in both:
                    self.refuse("not_expressible",
                                f"{self.word(t)} (pin {t[1]}) is flagged no-connect and wired; "
                                "remove the flag or the wire", **self.at(t))
                kinds = {s for s in states if s is not None}
                if both:
                    continue
                if len(kinds) > 1 or (kinds and any(t in flagged for t in terms)):
                    self.refuse("pad_split",
                                f"the pins of {ref}.{port.name} are wired apart; a port is wired "
                                "whole, every pin to the same net or every pin flagged",
                                component=ref, port=f"{ref}.{port.name}")
                    continue
                if all(t in flagged for t in terms):
                    nums = port.pads
                    if len(set(nums)) != len(nums):
                        self.refuse(
                            "open_shared_pads",
                            f"{ref}.{port.name} is left open and its pads share one name; "
                            "connect it or tie it to a rail",
                            component=ref, port=f"{ref}.{port.name}")
                        continue
                    opened.append(PortRef(ref, port.name))
                    continue
                for t, s in zip(terms, states):
                    if s is None and t not in flagged:
                        self.refuse("pin_not_wired",
                                    f"{self.word(t)} (pin {t[1]}) is not wired; wire it, or flag "
                                    "it no-connect if it stays open", **self.at(t))
        return opened

    # --- the system ------------------------------------------------------------
    def sheet(self) -> ClassicSheet:
        self.symbols()
        read = self.nets()
        rail_nets, externals, lanes = self.classify(read)
        rails, rail_pads = self.rails(rail_nets)
        links = self.links(lanes)
        opened = self.wiring()
        if self.out:
            raise SchematicImportRefused(self.out)
        pads = [(pad, "{}.{}".format(*port)) for port, pad, _ in externals] + rail_pads
        nets: Dict[str, FrozenSet[Terminal]] = {}
        for _, names in lanes.items():
            nets.update(names)
        for rail in rails:
            nets[rail.name] = frozenset((r.instance, pad) for r in rail.ports
                                        for pad in self.spec((r.instance, r.port)).pads)
        for port, _, _ in externals:
            nets.update({tie_net(port[0], pad): frozenset({(port[0], pad)})
                         for pad in self.spec(port).pads})
        dies = tuple(ClassicDie(ref, self.die_path[ref], self.dies[ref], *self.fields[ref])
                     for ref in sorted(self.dies, key=natural))
        return ClassicSheet(
            dies=dies, links=tuple(links), rails=tuple(rails), open=tuple(opened),
            pads=tuple(ExternalPad(pad, entry, self.pads[pad][1])
                       for pad, entry in sorted(pads, key=_natural_pair)),
            nets=nets)


def read_classic_sheet(form_path, folder, given: str, *, registry: Optional[str] = None,
                       methods: Optional[Callable[[str], Sequence[str]]] = None,
                       previous: Optional[Union[str, Path]] = None) -> ClassicSheet:
    """The system the classic sheet in ``form_path`` (form 1.2) draws, its die
    folders read from ``folder`` (the project file's), or
    :class:`SchematicImportRefused` with every reason it cannot be read.
    ``given`` names the project file in the messages, and ``registry`` (its
    ``system.registry``, when it states one) is named where a method is
    missing, with the ids ``methods`` gives for the die's technology when it
    is given. ``previous``, when given, is the form that produced the current
    source: a symbol whose key it holds under another reference is
    ``reference_changed``. Nothing is written."""
    prior = load_form(previous, CLASSIC) if previous is not None else None
    return _Reader(load_form(form_path, CLASSIC), Path(folder), given, registry,
                   methods, prior).sheet()


# ----------------------------------------------------------------------
# The edits
# ----------------------------------------------------------------------
def _kept_buses(source: Source, sheet: ClassicSheet) -> Tuple[set, set]:
    """The lines of the file's buses the sheet still draws lane for lane under
    their net names, and the port pairs those lanes are."""
    drawn = {frozenset({ref_text(link.a), ref_text(link.b)}): link.net for link in sheet.links}
    lines, pairs = set(), set()
    for bus in source.buses:
        lanes = {frozenset({f"{bus.ends[0]}.{p}", f"{bus.ends[1]}.{p}"}):
                 f"{bus.net_prefix}{p}{bus.net_suffix}" for p in bus.ports}
        if all(drawn.get(k) == net for k, net in lanes.items()) and not set(lanes) & pairs:
            lines.add(bus.line)
            pairs.update(lanes)
    return lines, pairs


def _wanted(source: Source, sheet: ClassicSheet, bused: set) -> Tuple[Dict[str, list], set]:
    """The items each block should hold, as :class:`~.source.patch.Insert`
    takes them, and the dies that keep their placement. The links in
    ``bused`` are a kept bus's lanes. A die the sheet still draws from the same
    folder keeps its admission, placed or not, and a rail the sheet still names
    keeps its routing requirement: the sheet shows neither. A rail renamed on the
    sheet is another rail to the file, so its requirement is not carried."""
    kept = set()
    items: Dict[str, list] = {block: [] for block in _BLOCKS}
    have = {d.id: d for d in source.dies}
    # a rotation the file leaves out stays out
    turned = {d.get("id") for d in (source.data.get("system") or {}).get("dies") or ()
              if isinstance(d, Mapping) and "rotation" in d}
    for die in sheet.dies:
        was = have.get(die.id)
        same = (was is not None
                and (source.folder / was.die).resolve() == (source.folder / die.die).resolve())
        held = same and was.at is not None
        if held:
            kept.add(die.id)
        items["dies"].append((("id", die.id), ("die", die.die),
                              ("at", was.at if held else None),
                              ("rotation", was.rotation if held and die.id in turned else None),
                              ("method", die.method), ("thickness", die.thickness),
                              ("admission", was.admission if same else None)))
    items["links"] = [(("a", ref_text(link.a)), ("b", ref_text(link.b)), ("net", link.net))
                      for link in sheet.links
                      if frozenset({ref_text(link.a), ref_text(link.b)}) not in bused]
    stated = {rail.name: rail.requirement for rail in source.rails if rail.requirement}
    items["rails"] = [(("name", rail.name), ("class", rail.net_class), ("volts", rail.volts),
                       *stated.get(rail.name, RailRequirement())._asdict().items(),
                       ("ports", [ref_text(r) for r in rail.ports])) for rail in sheet.rails]
    items["open"] = [ref_text(r) for r in sheet.open]
    return items, kept


def _rows(source: Source, sheet: ClassicSheet, kept: set) -> List[Edit]:
    """The external rows the sheet's pads ask for, against the file's."""
    external = source.external
    have = {edge.edge: [entry_text(e) for e in edge.entries]
            for edge in (external.edges if external else ())}
    waiting = [entry_text(e) for e in source.unplaced.pins] if source.unplaced else []
    held_on = {entry: edge for edge, entries in have.items() for entry in entries}
    rows: Dict[str, List[str]] = {edge: [] for edge in EDGES}
    unplaced: List[str] = []
    for pad in sheet.pads:
        edge = pad.edge
        if edge is None and pad.entry in held_on and (
                "." not in pad.entry or pad.entry.split(".", 1)[0] in kept):
            edge = held_on[pad.entry]
        (rows[edge] if edge else unplaced).append(pad.entry)
    edits: List[Edit] = []
    for edge in EDGES:
        was = have.get(edge, [])
        now = [e for e in was if e in rows[edge]] + [e for e in rows[edge] if e not in was]
        if now != was:
            edits.append(SetEntries(edge, tuple(now)))
    if unplaced != waiting:
        edits.append(SetEntries("unplaced", tuple(unplaced)))
    return edits


def _edits(source: Source, sheet: ClassicSheet) -> Tuple[Edit, ...]:
    if sheet.pads and source.external is None:
        raise EditRefused([EditFinding(
            "not_expressible", 0, None,
            f"The sheet draws {len(sheet.pads)} {EXT_PAD}(s) and the project file has no external "
            "block to hold them; add one with its profile (external: {profile: ...}).")])
    doc = Composed(source.text)
    buses, bused = _kept_buses(source, sheet)
    items, kept = _wanted(source, sheet, bused)
    edits: List[Edit] = []
    for block in _BLOCKS:
        nodes = doc.keys[block][1].value if block in doc.keys else []
        if block == "links":    # buses come between dies and links in the file
            edits.extend(Delete("buses", identity("buses", n))
                         for n in (doc.keys["buses"][1].value if "buses" in doc.keys else ())
                         if n.start_mark.line + 1 not in buses)
        written = [source.text[n.start_mark.index:n.end_mark.index] for n in nodes]
        if written == [item_line(block, item) for item in items[block]]:
            continue
        edits.extend(Delete(block, identity(block, n)) for n in nodes)
        edits.extend(Insert(block, item) for item in items[block])
    return tuple(edits) + tuple(_rows(source, sheet, kept))


def _prove(text: str, path, sheet: ClassicSheet) -> None:
    """The written file passes tier 1 read as unplaced, and its nets are the
    sheet's."""
    source, findings = check_text(text, path)
    if source is None or findings:
        raise EditRefused([EditFinding("not_expressible", f.line, None,
                                       "The file cannot hold the sheet: " + f.message)
                           for f in findings])
    try:
        _topology, planned = planned_nets(source, unplaced=True)
    except PlanRefused as exc:
        raise EditRefused([EditFinding("not_expressible", f.line, None,
                                       "The file cannot hold the sheet: " + f.message)
                           for f in exc.findings]) from exc
    nets = {net.name: net.endpoints for net in planned}
    if nets != dict(sheet.nets):
        raise SystemSpecError(f"{path}: the written file does not give the sheet's nets; this "
                              "is a defect of the writer, nothing was written")


def _finding(r: Mapping[str, Any], source: Source) -> EditFinding:
    named = r.get("was") or r.get("component")
    line = next((d.line for d in source.dies if d.id == named), 0)
    return EditFinding(r["code"], line, r.get("port"), r["message"])


def classic_edits(data: bytes, *, path: Union[str, Path], form: Union[str, Path],
                  resolver: Optional[Resolver] = None,
                  previous: Optional[Union[str, Path]] = None) -> Tuple[Edit, ...]:
    """The edits that write the classic sheet whose connectivity form is
    ``form`` into the project file ``data``, read from ``path`` (its die
    folders resolve from there), or :class:`EditRefused` with one finding per
    reason, each on the die's line where the file has the die, else 0. A
    ``resolver`` that is a :class:`MethodCatalog` lists the methods a die can
    take where its Method is missing; nothing else is read from it.
    ``previous`` is the form that produced the current source, as
    :func:`read_classic_sheet` takes it."""
    text, _ = decode(data)
    source, findings = check_text(text, path)
    if findings or source is None:
        raise not_a_source(findings)
    methods = None
    if isinstance(resolver, MethodCatalog):
        registry = source.folder / source.registry if source.registry else None
        methods = lambda technology: resolver.methods(technology, registry)  # noqa: E731
    try:
        sheet = read_classic_sheet(form, source.folder, Path(path).name,
                                   registry=source.registry, methods=methods,
                                   previous=previous)
    except SchematicImportRefused as exc:
        raise EditRefused([_finding(r, source) for r in exc.refusals]) from exc
    edits = _edits(source, sheet)
    written = text
    if edits:
        written, _ = decode(patch_system(data, edits, name=Path(path).name).text)
    _prove(written, path, sheet)
    return edits


__all__ = [
    "EXT_PAD",
    "ClassicDie",
    "ClassicLink",
    "ClassicRail",
    "ClassicSheet",
    "ExternalPad",
    "MethodCatalog",
    "classic_edits",
    "read_classic_sheet",
]
