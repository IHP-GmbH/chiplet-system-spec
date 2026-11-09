# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Read an edited system schematic against the design it was drawn from.

The tool-neutral back end of a sheet edit. A front end (KiCad's, Xschem's) turns
its own netlist into the connectivity form (:mod:`chiplet_system_description.connectivity`);
:func:`read_sheet` checks the form against the identities the generator wrote
and the base ``.chiplet``, and returns the sheet's nets. It writes nothing:
``packaging-flow edit <run> --sheet <form>`` lifts those nets to edits of the
run's ``system.chiplet`` (:func:`chiplet_system_description.source.edit.schematic_edits`),
whose one writer is :func:`chiplet_system_description.source.patch.patch_system`.

What it reads is connectivity only: which terminals share a named net. Only die
terminals are edited. A terminal of any other component the generator drew (the
carrier's pads) is fixed by the base, through the netlist where it is connected
there and through ``io_pads[].net`` otherwise: the sheet must show it on that
net, or the sheet is refused. A net bound to an io pad, marked external, or
reaching a terminal the sheet cannot edit is held whole.

A kept or renamed net keeps its position and every key it had; the connections
that stay keep their order, and arriving ones follow, sorted. A moved terminal
brings its own base connection record, ``layer`` included; a newly connected
terminal takes the one layer its component uses in the base. New nets are
appended in name order with no ``class`` key (the format's default, signal).

Refusals are collected by stage, with the front end's own in the first. A
malformed form stops everything. The inputs (base, generator report, digests)
are checked together, then the symbols and nets when the report could be read,
and the diff (held nets and terminals, layers) only when nothing came before it.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import yaml

from ._cfio import cfio
from ._vendor.chiplet_format_io.sheet_names import SHEET_NAME_WORDS, is_sheet_name
from .classic_import import read_classic_sheet
from .connectivity import (
    BLANK,
    GENERATED,
    ConnectivityForm,
    SchematicImportRefused,
    load_form,
    refusal,
)

Terminal = Tuple[str, str]


# ---------------------------------------------------------------- inputs

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve(path_text: str, here: Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else here / path



@dataclass
class _Base:
    path: Path
    raw: bytes
    sha: str
    doc: Dict[str, Any]
    nets: List[Dict[str, Any]]     # netlist.nets rows, as loaded
    bound: FrozenSet[str]          # nets named by an io_pad or marked external
    kinds: Dict[str, Any]          # component id: its type
    pads: Dict[Terminal, str]      # (component, io pad id): the pad's net


def _read_base(path: Path, out: List[dict], raw: Optional[bytes] = None) -> Optional[_Base]:
    def unreadable(why):
        out.append(refusal("base_unreadable", f"base {path}: {why}"))

    try:
        raw = path.read_bytes() if raw is None else raw
        text = raw.decode("utf-8")
        doc = cfio.loads(text)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, cfio.ChipletFormatError) as exc:
        unreadable(exc)
        return None
    netlist = doc.get("netlist")
    if netlist is None:
        netlist = {}
    if not isinstance(netlist, dict):
        unreadable("netlist is not a mapping")
        return None
    if netlist.get("external_netlist"):
        out.append(refusal(
            "external_netlist",
            f"base {path} keeps its nets in {netlist['external_netlist']!r}, "
            f"which a schematic edit cannot reach"))
    rows = netlist.get("nets") or []
    if not isinstance(rows, list):
        unreadable("netlist.nets is not a list")
        return None
    names, owner = set(), {}
    for row in rows:
        name = row.get("name") if isinstance(row, dict) else None
        conns = row.get("connections") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name or name in names:
            unreadable(f"empty or repeated net name {name!r}")
            return None
        names.add(name)
        if not isinstance(conns, list) or not all(
                isinstance(c, dict) and isinstance(c.get("component"), str)
                and isinstance(c.get("pin"), str) for c in conns):
            unreadable(f"net {name} has a connection without component and pin")
            return None
        for c in conns:
            key = (c["component"], c["pin"])
            if key in owner:
                unreadable(f"{key[0]}.{key[1]} is on net {owner[key]} and on net {name}")
                return None
            owner[key] = name
    bound = {row["name"] for row in rows if row.get("external") is True}
    kinds: Dict[str, Any] = {}
    pads: Dict[Terminal, str] = {}
    for comp in doc.get("components") or []:
        if not isinstance(comp, dict) or not isinstance(comp.get("id"), str):
            continue
        kinds[comp["id"]] = comp.get("type")
        for pad in comp.get("io_pads") or []:
            if isinstance(pad, dict) and isinstance(pad.get("net"), str) and pad["net"]:
                bound.add(pad["net"])
                if isinstance(pad.get("id"), str):
                    pads[(comp["id"], pad["id"])] = pad["net"]
    return _Base(path, raw, _sha256(raw), doc, rows, frozenset(bound), kinds, pads)


@dataclass
class _Drawn:
    """What the generator drew: its components, in order, and their terminals.
    A blank sheet's is its given file's dies, with no report."""
    report_path: Path
    report_sha: Optional[str]
    components: Tuple[str, ...]
    terminals: Dict[str, FrozenSet[str]]


def _read_report(form: ConnectivityForm, here: Path, base_sha: Optional[str],
                 out: List[dict]) -> Optional[_Drawn]:
    """Only four fields are read (word, chiplet_sha256, components, pins), under
    any format and version: the report is the generator's, not this contract's."""
    path = _resolve(form.generator_report.path, here)

    def malformed(why):
        out.append(refusal("malformed_report", f"generator report {path}: {why}"))

    try:
        raw = path.read_bytes()
    except OSError as exc:
        malformed(exc)
        return None
    if _sha256(raw) != form.generator_report.sha256:
        out.append(refusal(
            "generator_report_changed",
            f"generator report {path} is not the file the front end read "
            f"(sha256 {_sha256(raw)}, form says {form.generator_report.sha256})"))
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        malformed(exc)
        return None
    if not isinstance(doc, dict):
        malformed("not a JSON object")
        return None
    if doc.get("word") != "written":
        out.append(refusal(
            "generator_not_written",
            f"generator report {path} says {doc.get('word')!r}, not 'written'"))
        return None
    if base_sha is not None and doc.get("chiplet_sha256") != base_sha:
        out.append(refusal(
            "base_digest_mismatch",
            f"the generator drew from .chiplet sha256 {doc.get('chiplet_sha256')}, "
            f"the base given is {base_sha}"))
    comps, pins = doc.get("components"), doc.get("pins")
    if (not isinstance(comps, list) or not isinstance(pins, dict)
            or not all(isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"]
                       for c in comps)):
        malformed("needs components [{id}] and pins {component: {path, sha256}}")
        return None
    ids = tuple(c["id"] for c in comps)
    terminals: Dict[str, FrozenSet[str]] = {}
    ok = True
    for cid in ids:
        entry = pins.get(cid)
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or not isinstance(entry.get("sha256"), str)):
            malformed(f"no pin list recorded for {cid}")
            ok = False
            continue
        pin_path = _resolve(entry["path"], path.parent)
        try:
            pin_raw = pin_path.read_bytes()
            names = [p.get("name") for p in json.loads(pin_raw.decode("utf-8"))["pins"]]
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError) as exc:
            out.append(refusal("pin_list_changed", f"pin list of {cid} ({pin_path}): {exc}",
                               component=cid))
            ok = False
            continue
        if _sha256(pin_raw) != entry["sha256"]:
            out.append(refusal(
                "pin_list_changed",
                f"pin list of {cid} ({pin_path}) changed since the schematic was drawn",
                component=cid))
            ok = False
            continue
        terminals[cid] = frozenset(n for n in names if isinstance(n, str))
    if not ok:
        return None
    return _Drawn(path, _sha256(raw), ids, terminals)



