# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Source documents (format 1.3): a ``system.chiplet`` read, checked and written.

A source names the dies, where they sit and how their ports are wired; the
assembly is derived from it and from each die's contract. This package is the
one reader of a source that passes ``allow_source=True`` to the vendored
``chiplet_format_io``. A 1.3 source may leave its dies and carrier size
unplaced; :func:`placement_edits` writes a placement into it, from a file
:func:`elaborate_provisional` elaborated for the placer.
"""
from __future__ import annotations

from .document import (
    Carrier,
    DieItem,
    BusItem,
    EdgeItem,
    External,
    FillKeepout,
    Finding,
    LinkItem,
    RailItem,
    RefItem,
    Source,
    SourceError,
    Unplaced,
    check_text,
    expand,
    loads_source,
    parse_ref,
    read_source,
)
from .elaborate import PROVISIONAL_METADATA, elaborate_provisional
from .place import PROFILE_KEYS, Placed, placement_edits
from .sheet_nets import sheet_net_names
from .supply import supply_pins

__all__ = [
    "Carrier",
    "DieItem",
    "BusItem",
    "EdgeItem",
    "External",
    "FillKeepout",
    "Finding",
    "LinkItem",
    "RailItem",
    "RefItem",
    "Source",
    "SourceError",
    "Unplaced",
    "check_text",
    "expand",
    "loads_source",
    "parse_ref",
    "read_source",
    "sheet_net_names",
    "supply_pins",
    # placement
    "PROFILE_KEYS",
    "PROVISIONAL_METADATA",
    "Placed",
    "elaborate_provisional",
    "placement_edits",
]
