# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Two sources compared as the systems they elaborate to: which ports connect.

:func:`compare_nets` elaborates a file and a reference and reports, one
:class:`NetDifference` each, every way the file's nets differ: a net the file names otherwise, a net
whose ports are joined differently, a port the file leaves unconnected, a class
that differs. Ports are named as the die serves them (``U1.d2d_f3``), not by
pad. Everything outside the netlist (the dies, their placement, the carrier,
the assembly, the technologies) must be equal too, so a file wired right on
dies moved or swapped is not equal; ``_metadata`` is left out. The comparison
is of the elaborated systems, not of the text: two files that write the same
connections as a bus and as links are equal. The file is read as a sheet base
(:attr:`~.plan.Plan.sheet_base_refusals`), so one whose links are not drawn yet
reports each port as not connected; the reference must be whole.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Mapping, Tuple, Union

from ..elaborate import elaborate
from .document import SourceError, read_source
from .elaborate import Resolver, build
from .plan import plan

__all__ = ["NetDifference", "compare_nets"]

Terminal = Tuple[str, str]
#: Top-level keys of an elaborated system the comparison leaves out.
LEFT_OUT = ("netlist", "_metadata")


@dataclass(frozen=True)
class NetDifference:
    """One way a file differs from its reference.

    ``code`` is one of ``system_differs`` (a top-level key), ``component_differs``,
    ``named_otherwise`` (the same ports, another net name), ``class_differs``,
    ``joined_otherwise`` (a net the reference does not have) and
    ``not_connected`` (a port the reference connects); ``subject`` is the key,
    component, net or port it is about."""
    code: str
    subject: str
    message: str


def _elaborated(path: Union[str, Path], resolver: Resolver, *, sheet_base: bool = False):
    source = read_source(path)
    topology = plan(source)
    refusals = topology.sheet_base_refusals if sheet_base else topology.refusals
    if refusals:
        raise SourceError(source.path, refusals)
    doc = elaborate(build(source, topology, resolver))
    ports = {(die.id, pad): port.name for die in source.dies
             for port in topology.folders[die.id].ports for pad in port.pads}
    return doc, ports


def _nets(doc: Mapping) -> Dict[str, Tuple[str, FrozenSet[Terminal]]]:
    return {row["name"]: (row.get("class", "signal"),
                          frozenset((c["component"], c["pin"]) for c in row["connections"]))
            for row in (doc.get("netlist") or {}).get("nets") or []}


def compare_nets(path: Union[str, Path], reference: Union[str, Path],
                 resolver: Resolver) -> Tuple[NetDifference, ...]:
    """How ``path`` differs from ``reference``, one difference each; none when
    it connects what the reference connects. Either file failing to elaborate
    raises its :class:`SourceError`."""
    got_doc, got_ports = _elaborated(path, resolver, sheet_base=True)
    ref_doc, ref_ports = _elaborated(reference, resolver)
    ports = {**got_ports, **ref_ports}

    def word(term: Terminal) -> str:
        return f"{term[0]}.{ports.get(term, term[1])}"

    def joined(terms) -> str:
        names = sorted({word(t) for t in terms})
        return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]

    found: List[NetDifference] = []

    def say(code: str, subject: str, message: str) -> None:
        found.append(NetDifference(code, subject, message))

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

    got, ref = _nets(got_doc), _nets(ref_doc)
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
    return tuple(found)