# ---------------------------------------------------------------- the edit

def _generated_symbols(form: ConnectivityForm, drawn: _Drawn,
                       out: List[dict]) -> Dict[str, str]:
    """The usable symbols of a generated sheet, {ref: component}: each carries
    the identity the generator wrote."""
    components = set(drawn.components)
    claimed: Dict[str, str] = {}
    usable: Dict[str, str] = {}
    for sym in form.symbols:
        if sym.identity is None or sym.identity not in components:
            what = "no identity" if sym.identity is None else f"identity {sym.identity!r}"
            out.append(refusal(
                "unknown_symbol",
                f"symbol {sym.ref} has {what}, which is not a component the "
                f"generator drew", component=sym.ref))
            continue
        if sym.identity in claimed:
            out.append(refusal(
                "duplicate_identity",
                f"{sym.identity} is claimed by symbols {claimed[sym.identity]} and {sym.ref}",
                component=sym.identity))
            continue
        claimed[sym.identity] = sym.ref       # on the sheet, if mislabelled
        if sym.ref != sym.identity:
            out.append(refusal(
                "reference_mismatch",
                f"symbol {sym.ref} carries identity {sym.identity}", component=sym.identity))
            continue
        want, have = drawn.terminals[sym.identity], set(sym.pins)
        if have != want:
            out.append(refusal(
                "terminal_set_changed",
                f"symbol {sym.ref} has pins added {sorted(have - want)} and missing "
                f"{sorted(want - have)} against its pin list", component=sym.identity))
        usable[sym.ref] = sym.identity
    for cid in drawn.components:
        if cid not in claimed:
            out.append(refusal("missing_component",
                               f"{cid} has no symbol on the sheet", component=cid))
    return usable


