# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""External rows written whole: an edge row or ``unplaced.pins``.

:class:`SetEntries` lands here. A row whose entries already begin the ones
asked for keeps its bytes and gains the rest before its ``]``. A new edge row
goes after the last one, and a new ``unplaced`` block after ``system``.

:class:`_ExternalRows` is mixed into the patcher for now. It expects the
patcher's ``text``, ``doc``, ``spans``, ``whole_rows``, ``change``, ``token``
and ``written``.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import yaml

from .document import EDGES, expand, parse_ref
from .scalars import scalar


def flow_list(items: Sequence[str]) -> str:
    """A flow list of scalars, as an edge row writes its entries."""
    return "[" + ", ".join(scalar(i) for i in items) + "]"


def inserted_at_end(text: str, at: int, body: str) -> str:
    """``body`` inserted at ``at``. At the end of a file with no trailing
    newline, the inserted block starts on that last line and does not add one."""
    if at == len(text) and not text.endswith("\n"):
        return "\n" + body[:-1]
    return body


def last_row(node: yaml.Node) -> int:
    """The 0-based line holding the last character of ``node``: a block
    collection ends on its last item's line, never on the comments after it."""
    while isinstance(node, (yaml.MappingNode, yaml.SequenceNode)) and not node.flow_style \
            and node.value:
        node = node.value[-1][1] if isinstance(node, yaml.MappingNode) else node.value[-1]
    end = node.end_mark
    return end.line if end.column else end.line - 1


def _entry_members(written: str) -> List[str]:
    """The entries one written external entry stands for."""
    if "." not in written:
        return [written]
    ref = parse_ref(written)
    return [f"{ref.instance}.{port}" for port in expand(ref.port)]


def _after(row: yaml.Node, entries: Sequence[str]) -> Optional[List[str]]:
    """What ``entries`` add after the edge row ``row``'s own entries, when
    those, by what they stand for, begin ``entries``; else None."""
    have = [m for node in row.value for m in _entry_members(node.value)]
    stood: List[str] = []
    for n, entry in enumerate(entries):
        if stood == have:
            return list(entries[n:])
        stood.extend(_entry_members(entry))
    return [] if stood == have else None


class _ExternalRows:
    """The :class:`SetEntries` half of the patcher.

    Mixed into the patcher for now. It uses ``text``, ``doc``, ``spans``,
    ``whole_rows``, ``change``, ``token`` and ``written`` from that class.
    """

    def edge_rows(self, rows: List[str]) -> None:
        """New edge rows after the last one, with the ``edges`` key when absent."""
        external = self.doc.keys["external"][1]
        if self.doc.edges_node is not None and self.doc.edges_node.value:
            first, last = self.doc.edges_node.value[0][0], self.doc.edges_node.value[-1][1]
            indent, head = " " * first.start_mark.column, ""
        else:
            profile = next(k for k, _ in external.value)
            last = external.value[-1][1]
            indent = " " * (profile.start_mark.column + 2)
            head = " " * profile.start_mark.column + "edges:\n"
        _, at = self.doc.line_span(last_row(last))
        body = inserted_at_end(self.text, at, head + "".join(f"{indent}{row}\n" for row in rows))
        self.spans.put(at, at, body)
        for row in rows:
            self.change("external", row.split(":", 1)[0], None, None, f"{indent}{row}",
                        last.end_mark.line + 1)

    def set_entries(self, edit) -> None:
        if edit.row not in EDGES and edit.row != "unplaced":
            raise ValueError(f"{edit.row!r} is not north, south, west, east or unplaced")
        if edit.row in self.whole_rows:
            raise ValueError(f"external row {edit.row!r} is written twice")
        if edit.row in EDGES and "external" not in self.doc.keys and edit.entries:
            raise ValueError("the file has no external block to hold an edge row")
        self.whole_rows[edit.row] = tuple(edit.entries)

    def place_entries(self) -> None:
        """Each row :class:`SetEntries` wrote: rewritten, removed or added. New
        edge rows go after the last row, and a new ``unplaced`` block after the
        last line of ``system``, so the rows land before it."""
        if not self.whole_rows:
            return
        new_rows, kept = [], set(self.doc.rows)
        for edge in EDGES:
            if edge not in self.whole_rows:
                continue
            entries, row = self.whole_rows[edge], self.doc.rows.get(edge)
            text = flow_list(entries)
            if row is None:
                if entries:
                    new_rows.append(f"{edge}: {text}")
                continue
            line = row.start_mark.line + 1
            added = _after(row, entries)
            if not entries:
                self.spans.put(*self.doc.line_span(row.start_mark.line), "")
                kept.discard(edge)
                self.change("external", edge, None, self.written(row), None, line)
            elif added is not None:
                if added:
                    self.append(row, added, edge)
            elif text != self.written(row):
                self.token(row, text)
                self.change("external", edge, None, self.written(row), text, line)
        if new_rows:
            self.edge_rows(new_rows)
        elif self.doc.edges_node is not None and not kept:
            key = next(k for k, v in self.doc.keys["external"][1].value
                       if v is self.doc.edges_node)
            self.spans.put(*self.doc.line_span(key.start_mark.line), "")
        if "unplaced" in self.whole_rows:
            self.unplaced_row(self.whole_rows["unplaced"])

    def append(self, row: yaml.Node, added: Sequence[str], edge: str) -> None:
        """``added`` written at the end of the edge row ``row``, before its ``]``."""
        close = row.end_mark.index - 1
        if self.text[close] != "]":
            raise ValueError(f"external.edges.{edge} does not end its line with ']'")
        # After the last entry, not before the ``]``: tier 0 allows a trailing comma.
        tail = ", ".join(scalar(e) for e in added)
        at, piece = ((row.value[-1].end_mark.index, f", {tail}") if row.value
                     else (row.start_mark.index + 1, tail))
        self.spans.put(at, at, piece)
        start, end = row.start_mark.index, row.end_mark.index
        self.change("external", edge, None, self.written(row),
                    self.text[start:at] + piece + self.text[at:end], row.start_mark.line + 1)

    def unplaced_row(self, entries) -> None:
        pins, text = self.doc.pins, flow_list(entries)
        if pins is not None:
            line = pins.start_mark.line + 1
            if not entries:
                # the block goes with its last pin, both its lines
                key, value = self.doc.roots["unplaced"]
                start, _ = self.doc.line_span(key.start_mark.line)
                _, end = self.doc.line_span(max(v.end_mark.line for _, v in value.value))
                self.spans.put(start, end, "")
                self.change("unplaced", "pins", None, self.written(pins), None, line)
            elif text != self.written(pins):
                self.token(pins, text)
                self.change("unplaced", "pins", None, self.written(pins), text, line)
            return
        if not entries:
            return
        row = last_row(self.doc.system)
        _, at = self.doc.line_span(row)
        body = inserted_at_end(self.text, at, f"unplaced:\n  pins: {text}\n")
        self.spans.put(at, at, body)
        self.change("unplaced", "pins", None, None, text, row + 1)
