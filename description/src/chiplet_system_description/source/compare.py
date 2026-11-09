# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Two sources compared as the systems they describe: which ports connect.

:func:`compare_nets` reads a file and a reference and reports, one
:class:`NetDifference` each, every way the file's nets differ: a net the file names otherwise, a net
whose ports are joined differently, a port the file leaves unconnected, a class
that differs. Ports are named as the die serves them (``U1.d2d_f3``), not by
pad. The comparison is of the systems, not of the text: two files that write
the same connections as a bus and as links are equal. The file is read as a
sheet base (:attr:`~.plan.Plan.sheet_base_refusals`), so one whose links are not
drawn yet reports each port as not connected; the reference must be whole.

The scope says what else must be equal. ``system`` (the default) elaborates
both files: everything outside the netlist (the dies, their placement, the
carrier, the assembly, the technologies) must be equal too, so a file wired
right on dies moved or swapped is not equal; ``_metadata`` is left out.
``connections`` compares the systems as connections only, read from the tier
1 topology rather than elaborated, so either file may be unplaced (format 1.3):
the die set (each instance's technology and top cell), the nets, each rail's
volts where both files declare one, each rail's routing requirement where
either states one (a width the other lacks routes another way), and how many
external pads reach each net.
Placement, rotation, carrier size, which edge a pad is on and its order there,
fill keep-out, method and thickness are left out, so a classic file placed by
``packaging-flow place``, or not placed yet, equals the reference it was drawn
from when it connects the same pins and its rails state the same requirements
(the sheet shows none; a re-import carries the file's own).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple, Union

from ..elaborate import elaborate
from .document import Source, read_source
from .elaborate import Resolver, build
from .plan import Plan, planned_nets
from .rail_requirement import RailRequirement

__all__ = ["NetDifference", "SCOPES", "compare_nets"]

Terminal = Tuple[str, str]
Nets = Dict[str, Tuple[str, FrozenSet[Terminal]]]
Say = Callable[[str, str, str], None]
#: What :func:`compare_nets` may compare, the default first.
SCOPES = ("system", "connections")
#: Top-level keys of an elaborated system the comparison leaves out.
LEFT_OUT = ("netlist", "_metadata")


@dataclass(frozen=True)
class NetDifference:
    """One way a file differs from its reference.

    ``code`` is one of ``system_differs`` (a top-level key), ``component_differs``,
    ``die_differs`` (an instance the other file lacks or serves from another
    die; connections scope), ``named_otherwise`` (the same ports, another net
    name), ``class_differs`` (the net's class, or a rail's volts or routing
    requirement),
    ``external_differs`` (another count of external pads on the net;
    connections scope), ``joined_otherwise`` (a net the reference does not
    have) and ``not_connected`` (a port the reference connects); ``subject`` is
    the key, component, instance, net or port it is about."""
    code: str
    subject: str
    message: str


@dataclass(frozen=True)
class _Side:
    """One file as compared: its nets, each (instance, pad)'s port name, and
    what else the scope compares."""
    nets: Nets
    ports: Dict[Terminal, str]
    doc: Optional[Mapping] = None  # system scope: the elaborated system
    dies: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # id -> (technology, top cell)
    volts: Dict[str, float] = field(default_factory=dict)  # rail -> volts, where declared
    requirements: Dict[str, RailRequirement] = field(default_factory=dict)  # where stated
    reach: Mapping[str, int] = field(default_factory=dict)  # net -> external pads on it


def _planned(path: Union[str, Path], *, sheet_base: bool, unplaced: bool = False):
    """The source, its plan and the nets that plan names. A refusal is
    :class:`~.plan.PlanRefused`, which is a :class:`SourceError`."""
    source = read_source(path)
    topology, nets = planned_nets(source, unplaced=unplaced, sheet_base=sheet_base)
    return source, topology, nets


def _port_names(source: Source, topology: Plan) -> Dict[Terminal, str]:
    return {(die.id, pad): port.name for die in source.dies
            for port in topology.folders[die.id].ports for pad in port.pads}


def _elaborated(path: Union[str, Path], resolver: Resolver, *, sheet_base: bool = False) -> _Side:
    source, topology, _nets_planned = _planned(path, sheet_base=sheet_base)
    doc = elaborate(build(source, topology, resolver))
    return _Side(_nets(doc), _port_names(source, topology), doc=doc)


def _nets(doc: Mapping) -> Nets:
    return {row["name"]: (row.get("class", "signal"),
                          frozenset((c["component"], c["pin"]) for c in row["connections"]))
            for row in (doc.get("netlist") or {}).get("nets") or []}


def _connections(path: Union[str, Path], *, sheet_base: bool = False) -> _Side:
    """The file at ``path`` as connections, from its plan with the unplaced
    opt-in: the nets the elaborator would draw, and what reaches them."""
    source, topology, planned = _planned(path, sheet_base=sheet_base, unplaced=True)
    nets: Nets = {net.name: (net.net_class, net.endpoints) for net in planned}
    # A tie or a supply the planner recorded as leaving the carrier. The name
    # is the one the planner gave the tie, not one rebuilt from the pad.
    reach = Counter(item.net for item in (*topology.ties, *topology.supplies) if item.leaves)
    dies = {die.id: (topology.folders[die.id].technology, topology.folders[die.id].top_cell)
            for die in source.dies}
    volts = {rail.name: rail.volts for rail in source.rails if rail.volts is not None}
    requirements = {rail.name: rail.requirement for rail in source.rails if rail.requirement}
    return _Side(nets, _port_names(source, topology), dies=dies, volts=volts,
                 requirements=requirements, reach=reach)


def compare_nets(path: Union[str, Path], reference: Union[str, Path],
                 resolver: Optional[Resolver], *, scope: str = "system"
                 ) -> Tuple[NetDifference, ...]:
    """How ``path`` differs from ``reference`` in ``scope`` (one of
    :data:`SCOPES`), one difference each; none when it connects what the
    reference connects. Either file failing to read raises its
    :class:`SourceError`. The ``connections`` scope elaborates nothing, so
    its ``resolver`` may be None."""
    if scope not in SCOPES:
        raise ValueError(f"scope {scope!r} is not one of {', '.join(SCOPES)}")
    if scope == "connections":
        got, ref = _connections(path, sheet_base=True), _connections(reference)
    else:
        got, ref = (_elaborated(path, resolver, sheet_base=True),
                    _elaborated(reference, resolver))
    ports = {**got.ports, **ref.ports}

    def word(term: Terminal) -> str:
        return f"{term[0]}.{ports.get(term, term[1])}"

    def joined(terms) -> str:
        names = sorted({word(t) for t in terms})
        return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]

    found: List[NetDifference] = []

    def say(code: str, subject: str, message: str) -> None:
        found.append(NetDifference(code, subject, message))

    if scope == "connections":
        _compare_dies(got.dies, ref.dies, say)
        matched = _connections_matched(got, ref, say)
    else:
        _compare_system(got.doc, ref.doc, say)
        matched = None
    _compare_netlists(got.nets, ref.nets, word, joined, say, matched)
    return tuple(found)


def _compare_system(got_doc: Mapping, ref_doc: Mapping, say: Say) -> None:
    for key in sorted((set(got_doc) | set(ref_doc)) - set(LEFT_OUT)):
        if key == "components":
            got_c = {c.get("id"): c for c in got_doc.get(key) or []}
            ref_c = {c.get("id"): c for c in ref_doc.get(key) or []}
            for cid in sorted(set(got_c) | set(ref_c), key=str):
                if got_c.get(cid) != ref_c.get(cid):
                    say("component_differs", str(cid),
                        f"{cid} differs from the reference (its die, placement or carrier "
                        "record); the connections are compared on the reference's dies")
        elif got_doc.get(key) != ref_doc.get(key):
            say("system_differs", key, f"{key} differs from the reference")


def _compare_dies(got: Mapping[str, Tuple[str, str]], ref: Mapping[str, Tuple[str, str]],
                  say: Say) -> None:
    def die(identity: Tuple[str, str]) -> str:
        return f"die {identity[1]} ({identity[0]})"

    for instance in sorted(set(got) | set(ref)):
        mine, theirs = got.get(instance), ref.get(instance)
        if mine == theirs:
            continue
        if theirs is None:
            say("die_differs", instance,
                f"{instance} is {die(mine)}; the reference has no instance {instance}")
        elif mine is None:
            say("die_differs", instance,
                f"{instance} is not in the file; the reference's {instance} is {die(theirs)}")
        else:
            say("die_differs", instance,
                f"{instance} is {die(mine)}; the reference's {instance} is {die(theirs)}")


def _connections_matched(got: _Side, ref: _Side, say: Say) -> Callable[[str, str], None]:
    """What the connections scope compares on a net the file and the reference
    both have: a rail's volts where both declare them, its routing requirement
    where either states one, and the external pads."""
    def matched(name: str, other: str) -> None:
        mine, theirs = got.volts.get(name), ref.volts.get(other)
        if mine is not None and theirs is not None and mine != theirs:
            say("class_differs", name,
                f"rail {name} is {mine:g} V; the reference's {other} is {theirs:g} V")
        mine, theirs = got.requirements.get(name), ref.requirements.get(other)
        if mine != theirs:
            say("class_differs", name,
                f"rail {name} {_requirement(mine)}; the reference's {other} {_requirement(theirs)}")
        mine, theirs = got.reach.get(name, 0), ref.reach.get(other, 0)
        if mine != theirs:
            say("external_differs", name,
                f"net {name} has {_pads(mine)}; the reference's {other} has {_pads(theirs)}")
    return matched


def _requirement(stated: Optional[RailRequirement]) -> str:
    if stated is None:
        return "states no routing requirement"
    return "states " + ", ".join(f"{key} {value:g}" for key, value
                                 in stated._asdict().items() if value is not None)


def _pads(count: int) -> str:
    return f"{count} external pad" + ("" if count == 1 else "s") if count else "no external pad"


def _compare_netlists(got: Nets, ref: Nets, word: Callable[[Terminal], str],
                      joined: Callable, say: Say,
                      matched: Optional[Callable[[str, str], None]]) -> None:
    ref_by_terms = {terms: name for name, (_, terms) in ref.items()}
    matched_got, matched_ref = set(), set()
    for name, (cls, terms) in sorted(got.items()):
        other = ref_by_terms.get(terms)
        if other is None:
            continue
        matched_got.add(name)
        matched_ref.add(other)
        if other != name:
            say("named_otherwise", name,
                f"{joined(terms)} are joined as {name}; the reference calls the net {other}")
        if ref[other][0] != cls:
            say("class_differs", name,
                f"net {name} is {cls}; the reference's {other} is {ref[other][0]}")
        if matched is not None:
            matched(name, other)

    ref_of = {t: n for n, (_, ts) in ref.items() for t in ts}
    got_of = {t: n for n, (_, ts) in got.items() for t in ts}

    def in_reference(term: Terminal) -> str:
        net = ref_of.get(term)
        if net is None:
            return f"{word(term)} is not connected"
        peers = ref[net][1] - {term}
        return f"{word(term)} joins {joined(peers)} on {net}" if peers else \
            f"{word(term)} is alone on {net}"

    for name in sorted(set(got) - matched_got):
        terms = got[name][1]
        say("joined_otherwise", name,
            f"net {name} joins {joined(terms)}; in the reference "
            + "; ".join(in_reference(t) for t in sorted(terms, key=word)))
    for name in sorted(set(ref) - matched_ref):
        for term in sorted(ref[name][1], key=word):
            if term in got_of:
                continue        # reported with the file's net it is on
            peers = ref[name][1] - {term}
            say("not_connected", word(term),
                f"{word(term)} is not connected; the reference joins it to {joined(peers)} "
                f"on {name}" if peers else
                f"{word(term)} is not connected; the reference has it alone on {name}")