def _sheet_nets(form: ConnectivityForm, drawn: _Drawn, usable: Dict[str, str],
                out: List[dict], name=None) -> Dict[str, FrozenSet[Terminal]]:
    """Named nets of the edit as {name: {(component, terminal)}}. ``name`` words
    a terminal in a message (the port's name on a blank sheet)."""
    name = name or (lambda term: f"{term[0]}.{term[1]}")
    refused_refs = {sym.ref for sym in form.symbols} - set(usable)
    owner: Dict[Terminal, str] = {}
    named: Dict[str, FrozenSet[Terminal]] = {}
    for net in form.nets:
        terms = set()
        for ref, pin in net.endpoints:
            if ref in refused_refs:
                continue        # its symbol is refused already
            if ref not in usable:
                out.append(refusal("unknown_ref",
                                   f"net {net.name} reaches {ref}, which is no symbol",
                                   net=net.name, component=ref))
                continue
            cid = usable[ref]
            if pin not in drawn.terminals[cid]:
                out.append(refusal(
                    "unknown_terminal",
                    f"net {net.name} connects {cid}.{pin}, and {pin} is not in the "
                    f"pin list of {cid}", net=net.name, component=cid, terminal=pin))
                continue
            terms.add((cid, pin))
        for term in sorted(terms):
            if term in owner:
                out.append(refusal(
                    "terminal_on_two_nets",
                    f"{name(term)} is on net {owner[term]} and on net {net.name}",
                    net=net.name, component=term[0], terminal=term[1]))
            else:
                owner[term] = net.name
        if net.auto_named:
            if len(set(net.endpoints)) > 1:
                ends = sorted({name(t) for t in terms}) or \
                    sorted({f"{r}.{p}" for r, p in net.endpoints})
                joined = ", ".join(ends)
                out.append(refusal(
                    "unnamed_net",
                    f"the wire joining {joined} has no label; put its net name on it",
                    net=net.name))
            continue            # one endpoint: an unconnected terminal
        if not is_sheet_name(net.name):
            out.append(refusal(
                "unmappable_name",
                f"net name {net.name!r} is not a {SHEET_NAME_WORDS}",
                net=net.name))
            continue
        named[net.name] = frozenset(terms)
    return named


@dataclass(frozen=True)
class GivenDie:
    """A die of a blank sheet's given file: the symbol it is placed from, its
    terminals, and the port each terminal serves (for the messages)."""
    top_cell: str
    terminals: FrozenSet[str]
    ports: Dict[str, str]


