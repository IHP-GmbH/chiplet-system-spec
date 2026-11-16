# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The one writer of an existing source: edits applied to the file's own bytes.

:func:`patch_system` takes the bytes of a ``system.chiplet`` and a list of
edits, and returns the new bytes with a diff. It is pure: it reads no file and
consults no clock or environment, so a caller can show the diff, check the
file's digest and write the result itself. Three edits, each naming its item as
the input file names it:

- :class:`Replace` rewrites one field of an item;
- :class:`Insert` adds an item line at the end of its block, writing the block
  key too when the block is absent;
- :class:`Delete` removes an item line, and the block key with its last item.

Items are named by identity, never by line: a die by ``id``, a bus by its first
lane on its first end (``U1.d2d_f0``), a link by its ``a`` as written, a rail by
``name``, an ``alone`` or ``open`` item by its ref as written.

Every byte the edits do not touch stays as it was: comments, blank lines, the
``flow`` block, the line endings (CRLF is kept; a file that mixes endings is
refused) and a missing final newline. A replaced token is located by the
composed node's marks, so the rest of its line keeps its bytes, a trailing
comment included. New values follow the source writer's rules: the bare-string
rule, the shortest decimal, ranges collapsed. A placement is compared on the
1 nm grid, so a value that moved less than that writes nothing.

The result is read back through tier 0 before it is returned; a patch that
fails there is this module's defect and raises, never returns.
"""
from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import yaml

from ..errors import SystemSpecError
from ..model import PortRef
from .document import ITEM_KEYS, SYSTEM_KEYS, Finding, SourceError, check_text, expand, parse_ref
from .plan import collapse
from .scalars import number, quantized, scalar

#: The blocks that hold one item per line, which edits may touch.
BLOCKS = ("dies", "buses", "links", "rails", "alone", "open")
#: The fields a Replace may set, per block.
FIELDS = {
    "dies": ("at", "rotation"),
    "buses": ("ports", "net_prefix", "net_suffix"),
    "links": ("net",),
    "rails": ("name", "ports"),
}


@dataclass(frozen=True)
class Replace:
    """Set ``field`` of ``item`` in ``block``. ``value`` is, by field: ``at`` an
    (x, y) pair of numbers, ``rotation`` one of 0, 90, 180, 270, ``ports`` the
    refs (rails) or port names (buses) in order, any other field a string, or
    None to drop an optional one."""

    block: str
    item: str
    field: str
    value: Any


@dataclass(frozen=True)
class Insert:
    """Add an item to ``block``: a ref for ``alone`` and ``open``, else the
    item's ``(key, value)`` pairs, values as :class:`Replace` takes them."""

    block: str
    item: Union[str, Tuple[Tuple[str, Any], ...]]


@dataclass(frozen=True)
class Delete:
    """Remove ``item`` from ``block``."""

    block: str
    item: str


Edit = Union[Replace, Insert, Delete]


@dataclass(frozen=True)
class Change:
    """One change as the file shows it: the text before and after, None for a
    line or field that is not there on that side. ``line`` is the input's,
    1-based; an inserted line gives the line it follows."""

    block: str
    item: str
    field: Optional[str]
    old: Optional[str]
    new: Optional[str]
    line: int


@dataclass(frozen=True)
class Patch:
    text: bytes
    #: A unified diff, ``a/<name>`` to ``b/<name>``; empty when nothing moved.
    diff: str
    #: The sha256 of the bytes patched, for the caller's check before it writes.
    sha256_before: str
    changes: Tuple[Change, ...]


@dataclass(frozen=True)
class EditFinding:
    """Why an edit cannot be written. ``code`` is the contract; the message is
    for a person and may change."""

    code: str
    line: int
    port: Optional[str]
    message: str


class EditRefused(SystemSpecError):
    """An edit the source cannot hold, with every finding that refuses it."""

    def __init__(self, findings: Sequence[EditFinding]):
        self.findings = tuple(findings)
        super().__init__("\n".join(f.message for f in self.findings))


