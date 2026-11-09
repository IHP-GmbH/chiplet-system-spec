# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Each net the planner names, mapped onto the sheet net that holds it.

The planner stays free of the connectivity form. This module is the one
place that has both: :func:`planned_nets` for the system side, the form's
nets for the sheet side. A planner refusal is ``not_expressible`` and no
partial map is returned. Every refusal is collected before one is raised.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

from ..connectivity import EXT_PAD, ConnectivityForm, SchematicImportRefused, refusal
from .document import Source
from .plan import ALONE, PlanRefused, PlannedNet, planned_nets

_UNWRITABLE = ('"', "\r", "\n")


@dataclass(frozen=True)
class _Held:
    """One sheet net as matched."""

    name: str
    ends: FrozenSet[Tuple[str, str]]
    j_count: int
    auto: bool
    foreign: Tuple[str, ...]


def _held(source: Source, form: ConnectivityForm) -> List[_Held]:
    dies = frozenset(die.id for die in source.dies)
    pads = frozenset(sym.ref for sym in form.symbols
                     if sym.part is not None and sym.part.name == EXT_PAD)
    rows = []
    for net in form.nets:
        ends = frozenset((ref, pin) for ref, pin in net.endpoints if ref in dies)
        j_count = sum(1 for ref, _pin in net.endpoints if ref in pads)
        foreign = tuple(dict.fromkeys(
            ref for ref, _pin in net.endpoints if ref not in dies and ref not in pads))
        auto = bool(net.auto_named and len(set(net.endpoints)) == 1)
        rows.append(_Held(net.name, ends, j_count, auto, foreign))
    return rows


def _alone(net: PlannedNet) -> bool:
    """A pad on its own: the one system net an ``unconnected-(...)`` name can be."""
    return net.net_class == "signal" and net.origin == ALONE and len(net.endpoints) == 1


def _candidates(net: PlannedNet, rows: List[_Held]) -> List[str]:
    found = []
    rail = net.net_class != "signal"
    want = 1 if net.leaves else 0
    for row in rows:
        if row.auto and not _alone(net):
            continue
        if row.ends != net.endpoints or row.j_count != want:
            continue
        if rail and row.name != net.name:
            continue
        found.append(row.name)
    return found


def _sharing(net: PlannedNet, rows: List[_Held], skip: Optional[str] = None) -> List[str]:
    """Non-auto sheet nets whose die pins meet ``net``, optionally except ``skip``."""
    return sorted({row.name for row in rows
                   if not row.auto and row.name != skip and bool(row.ends & net.endpoints)})


def _not_one(net: PlannedNet, rows: List[_Held]) -> Dict[str, str]:
    joined = ", ".join(_sharing(net, rows)) or "none"
    return refusal("not_one_to_one",
                   f"system net {net.name} is not one sheet net ({joined})", net=net.name)


def _unclaimed(rows: List[_Held], claimed: FrozenSet[Tuple[str, str]]) -> List[Dict[str, str]]:
    out = []
    for row in rows:
        if row.auto or not row.ends or row.ends <= claimed:
            continue
        out.append(refusal(
            "unclaimed_pins",
            f"sheet net {row.name} holds die pins no system net claims", net=row.name))
    return out


def _foreign(rows: List[_Held]) -> List[Dict[str, str]]:
    out = []
    for row in rows:
        if not row.foreign:
            continue
        named = ", ".join(row.foreign)
        out.append(refusal(
            "foreign_ref",
            f"sheet net {row.name} holds {named}, which is neither a die nor an {EXT_PAD}",
            net=row.name))
    return out


def _unwritable(name: str) -> Dict[str, str]:
    return refusal(
        "unwritable_net_name",
        f"sheet net {name!r} holds a double quote or a line break, which the "
        "board writer cannot write", net=name)


def sheet_net_names(source: Source, form: ConnectivityForm) -> Dict[str, str]:
    """Each system net mapped to the one sheet net that holds it.

    Die endpoints must be equal, and the carrier pads must match exactly: a
    net that leaves the carrier (the tie's ``leaves``) maps only to
    a sheet net with one ``EXT_PAD``, and a link or an ``alone`` pad only to a
    sheet net with none. A rail maps only to its own name. One wording covers
    a split, a merge, a rail no sheet net holds, a J on a link, an alone pad
    drawn to a J and a tie with two J's (``not_one_to_one``). A sheet net whose
    die pins are not all claimed is ``unclaimed_pins``, including one that
    mixes claimed and unclaimed pins. A ref that is neither a source die nor
    an ``EXT_PAD`` is ``foreign_ref``. A one-pin auto-named net is invisible
    to the unclaimed check and to rails, and is the match for an alone pad.
    ``unwritable_net_name`` is one of the refusals, not a reason to hide the
    others. ``{slash}`` and any other sheet text is returned unchanged.
    """
    try:
        _topology, planned = planned_nets(source, unplaced=True)
    except PlanRefused as exc:
        raise SchematicImportRefused([refusal(
            "not_expressible", "The file cannot hold the sheet: " + finding.message)
            for finding in exc.findings]) from exc
    rows = _held(source, form)
    claimed = frozenset(pin for net in planned for pin in net.endpoints)
    out = _foreign(rows) + _unclaimed(rows, claimed)
    mapped: Dict[str, str] = {}
    for net in planned:
        found = _candidates(net, rows)
        if len(found) == 1 and not _sharing(net, rows, found[0]):
            mapped[net.name] = found[0]
            continue
        out.append(_not_one(net, rows))
    out.extend(_unwritable(name) for name in mapped.values()
               if any(ch in name for ch in _UNWRITABLE))
    if out:
        raise SchematicImportRefused(out)
    return mapped