def _blank_symbols(form: ConnectivityForm, dies: Dict[str, GivenDie], given: str,
                   out: List[dict]) -> Dict[str, str]:
    """The usable symbols of a blank sheet, {ref: die id}: the designator is
    the identity, and the part placed must be that die's. The part's library
    is not checked against the library's name, which a tool lets the user
    choose; a die's symbol is known by its name and its pins."""
    library = form.library.name
    by_cell: Dict[str, List[str]] = {}
    for cid, die in dies.items():
        by_cell.setdefault(die.top_cell, []).append(cid)
    count: Dict[str, int] = {}
    for sym in form.symbols:
        count[sym.ref] = count.get(sym.ref, 0) + 1
    placed = {sym.ref for sym in form.symbols if count[sym.ref] == 1 and sym.ref in dies
              and sym.part.name == dies[sym.ref].top_cell}

    def free(cell: str) -> List[str]:
        return [cid for cid in by_cell.get(cell, []) if cid not in placed]

    shared = set()
    pending = set()         # cells of symbols placed but not yet named as a die
    usable = {}
    for sym in form.symbols:
        part = sym.part
        ids, open_ids = by_cell.get(part.name, []), free(part.name)
        if part.name not in by_cell:
            if part.lib != library:
                out.append(refusal(
                    "foreign_part",
                    f"{sym.ref} is a {part.lib}:{part.name} symbol, not a die of {given}; "
                    f"remove it, and place the dies from {library}", component=sym.ref))
            else:
                out.append(refusal(
                    "unknown_symbol",
                    f"{sym.ref} is a {part.name} symbol, which is no die of {given}; "
                    "remove it", component=sym.ref))
            continue
        if sym.ref.endswith("?"):
            hint = (f"annotate it {' or '.join(open_ids)}" if open_ids else
                    f"{' and '.join(ids)} {'is' if len(ids) == 1 else 'are'} placed "
                    "already, so remove it")
            pending.add(part.name)
            # one die it can be: the message goes on that die's line
            at = {"component": open_ids[0]} if len(open_ids) == 1 else {}
            out.append(refusal(
                "not_annotated", f"the {part.name} symbol is still named {sym.ref}; {hint}",
                **at))
            continue
        if count[sym.ref] > 1:
            pending.add(part.name)
            if sym.ref not in shared:
                shared.add(sym.ref)
                parts = list(dict.fromkeys(s.part.name for s in form.symbols if s.ref == sym.ref))
                dies_of = "; ".join(f"{p} is {' or '.join(by_cell.get(p, []))}" for p in parts
                                    if p in by_cell)
                out.append(refusal(
                    "duplicate_reference",
                    f"{count[sym.ref]} symbols are named {sym.ref} ({', '.join(parts)}); "
                    f"name each as the die it is ({dies_of})", component=sym.ref))
            continue
        if sym.ref not in dies:
            hint = (f"name it {' or '.join(open_ids)}" if open_ids else
                    f"{' and '.join(ids)} {'is' if len(ids) == 1 else 'are'} placed "
                    "already, so remove it")
            out.append(refusal(
                "unknown_symbol", f"{sym.ref} is not a die of {given}; {hint}",
                component=sym.ref))
            continue
        die = dies[sym.ref]
        if part.name != die.top_cell:
            tail = (f"; this symbol is {' or '.join(open_ids)}" if open_ids else
                    f"; {' and '.join(ids)} {'is' if len(ids) == 1 else 'are'} placed "
                    f"already, so replace this one with the {die.top_cell} symbol")
            out.append(refusal(
                "wrong_die",
                f"{sym.ref} is a {part.name} symbol, but {sym.ref} is {die.top_cell} in "
                f"{given}{tail}", component=sym.ref))
            continue
        have = set(sym.pins)
        if have != die.terminals:
            out.append(refusal(
                "terminal_set_changed",
                f"{sym.ref} has pins added {sorted(have - die.terminals)} and missing "
                f"{sorted(die.terminals - have)} against {die.top_cell}; place it again "
                f"from {library}", component=sym.ref))
            continue
        usable[sym.ref] = sym.ref
    named = {sym.ref for sym in form.symbols}
    for cid, die in dies.items():
        if cid not in named and die.top_cell not in pending:
            out.append(refusal(
                "missing_component",
                f"{cid} ({die.top_cell}) is not on the sheet; place it from {library} and "
                f"name it {cid}", component=cid))
    return usable