# ----------------------------------------------------------------------
# Text and endings
# ----------------------------------------------------------------------
def decode(data: bytes) -> Tuple[str, bool]:
    """The text with LF endings, and whether the bytes used CRLF throughout."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EditRefused([EditFinding("not_a_source", 0, None,
                                       f"The file is not UTF-8 text ({exc.reason}).")]) from None
    crlf = text.count("\r\n")
    if crlf and text.count("\n") == crlf and text.count("\r") == crlf:
        return text.replace("\r\n", "\n"), True
    if "\r" in text:
        raise EditRefused([EditFinding(
            "line_endings_mixed", 0, None,
            "The file mixes line endings; save it with one kind (LF or CRLF) and edit again.")])
    return text, False


def _encode(text: str, crlf: bool) -> bytes:
    return (text.replace("\n", "\r\n") if crlf else text).encode("utf-8")


def not_a_source(findings: Sequence[Finding]) -> EditRefused:
    lines = [EditFinding("not_a_source", 0, None,
                         "The file does not read as a source; fix it with "
                         "`chiplet-system-description check` first.")]
    lines += [EditFinding("not_a_source", f.line, None, f.render("system.chiplet"))
              for f in findings]
    return EditRefused(lines)


# ----------------------------------------------------------------------
# The composed file
# ----------------------------------------------------------------------
def _get(item: yaml.Node, key: str) -> Optional[yaml.Node]:
    for k, v in item.value:
        if k.value == key:
            return v
    return None


def identity(block: str, item: yaml.Node) -> str:
    """The name an edit gives ``item`` of ``block``."""
    if block in ("alone", "open"):
        return item.value
    if block == "dies":
        return _get(item, "id").value
    if block == "links":
        return _get(item, "a").value
    if block == "rails":
        return _get(item, "name").value
    ends, ports = _get(item, "ends"), _get(item, "ports")
    return f"{ends.value[0].value}.{expand(ports.value[0].value)[0]}"


def new_identity(block: str, item: Union[str, Sequence[Tuple[str, Any]]]) -> str:
    """The name :func:`identity` gives an item an :class:`Insert` adds."""
    if block in ("alone", "open"):
        return item
    fields = dict(item)
    if block == "links":
        return fields["a"]
    if block == "rails":
        return fields["name"]
    return f"{fields['ends'][0]}.{expand(collapse(list(fields['ports']))[0])[0]}"


class Composed:
    """A source's composed system block: its block nodes and items by identity."""

    def __init__(self, text: str):
        self.text = text
        self.starts = [0]
        for n, ch in enumerate(text):
            if ch == "\n":
                self.starts.append(n + 1)
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        self.system = next(v for k, v in root.value if k.value == "system")
        self.keys: Dict[str, Tuple[yaml.Node, yaml.Node]] = {}
        self.items: Dict[Tuple[str, str], yaml.Node] = {}
        self.edges: List[yaml.Node] = []
        for key, value in self.system.value:
            self.keys[key.value] = (key, value)
            if key.value in BLOCKS:
                for item in value.value:
                    self.items[(key.value, identity(key.value, item))] = item
            elif key.value == "external":
                edges = _get(value, "edges")
                if edges is not None:
                    self.edges = [edge for _, edge in edges.value]

    def item(self, block: str, name: str) -> yaml.Node:
        try:
            return self.items[(block, name)]
        except KeyError:
            raise ValueError(f"{block} has no item {name!r}") from None

    def line_span(self, row: int) -> Tuple[int, int]:
        """The 0-based line ``row``, from its first byte to past its newline."""
        end = self.starts[row + 1] if row + 1 < len(self.starts) else len(self.text)
        return self.starts[row], end

    def names(self) -> Dict[Tuple[str, int], str]:
        """Each item's identity by (block, 1-based line)."""
        return {(block, node.start_mark.line + 1): name
                for (block, name), node in self.items.items()}


def item_names(text: str) -> Dict[Tuple[str, int], str]:
    """The identity of each item in ``text`` by (block, 1-based line)."""
    return Composed(text).names()


# ----------------------------------------------------------------------
# Writing values
# ----------------------------------------------------------------------
def _flow_list(items: Sequence[str]) -> str:
    return "[" + ", ".join(scalar(i) for i in items) + "]"


def ref_list(refs: Sequence[str]) -> List[str]:
    """Refs as written: consecutive runs on one instance collapse into a range."""
    out: List[str] = []
    run: List[PortRef] = []

    def flush():
        if run:
            out.extend(f"{run[0].instance}.{name}" for name in collapse([r.port for r in run]))
            run.clear()

    for text in refs:
        ref = parse_ref(text)
        if run and ref.instance != run[0].instance:
            flush()
        run.append(ref)
    flush()
    return out


def _value_text(block: str, key: str, value: Any) -> str:
    if key == "at":
        return f"[{number(quantized(value[0]))}, {number(quantized(value[1]))}]"
    if key == "rotation":
        return str(int(value))
    if key == "ports":
        return _flow_list(ref_list(value) if block == "rails" else collapse(list(value)))
    if key == "ends":
        return _flow_list(value)
    return scalar(value)


