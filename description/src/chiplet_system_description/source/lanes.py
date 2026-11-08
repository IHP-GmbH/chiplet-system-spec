# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""A sheet's nets between two die ports, read back as the link that draws them.

A sheet draws a link as one net per lane, each joining one pad of each port.
The lift of a blank or generated sheet
(:func:`chiplet_system_description.source.edit.schematic_edits`) and the
classic import (:mod:`chiplet_system_description.classic_import`) read them
with the rules here, so a drawing reads the same on every kind of sheet: a
port wired to two ports is ``pad_split``, lanes that do not pair the two
ports' pads by ordinal are ``not_expressible``, and lane names that are no
``<net>0``, ``<net>1``, ... as :func:`~.plan.link_net` names them are
``rename_no_field``.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from .plan import link_net

Port = Tuple[str, str]          # (instance, port name)
Terminal = Tuple[str, str]      # (instance, pad)
#: Each pair of ports a sheet's nets join, with those nets as (name, terminals).
Lanes = Mapping[FrozenSet[Port], Sequence[Tuple[str, FrozenSet[Terminal]]]]


@dataclass(frozen=True)
class LaneFinding:
    """Why a pair of ports reads as no link."""

    code: str                   # pad_split, not_expressible or rename_no_field
    port: Port                  # the split port, else the pair's first
    other: Optional[Port] = None    # the pair's other port; None for pad_split
    names: Tuple[str, ...] = ()     # rename_no_field: the lane names, by ordinal

    @property
    def message(self) -> str:
        port = "{}.{}".format(*self.port)
        if self.code == "pad_split":
            return f"the pads of {port} go to two ports; a port is wired whole, to one port"
        other = "{}.{}".format(*self.other)
        if self.code == "not_expressible":
            return (f"the nets between {port} and {other} do not pair their pads lane by lane, "
                    "by ordinal; a link joins two ports whole")
        return (f"the lanes of {port} to {other} are named {', '.join(self.names)}; a link "
                "names its lanes <net>0, <net>1, ... Name them so")


def lane_base(names: Sequence[str]) -> Optional[str]:
    """The link net whose lanes :func:`~.plan.link_net` names ``names``, in
    lane order, or None when it names them no way."""
    if len(names) == 1:
        return names[0]
    base = names[0][:-1]
    return base if [link_net(base, len(names), i) for i in range(len(names))] == list(names) \
        else None


def read_lanes(lanes: Lanes, pads: Callable[[Port], Sequence[str]], order: Callable[[Port], Any]
               ) -> Tuple[Dict[FrozenSet[Port], Tuple[Port, Port, str]], List[LaneFinding]]:
    """The link each pair of ports in ``lanes`` draws, as (a, b, net) with
    ``a`` the port ``order`` puts first, and the findings for every pair that
    draws none. ``pads`` gives a port's pads in ordinal order. A pair with a
    split port is not read further."""
    count = Counter(port for key in lanes for port in key)
    split = sorted((port for port, n in count.items() if n > 1), key=order)
    findings = [LaneFinding("pad_split", port) for port in split]
    links: Dict[FrozenSet[Port], Tuple[Port, Port, str]] = {}
    for key, nets in sorted(lanes.items(), key=lambda kv: sorted(map(order, kv[0]))):
        a, b = sorted(key, key=order)
        if count[a] > 1 or count[b] > 1:
            continue
        pa, pb = pads(a), pads(b)
        by_pair = {frozenset(terms): name for name, terms in nets}
        names = [by_pair.get(frozenset({(a[0], x), (b[0], y)})) for x, y in zip(pa, pb)]
        if len(pa) != len(pb) or None in names or len(nets) != len(pa):
            findings.append(LaneFinding("not_expressible", a, b))
            continue
        net = lane_base(names)
        if net is None:
            findings.append(LaneFinding("rename_no_field", a, b, tuple(names)))
            continue
        links[key] = (a, b, net)
    return links, findings


__all__ = ["LaneFinding", "lane_base", "read_lanes"]