def _lone_labels(form: ConnectivityForm, named: Dict[str, FrozenSet[Terminal]],
                 held: FrozenSet[str], name, out: List[dict]) -> None:
    """A blank sheet's label on one pin only: a net with one end is no
    connection, and is most often a label misspelled at its other end."""
    for net in form.nets:
        terms = named.get(net.name)
        if (net.auto_named or net.name in held or terms is None or len(terms) != 1
                or len(set(net.endpoints)) != 1):
            continue            # an end on a refused symbol is reported with it
        (term,) = terms
        out.append(refusal(
            "lone_label",
            f"label {net.name} reaches only {name(term)}; check its spelling at the other "
            f"end", net=net.name, component=term[0], terminal=term[1]))


def _blank_held(base: _Base, dies: Dict[str, GivenDie]) -> Dict[str, FrozenSet[Terminal]]:
    """The nets of a blank sheet's given file the sheet does not draw, with
    their die terminals: a carrier's or external net, a rail, and a port on a
    net of its own, or a net with no connections. The sheet draws the nets
    between dies."""
    held: Dict[str, FrozenSet[Terminal]] = {}
    for row in base.nets:
        terms = frozenset((c["component"], c["pin"]) for c in row["connections"])
        on_dies = frozenset(t for t in terms if t[0] in dies)
        if (row["name"] in base.bound or row.get("class", "signal") != "signal"
                or terms != on_dies or len(terms) <= 1):
            held[row["name"]] = on_dies
    return held


def _hold(base: _Base, dies: Dict[str, GivenDie], held: Dict[str, FrozenSet[Terminal]],
          named: Dict[str, FrozenSet[Terminal]], given: str, name,
          out: List[dict]) -> Dict[str, FrozenSet[Terminal]]:
    """The sheet's nets with the held ones as the given file has them. A held
    terminal may be left unconnected, or put on its own net (a power symbol of
    that name); anything else is refused."""
    held_of = {t: h for h, ts in held.items() for t in ts}
    sheet_of = {t: n for n, ts in named.items() for t in ts}
    told = set()            # a port of several pads is told once

    def once(term: Terminal, net: str) -> bool:
        key = (name(term), net)
        if key in told:
            return False
        told.add(key)
        return True

    for term in sorted(held_of):
        now, was = sheet_of.get(term), held_of[term]
        if now is not None and now != was and once(term, was):
            out.append(refusal(
                "held_pin_changed",
                f"{name(term)} is on {was} in {given}; leave it unconnected or put it on "
                f"{was} (the sheet puts it on {now})",
                net=was, component=term[0], terminal=term[1]))
    for net in sorted(set(named) & set(held)):
        for term in sorted(named[net] - held[net]):
            if term in held_of or not once(term, net):
                continue        # refused above, or told for another pad
            out.append(refusal(
                "held_pin_changed",
                f"{net} is fixed by {given}, so {name(term)} cannot join it on the sheet",
                net=net, component=term[0], terminal=term[1]))
    full = {n: ts - held_of.keys() for n, ts in named.items() if n not in held}
    full = {n: ts for n, ts in full.items() if ts}
    full.update({h: ts for h, ts in held.items() if ts})
    return full


@dataclass
class _Edit:
    nets: List[Dict[str, Any]]      # the new netlist.nets
    renamed: Dict[str, str]


