# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Source documents (format 1.2): a ``system.chiplet`` read, checked and written.

A source names the dies, where they sit and how their ports are wired; the
assembly is derived from it and from each die's contract. This package is the
one reader of a source that passes ``allow_source=True`` to the vendored
``chiplet_format_io``.
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
    check_text,
    expand,
    loads_source,
    parse_ref,
    read_source,
)
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
    "check_text",
    "expand",
    "loads_source",
    "parse_ref",
    "read_source",
    "supply_pins",
]