def item_line(block: str, item: Union[str, Sequence[Tuple[str, Any]]]) -> str:
    """The text after ``- `` for a new item."""
    if block in ("alone", "open"):
        return scalar(item)
    fields = dict(item)
    if block == "links" and fields.get("net") == "_".join(fields["a"].split(".", 1)):
        fields.pop("net")  # the default needs no field
    order = ITEM_KEYS[block]
    unknown = set(fields) - set(order)
    if unknown:
        raise ValueError(f"{block}: no field {sorted(unknown)[0]!r}")
    return "{" + ", ".join(f"{k}: {_value_text(block, k, fields[k])}" for k in order
                           if fields.get(k) is not None) + "}"


def _decimal(node: yaml.Node) -> Optional[Decimal]:
    try:
        return quantized(Decimal(node.value))
    except (InvalidOperation, TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# The patch
# ----------------------------------------------------------------------
class _Spans:
    def __init__(self):
        self.edits: List[Tuple[int, int, int, str]] = []

    def put(self, start: int, end: int, new: str) -> None:
        self.edits.append((start, end, len(self.edits), new))

    def apply(self, text: str) -> str:
        """Every span, last first; inserts at one index land in the order made."""
        spans = sorted(self.edits, key=lambda e: (e[0], e[2]))
        out, at = [], 0
        for start, end, _, new in spans:
            if start < at:
                raise ValueError("two edits touch the same bytes")
            out.append(text[at:start])
            out.append(new)
            at = end
        out.append(text[at:])
        return "".join(out)


class _Patcher:
    def __init__(self, text: str):
        self.text = text
        self.doc = Composed(text)
        self.spans = _Spans()
        self.changes: List[Change] = []
        self.deleted: Dict[str, set] = {}
        self.touched: set = set()
        #: Inserts per block, in the order made: (identity, line text).
        self.inserts: Dict[str, List[Tuple[str, str]]] = {}

    def change(self, block, item, field, old, new, line):
        self.changes.append(Change(block, item, field, old, new, line))

    # --- replace ------------------------------------------------------------
    def replace(self, edit: Replace) -> None:
        if edit.field not in FIELDS.get(edit.block, ()):
            raise ValueError(f"{edit.block}: field {edit.field!r} cannot be replaced")
        item = self.doc.item(edit.block, edit.item)
        self.touched.add((edit.block, edit.item))
        line = item.start_mark.line + 1
        node = _get(item, edit.field)
        if edit.field == "at":
            moved = False
            for i, value in enumerate(edit.value):
                if _decimal(node.value[i]) != quantized(value):
                    self.token(node.value[i], number(quantized(value)))
                    moved = True
            if moved:
                self.change(edit.block, edit.item, "at", self.written(node),
                            _value_text("dies", "at", edit.value), line)
            return
        if edit.field == "rotation":
            turned = int(edit.value)
            if turned not in (0, 90, 180, 270):
                raise ValueError(f"rotation {edit.value!r} is not 0, 90, 180 or 270")
            if node is not None and int(node.value) != turned:
                self.token(node, str(turned))
                self.change("dies", edit.item, "rotation", node.value, str(turned), line)
            elif node is None and turned != 0:
                at = _get(item, "at")
                self.spans.put(at.end_mark.index, at.end_mark.index, f", rotation: {turned}")
                self.change("dies", edit.item, "rotation", None, str(turned), line)
            return
        if edit.value is None and (edit.field in ITEM_KEYS[edit.block][:1]
                                   or edit.field == "ports"):
            raise ValueError(f"{edit.block}: field {edit.field!r} is required")
        new = None if edit.value is None else _value_text(edit.block, edit.field, edit.value)
        old = None if node is None else self.written(node)
        if edit.field == "ports" and node is not None:
            current = [n.value for n in node.value]
            wanted = (ref_list(edit.value) if edit.block == "rails"
                      else collapse(list(edit.value)))
            if self._expanded(current, edit.block) == self._expanded(wanted, edit.block):
                return
        elif node is not None and edit.value is not None and node.value == edit.value:
            return
        if node is None and edit.value is None:
            return
        if edit.value is None:
            pairs = item.value
            k = next(i for i, (key, _) in enumerate(pairs) if key.value == edit.field)
            self.spans.put(pairs[k - 1][1].end_mark.index, pairs[k][1].end_mark.index, "")
        elif node is not None:
            self.token(node, new)
        else:
            order = ITEM_KEYS[edit.block]
            ahead = set(order[:order.index(edit.field)])
            after = [v for k, v in item.value if k.value in ahead][-1]
            self.spans.put(after.end_mark.index, after.end_mark.index, f", {edit.field}: {new}")
        self.change(edit.block, edit.item, edit.field, old, new, line)
        if edit.block == "rails" and edit.field == "name":
            for edge in self.doc.edges:
                for entry in edge.value:
                    if entry.value == edit.item:
                        self.token(entry, new)

    @staticmethod
    def _expanded(written: Sequence[str], block: str) -> List[str]:
        out: List[str] = []
        for text in written:
            if block == "rails":
                ref = parse_ref(text)
                out.extend(f"{ref.instance}.{p}" for p in expand(ref.port))
            else:
                out.extend(expand(text))
        return out

    def token(self, node: yaml.Node, new: str) -> None:
        self.spans.put(node.start_mark.index, node.end_mark.index, new)

    def written(self, node: yaml.Node) -> str:
        return self.text[node.start_mark.index:node.end_mark.index]

    # --- delete and insert ----------------------------------------------------
    def delete(self, edit: Delete) -> None:
        item = self.doc.item(edit.block, edit.item)
        row = item.start_mark.line
        self.spans.put(*self.doc.line_span(row), "")
        self.deleted.setdefault(edit.block, set()).add(edit.item)
        written = self.text[slice(*self.doc.line_span(row))].rstrip("\n")
        self.change(edit.block, edit.item, None, written, None, row + 1)

    def insert(self, edit: Insert) -> None:
        if edit.block not in BLOCKS or edit.block == "dies":
            raise ValueError(f"an item cannot be inserted into {edit.block!r}")
        name = new_identity(edit.block, edit.item)
        self.inserts.setdefault(edit.block, []).append((name, item_line(edit.block, edit.item)))

    def place_inserts(self) -> None:
        for block, lines in self.inserts.items():
            if block in self.doc.keys:
                seq = self.doc.keys[block][1]
                last = seq.value[-1]
                indent = self.text[self.doc.starts[last.start_mark.line]:
                                   last.start_mark.index - 2]
                row = last.start_mark.line
                head = ""
            else:
                indent = "    "
                previous = [k for k in SYSTEM_KEYS[:SYSTEM_KEYS.index(block)] if k in self.doc.keys]
                value = self.doc.keys[previous[-1]][1]
                # The new block goes after the last line of the block before it,
                # so a comment above the next key stays with that key.
                block_list = isinstance(value, yaml.SequenceNode) and not value.flow_style
                row = (value.value[-1] if block_list else value).start_mark.line
                head = f"  {block}:\n"
            _, at = self.doc.line_span(row)
            body = head + "".join(f"{indent}- {line}\n" for _, line in lines)
            if at == len(self.text) and not self.text.endswith("\n"):
                body = "\n" + body[:-1]
            self.spans.put(at, at, body)
            for name, line in lines:
                self.change(block, name, None, None, f"{indent}- {line}", row + 1)

    def drop_empty_blocks(self) -> None:
        for block, gone in self.deleted.items():
            seq = self.doc.keys[block][1]
            if len(gone) == len(seq.value) and not self.inserts.get(block):
                key = self.doc.keys[block][0]
                self.spans.put(*self.doc.line_span(key.start_mark.line), "")

    def check(self) -> None:
        for block, gone in self.deleted.items():
            both = {(block, name) for name in gone} & self.touched
            if both:
                raise ValueError(f"{sorted(both)[0]} is both replaced and deleted")
        for block, lines in self.inserts.items():
            names = [name for name, _ in lines]
            kept = {n for b, n in self.doc.items if b == block} - self.deleted.get(block, set())
            twice = sorted({n for n in names if names.count(n) > 1} | (set(names) & kept))
            if twice:
                raise ValueError(f"{block} would hold two items {twice[0]!r}")


def patch_system(data: bytes, edits: Sequence[Edit], *, name: str = "system.chiplet") -> Patch:
    """``data`` with ``edits`` applied, and nothing else changed."""
    sha = hashlib.sha256(data).hexdigest()
    text, crlf = decode(data)
    source, findings = check_text(text, name)
    if findings or source is None:
        raise not_a_source(findings)
    patcher = _Patcher(text)
    for edit in edits:
        if isinstance(edit, Replace):
            patcher.replace(edit)
        elif isinstance(edit, Delete):
            patcher.delete(edit)
        elif isinstance(edit, Insert):
            patcher.insert(edit)
        else:
            raise TypeError(f"not an edit: {edit!r}")
    patcher.check()
    patcher.place_inserts()
    patcher.drop_empty_blocks()
    new = patcher.spans.apply(text)
    if new == text:
        return Patch(data, "", sha, ())
    again, findings = check_text(new, name)
    if findings or again is None:
        raise SourceError(name, findings or [Finding(0, "the patched file is not a source")])
    diff = "".join(difflib.unified_diff(text.splitlines(True), new.splitlines(True),
                                        f"a/{name}", f"b/{name}"))
    changes = tuple(sorted(patcher.changes, key=lambda c: c.line))
    return Patch(_encode(new, crlf), diff, sha, changes)