def _apply(base: _Base, drawn: _Drawn, named: Dict[str, FrozenSet[Terminal]],
           out: List[dict]) -> Optional[_Edit]:
    start = len(out)
    # Only die terminals are edited. Any other component's terminals stay where
    # the base puts them: on their netlist net, else on their io pad's net.
    editable = {cid for cid in drawn.components if base.kinds.get(cid) == "die"}
    base_net = {(c["component"], c["pin"]): row["name"]
                for row in base.nets for c in row["connections"]}
    sheet_net = {t: n for n, ts in named.items() for t in ts}
    for cid in drawn.components:
        if cid in editable:
            continue
        for pin in sorted(drawn.terminals[cid]):
            was = base_net.get((cid, pin), base.pads.get((cid, pin)))
            now = sheet_net.get((cid, pin))
            if was != now:
                out.append(refusal(
                    "bound_net_changed",
                    f"{cid}.{pin} is a terminal of {cid}, not of a die, and stays on "
                    f"net {was} (the sheet puts it on {now})",
                    component=cid, terminal=pin, net=was or now))
    named = {n: frozenset(t for t in ts if t[0] in editable) for n, ts in named.items()}
    named = {n: ts for n, ts in named.items() if ts}

    base_sets: Dict[str, FrozenSet[Terminal]] = {}
    for row in base.nets:
        seen = frozenset((c["component"], c["pin"]) for c in row["connections"]
                         if c["component"] in editable)
        if seen:
            base_sets[row["name"]] = seen
    # A net with a terminal the sheet cannot edit (not drawn, or not a die's), or
    # with none at all, is held like a pad-bound or external one: unchanged, or
    # refused.
    held = set(base.bound) | {row["name"] for row in base.nets
                              if not row["connections"]
                              or any(c["component"] not in editable for c in row["connections"])}
    for name in sorted(held):
        if base_sets.get(name) != named.get(name):
            out.append(refusal(
                "bound_net_changed",
                f"net {name} is bound outside the sheet (an io pad, an external net or "
                f"a terminal the sheet cannot edit) and cannot be renamed, removed or "
                f"rewired here", net=name))

    all_base = {row["name"] for row in base.nets}
    gone = [b for b in base_sets if b not in named]
    new = [n for n in named if n not in all_base]
    by_set = {named[n]: n for n in new}
    renamed = {b: by_set[base_sets[b]] for b in gone if base_sets[b] in by_set}
    removed = sorted(b for b in gone if b not in renamed)
    added = sorted(n for n in new if n not in renamed.values())

    new_of = {t: n for n, ts in named.items() for t in ts}
    record ={(c["component"], c["pin"]): c for row in base.nets for c in row["connections"]}
    layers: Dict[str, set] = {}
    for c in record.values():
        if isinstance(c.get("layer"), str) and c["layer"]:
            layers.setdefault(c["component"], set()).add(c["layer"])
    arriving: Dict[Terminal, Dict[str, Any]] = {}
    for term in sorted(new_of):
        if term in record:
            arriving[term] = copy.deepcopy(record[term])
            continue
        found = sorted(layers.get(term[0], ()))
        if not found and term[0] in editable:
            # A die the base connects nowhere (a blank sheet's given file may
            # state nothing of it) takes the one layer every die uses.
            found = sorted({layer for cid in editable for layer in layers.get(cid, ())})
        if len(found) != 1:
            out.append(refusal(
                "no_layer",
                f"{term[0]}.{term[1]} is newly connected and {term[0]} uses "
                f"{found or 'no'} layer(s) in the base, so there is no one layer to carry",
                component=term[0], terminal=term[1], net=new_of[term]))
            continue
        arriving[term] = {"component": term[0], "pin": term[1], "layer": found[0]}
    if len(out) > start:
        return None

    nets: List[Dict[str, Any]] = []
    for row in base.nets:
        name = row["name"]
        if name not in base_sets:
            nets.append(copy.deepcopy(row))         # nothing of it is editable here
            continue
        if name in removed:
            continue
        name = renamed.get(name, name)
        want = named[name]
        stay = [c for c in row["connections"]
                if c["component"] not in editable or (c["component"], c["pin"]) in want]
        have = {(c["component"], c["pin"]) for c in stay}
        new_row = copy.deepcopy(row)
        new_row["name"] = name
        new_row["connections"] = ([copy.deepcopy(c) for c in stay]
                                  + [arriving[t] for t in sorted(want - have)])
        nets.append(new_row)
    for name in added:
        nets.append({"name": name, "connections": [arriving[t] for t in sorted(named[name])]})

    return _Edit(nets, renamed)


