# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Format 1.3, the unplaced source: what a file may leave for placement to decide.

A source drawn on a sheet says what the system is and nothing geometric, so
from 1.3 a die line may omit ``at`` and ``rotation`` (both or neither), the
carrier may omit ``size``, ``external`` may omit ``edges``, ``system.dies`` may
be absent (a project file before its first sheet import), and the root key
``unplaced: {pins: [...]}`` holds the external entries no edge carries yet.
Each is refused in a file stamped below 1.3, with a finding naming the minor.
The vendored reader holds the rule and its words
(``cfio.UNPLACED_SINCE``, ``cfio.unplaced_refusal``); :mod:`.document` puts
each finding on its line with them.

``unplaced`` is a ROOT key on purpose: a reader before 1.3 refuses an unknown
root key at any minor, whereas it skips an unknown item or system key of a
higher minor with a note, and would read on without those externals, wrong
rather than refused.

Tier 0 reads an unplaced source. Tier 1 refuses it, one finding per unplaced
thing, each naming the fix, unless the caller opts in (``plan(source,
unplaced=True)``), which turns those findings into notes; elaboration always
refuses it. Nothing here imports the reader, so the reader imports this.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from .._cfio import cfio

if TYPE_CHECKING:  # pragma: no cover
    from .document import Source

#: The verb that places an unplaced source, as findings name it.
PLACE = "packaging-flow place"
#: The keys of the ``unplaced`` block.
UNPLACED_KEYS = ("pins",)


def content_minor(source: "Source") -> str:
    """The lowest minor that carries everything ``source`` writes, as the
    vendored reader's floor gives it: 1.5 when a rail states a routing
    requirement, 1.4 when a die names an admission, 1.3
    when it leaves anything to placement (or has no dies yet), 1.2 for a rail's
    ``volts``, else 1.1."""
    return cfio.content_format_floor(source.data) or cfio.SOURCE_FORMAT_VERSION


def read_unplaced(reader, node: yaml.Node, line: int, rail_names: set):
    """The root ``unplaced`` block, read by ``reader`` (the tier 0 walk): a
    block mapping whose ``pins`` is one line holding a flow list of port refs
    and rail names, each entry once across it and the external edges, as on an
    edge row. Returns (entries, the pins line), or None."""
    if not isinstance(node, yaml.MappingNode) or node.flow_style:
        reader.finding(line, "unplaced is a block mapping holding pins")
        return None
    pairs = reader.mapping(node, "unplaced") or []
    for key, value in pairs:
        if key not in UNPLACED_KEYS:
            reader.unknown_key(value.start_mark.line + 1, "unplaced", key, UNPLACED_KEYS)
    got = dict(pairs)
    if "pins" not in got:
        reader.finding(line, "unplaced: missing required key 'pins'")
        return None
    value = got["pins"]
    pline = value.start_mark.line + 1
    if not (isinstance(value, yaml.SequenceNode) and value.flow_style) \
            or value.start_mark.line != value.end_mark.line:
        reader.finding(pline, "unplaced.pins is one line holding a flow list [...]")
        return None
    return reader.edge_entries(value, "unplaced.pins", pline, rail_names), pline