@dataclass(frozen=True)
class Sheet:
    """An edited sheet's nets, checked against the base it was drawn from."""
    base_nets: List[Dict[str, Any]]     # the base's netlist.nets rows
    nets: List[Dict[str, Any]]          # the edited netlist.nets rows
    held: FrozenSet[str]                # base nets the sheet cannot change
    renamed: Dict[str, str]             # base net: its new name, terminals kept


def read_sheet(form_path, base_name: str, base_raw: bytes) -> Sheet:
    """The nets of the generated sheet in ``form_path``, drawn from the base
    ``base_raw``, or :class:`SchematicImportRefused` with every reason the
    sheet cannot be read: the form, its report, the base's digest and the held
    nets are all checked. Nothing is written."""
    form_path = Path(form_path)
    form = load_form(form_path, GENERATED)
    out: List[dict] = [dict(r) for r in form.refusals]
    base = _read_base(Path(base_name), out, raw=base_raw)
    if base is not None and base.sha != form.base_sha256:
        out.append(refusal(
            "base_digest_mismatch",
            f"the sheet was drawn from .chiplet sha256 {form.base_sha256}, the base "
            f"given is {base.sha}"))
    drawn = _read_report(form, form_path.parent, base.sha if base else None, out)
    named = _sheet_nets(form, drawn, _generated_symbols(form, drawn, out), out) \
        if drawn else None
    edit = _apply(base, drawn, named, out) if base is not None and named is not None \
        and not out else None
    if out:
        raise SchematicImportRefused(out)
    editable = {cid for cid in drawn.components if base.kinds.get(cid) == "die"}
    held = set(base.bound) | {row["name"] for row in base.nets
                              if any(c["component"] not in editable for c in row["connections"])}
    return Sheet(base.nets, edit.nets, frozenset(held), dict(edit.renamed))


def read_blank_sheet(form_path, given: str, base_raw: bytes,
                     dies: Dict[str, GivenDie]) -> Sheet:
    """The nets of the blank sheet in ``form_path`` (form 1.1), drawn against a
    given file: ``base_raw`` is the given file's design, ``dies`` its dies in
    file order, and ``given`` the file's name for the messages. Each symbol's
    designator must be a die and its part that die's symbol; the carrier, the
    rails and every port on a net of its own are held as the given file has
    them. Refused as :func:`read_sheet` is; nothing is written."""
    form = load_form(form_path, BLANK)

    def name(term: Terminal) -> str:
        die = dies.get(term[0])
        return f"{term[0]}.{die.ports.get(term[1], term[1]) if die else term[1]}"

    # The import names the symbols that share a designator, with their parts.
    out: List[dict] = [dict(r) for r in form.refusals if r.get("code") != "duplicate_reference"]
    base = _read_base(Path(given), out, raw=base_raw)
    # No report: the given file's dies are what the sheet draws.
    drawn = _Drawn(Path(given), None, tuple(dies), {i: d.terminals for i, d in dies.items()})
    named = _sheet_nets(form, drawn, _blank_symbols(form, dies, given, out), out, name)
    if base is None:
        raise SchematicImportRefused(out)
    held = _blank_held(base, dies)
    _lone_labels(form, named, frozenset(held), name, out)
    full = _hold(base, dies, held, named, given, name, out) if not out else {}
    edit = _apply(base, drawn, full, out) if not out else None
    if out:
        raise SchematicImportRefused(out)
    return Sheet(base.nets, edit.nets, frozenset(held), dict(edit.renamed))


__all__ = [
    "GivenDie",
    "SchematicImportRefused",
    "Sheet",
    "load_form",
    "read_blank_sheet",
    "read_classic_sheet",
    "read_sheet",
]
