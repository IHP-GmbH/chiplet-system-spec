# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Reading a source ``.chiplet``: the ``system`` block as typed items, each with
the line it sits on, and the tier 0 checks that need nothing but the file.

The bytes are read twice, and both reads must agree before anything else runs.
The vendored reader (``allow_source=True``) holds the document level: the line
break rules, the version, ``_metadata.finalize_required``, no derived block.
PyYAML's composer then gives every node its marks, which is what the line rules
(the spec, "System (format 1.1)") are stated on: one item per line, keys in
the schema's order, a string bare only when the bare-string rule allows it. A
rule that fails is a
:class:`Finding` with its line, never an exception mid-walk, so one ``check``
reports every finding at once. :func:`read_source` raises :class:`SourceError`
carrying all of them.

Tier 0 also resolves names inside the file: instance ids in refs and bus ends,
rail names on external edges, ranges, and a port ref written twice. What a port
IS (that it exists, its direction, its pads) is tier 1, which reads the dies.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import yaml

from .._cfio import cfio
from ..errors import SystemSpecError
from ..model import PortRef
from .scalars import RETYPED_WORDS, bare, quote

#: The keys of a source document, in the order a writer emits them.
ROOT_KEYS = ("format_version", "_metadata", "assembly", "interposer", "flow", "system")
#: The keys of the system block, in their one order.
SYSTEM_KEYS = ("carrier", "registry", "dies", "buses", "links", "rails", "alone",
               "open", "external", "fill_keepout")
#: Each item's keys in their one order, and which of them are required.
ITEM_KEYS = {
    "carrier": ("technology", "top_cell", "size"),
    "dies": ("id", "die", "at", "rotation", "method", "thickness"),
    "buses": ("ends", "ports", "net_prefix", "net_suffix"),
    "links": ("a", "b", "net"),
    "rails": ("name", "class", "volts", "ports"),
}
REQUIRED_KEYS = {
    "carrier": ("technology", "top_cell", "size"),
    "dies": ("id", "die", "at", "method"),
    "buses": ("ends", "ports"),
    "links": ("a", "b"),
    "rails": ("name", "class", "ports"),
}
EDGES = ("north", "south", "west", "east")
ROTATIONS = (0, 90, 180, 270)
RAIL_CLASSES = ("power", "ground")
INSTANCE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
#: Any bracket holding ``..``; a well-formed range is the stricter RANGE.
_RANGE_LIKE = re.compile(r"\[([^\[\]]*)\.\.([^\[\]]*)\]")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")

_STR_TAG = "tag:yaml.org,2002:str"
#: The key paths whose value is a string (a list adds no step). A bare scalar
#: there that YAML reads as something else is refused with its quoted line.
_STRING_PATHS = frozenset({
    ("format_version",), ("_metadata", "finalizer"), ("assembly", "name"),
    ("assembly", "description"), ("assembly", "units"), ("interposer", "adapter"),
    ("system", "carrier", "technology"), ("system", "carrier", "top_cell"),
    ("system", "registry"), ("system", "dies", "id"), ("system", "dies", "die"),
    ("system", "dies", "method"), ("system", "buses", "ends"), ("system", "buses", "ports"),
    ("system", "buses", "net_prefix"), ("system", "buses", "net_suffix"),
    ("system", "links", "a"), ("system", "links", "b"), ("system", "links", "net"),
    ("system", "rails", "name"), ("system", "rails", "class"), ("system", "rails", "ports"),
    ("system", "alone"), ("system", "open"), ("system", "external", "profile"),
})
#: What YAML makes of a bare scalar that is not a string, as a finding words it.
_READS_AS = {"bool": "a bool", "int": "a number", "float": "a number", "null": "a null",
             "timestamp": "a date"}


# ----------------------------------------------------------------------
# Findings
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    """One defect, on one line of the file (0: the document as a whole)."""

    line: int
    message: str
    tier: int = 0
    #: A note reports and does not refuse (tier 1's "thickness not given").
    note: bool = False

    def render(self, path: Union[str, Path]) -> str:
        return f"{path}:{self.line}: {'note: ' if self.note else ''}{self.message}"


class SourceError(SystemSpecError):
    """A source the reader refuses, with every finding that refuses it."""

    def __init__(self, path: Union[str, Path], findings: Sequence[Finding]):
        self.path = str(path)
        self.findings = tuple(findings)
        self.line = self.findings[0].line if self.findings else 0
        super().__init__("\n".join(f.render(self.path) for f in self.findings))


# ----------------------------------------------------------------------
# Items
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Carrier:
    technology: str
    top_cell: str
    size: Tuple[float, float]
    line: int


@dataclass(frozen=True)
class DieItem:
    id: str
    die: str
    at: Tuple[float, float]
    method: str
    rotation: int
    thickness: Optional[float]
    line: int


@dataclass(frozen=True)
class BusItem:
    ends: Tuple[str, str]
    ports: Tuple[str, ...]
    net_prefix: str
    net_suffix: str
    line: int


@dataclass(frozen=True)
class LinkItem:
    a: PortRef
    b: PortRef
    net: Optional[str]
    line: int


@dataclass(frozen=True)
class RailItem:
    name: str
    net_class: str
    ports: Tuple[PortRef, ...]
    line: int
    #: 0-based column of the ``]`` closing ``ports`` on the line, so a port can
    #: be added to the line as written.
    ports_close: int = -1
    #: The rail's nominal supply, a power rail's only (format 1.2); None when
    #: the file declares none.
    volts: Optional[float] = None


@dataclass(frozen=True)
class RefItem:
    """One ``alone`` or ``open`` line: a port ref, expanded if it holds a range."""

    refs: Tuple[PortRef, ...]
    written: str
    line: int


@dataclass(frozen=True)
class EdgeItem:
    """One external edge: port refs (expanded) and rail names, in pad-row order."""

    edge: str
    entries: Tuple[Union[PortRef, str], ...]
    line: int


@dataclass(frozen=True)
class External:
    profile: str
    edges: Tuple[EdgeItem, ...]
    line: int


@dataclass(frozen=True)
class FillKeepout:
    halo_um: Tuple[Tuple[str, float], ...]
    line: int


@dataclass(frozen=True)
class Source:
    """A source document read and checked at tier 0. Lines are 1-based."""

    path: str
    text: str
    data: Dict[str, Any]
    carrier: Optional[Carrier]
    dies: Tuple[DieItem, ...]
    registry: Optional[str] = None
    buses: Tuple[BusItem, ...] = ()
    links: Tuple[LinkItem, ...] = ()
    rails: Tuple[RailItem, ...] = ()
    alone: Tuple[RefItem, ...] = ()
    open: Tuple[RefItem, ...] = ()
    external: Optional[External] = None
    fill_keepout: Optional[FillKeepout] = None
    #: The line of each system key that is present.
    key_lines: Dict[str, int] = field(default_factory=dict)
    #: Tier 0's notes, which report and do not refuse: the item keys of a
    #: newer minor that this reader skipped.
    notes: Tuple[Finding, ...] = ()

    def die(self, instance: str) -> DieItem:
        for item in self.dies:
            if item.id == instance:
                return item
        raise KeyError(instance)

    @property
    def folder(self) -> Path:
        """The folder the file sits in: absolute, links kept as written."""
        return Path(os.path.abspath(self.path)).parent


# ----------------------------------------------------------------------
# Names
# ----------------------------------------------------------------------
def expand(name: str) -> List[str]:
    """``name`` with its one ``[a..b]`` range expanded, in the written order.

    Raises ValueError naming what is wrong: two ranges, a bound that is not a
    decimal without leading zeros. A name with no ``..`` in brackets is itself.
    """
    found = list(_RANGE_LIKE.finditer(name))
    if not found:
        return [name]
    if len(found) > 1:
        raise ValueError(f"{name!r} holds {len(found)} ranges; a name holds at most one")
    match = found[0]
    lo, hi = match.group(1), match.group(2)
    for bound in (lo, hi):
        if not _DECIMAL.match(bound):
            raise ValueError(
                f"{name!r}: range bound {bound!r} is not a decimal without leading zeros")
    a, b = int(lo), int(hi)
    step = 1 if a <= b else -1
    head, tail = name[:match.start()], name[match.end():]
    return [f"{head}{i}{tail}" for i in range(a, b + step, step)]


def parse_ref(text: str) -> PortRef:
    """``<instance>.<port>`` as a PortRef, or ValueError."""
    instance, dot, port = text.partition(".")
    if not dot or not port:
        raise ValueError(f"{text!r} is not a port reference <instance>.<port>")
    if not INSTANCE_ID.match(instance):
        raise ValueError(f"{text!r}: instance id {instance!r} is not [A-Za-z][A-Za-z0-9_]*")
    return PortRef(instance=instance, port=port)


def ref_text(ref: PortRef) -> str:
    return f"{ref.instance}.{ref.port}"


def check_relative_path(value: str) -> Optional[str]:
    """Why ``value`` breaks the path rule, or None."""
    if "${" in value:
        return f"path {value!r} holds a variable; paths are relative to the file"
    if value.startswith("/") or re.match(r"[A-Za-z]:[\\/]", value) or value.startswith("\\"):
        return f"path {value!r} is absolute; paths are relative to the file"
    if "\\" in value:
        return f"path {value!r} uses a backslash; write '/'"
    if ".." in PurePosixPath(value).parts:
        return f"path {value!r} has a '..' segment; paths stay inside the file's folder"
    return None


# ----------------------------------------------------------------------
# The walk
# ----------------------------------------------------------------------
def _line(node: yaml.Node) -> int:
    return node.start_mark.line + 1


def _newer_than_reader(format_version: str) -> Optional[str]:
    """The version this reader implements, when ``format_version`` (already
    accepted by the vendored reader) is a higher minor of its major; else None."""
    major, minor = (int(part) for part in format_version.split("."))
    for accepted in cfio.ACCEPTED_FORMAT_VERSIONS:
        floor_major, floor_minor = (int(part) for part in accepted.split("."))
        if floor_major == major and minor > floor_minor:
            return accepted
    return None


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class _Reader:
    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text
        self.findings: List[Finding] = []
        self.instances: Dict[str, int] = {}
        #: Every port ref written, expanded, with the line that wrote it first.
        self.seen_refs: Dict[Tuple[str, str], int] = {}
        #: Bare scalars at a string path that YAML reads as something else.
        self.retyped: set = set()
        #: The version this reader implements, when the source is stamped with
        #: a higher minor of it (:func:`_newer_than_reader`), else None.
        self.reads_as: Optional[str] = None

    def finding(self, line: int, message: str, *, note: bool = False) -> None:
        self.findings.append(Finding(line, message, note=note))

    # --- node helpers -----------------------------------------------------
    def mapping(self, node: yaml.Node, where: str) -> Optional[List[Tuple[str, yaml.Node]]]:
        if not isinstance(node, yaml.MappingNode):
            self.finding(_line(node), f"{where} must be a mapping")
            return None
        pairs, seen = [], set()
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode):
                self.finding(_line(key), f"{where}: a key must be a plain name")
                continue
            if key.value in seen:
                self.finding(_line(key), f"{where}: key {key.value!r} is written twice")
                continue
            seen.add(key.value)
            pairs.append((key.value, value))
        return pairs

    def value(self, node: yaml.Node) -> Any:
        """The Python value of ``node``, through the same safe constructor."""
        loader = yaml.SafeLoader("")
        try:
            return loader.construct_document(node)
        finally:
            loader.dispose()

    def string(self, node: yaml.Node, where: str, *, empty: bool = False) -> Optional[str]:
        value = None
        if isinstance(node, yaml.ScalarNode):
            # A bare value the quoting rule refused reads as the string it spells,
            # so one unquoted id does not cascade into findings on every line naming it.
            value = node.value if id(node) in self.retyped else self.value(node)
        if not isinstance(value, str) or (not empty and not value):
            self.finding(_line(node), f"{where} must be a {'' if empty else 'non-empty '}string")
            return None
        return value

    def number(self, node: yaml.Node, where: str) -> Optional[float]:
        value = self.value(node) if isinstance(node, yaml.ScalarNode) else None
        if not _is_number(value):
            self.finding(_line(node), f"{where} must be a number")
            return None
        return value

    def pair(self, node: yaml.Node, where: str) -> Optional[Tuple[float, float]]:
        if not isinstance(node, yaml.SequenceNode) or len(node.value) != 2:
            self.finding(_line(node), f"{where} must be a list of two numbers")
            return None
        x = self.number(node.value[0], where)
        y = self.number(node.value[1], where)
        return None if x is None or y is None else (x, y)

    def strings(self, node: yaml.Node, where: str) -> Optional[List[Tuple[str, int]]]:
        if not isinstance(node, yaml.SequenceNode) or not node.value:
            self.finding(_line(node), f"{where} must be a non-empty list of strings")
            return None
        out = []
        for entry in node.value:
            text = self.string(entry, where)
            if text is not None:
                out.append((text, _line(entry)))
        return out

    def unknown_key(self, line: int, block: str, key: str, allowed: Sequence[str]) -> None:
        """A key ``block`` does not have: refused, or, in a source above this
        reader's minor, reported and skipped, since a minor's additions are
        ones a reader may skip (VERSION_POLICY)."""
        if self.reads_as is not None:
            self.finding(line, f"{block}: key {key!r} is not in format {self.reads_as}, which "
                               "this reader implements; skipped", note=True)
        else:
            self.finding(line, f"{block}: unknown key {key!r} (keys: {', '.join(allowed)})")

    def keys_in_order(self, pairs, block: str, line: int) -> Dict[str, yaml.Node]:
        allowed = ITEM_KEYS[block]
        known = [k for k, _ in pairs if k in allowed]
        for key, value in pairs:
            if key not in allowed:
                self.unknown_key(_line(value), block, key, allowed)
        if known != sorted(known, key=allowed.index):
            self.finding(line, f"{block}: keys are written in the order {', '.join(allowed)}")
        got = dict(pairs)
        for key in REQUIRED_KEYS[block]:
            if key not in got:
                self.finding(line, f"{block}: missing required key {key!r}")
        return got

    def one_line_item(self, node: yaml.Node, block: str, flow_mapping: bool) -> bool:
        if flow_mapping and not (isinstance(node, yaml.MappingNode) and node.flow_style):
            self.finding(_line(node), f"{block}: each item is one line holding a flow mapping {{...}}")
            return False
        if node.start_mark.line != node.end_mark.line:
            # Reported, then read anyway: the item's names still resolve, so one
            # broken line does not cascade into findings on every line naming it.
            self.finding(_line(node), f"{block}: each item is one line; this one spans "
                                      f"lines {_line(node)} to {node.end_mark.line + 1}")
        return True

    def block_sequence(self, node: yaml.Node, block: str) -> List[yaml.Node]:
        if not isinstance(node, yaml.SequenceNode):
            self.finding(_line(node), f"{block} must be a list, one item per line")
            return []
        if node.flow_style:
            self.finding(_line(node), f"{block} is a block list, one '- ' item per line")
            return []
        return list(node.value)

    # --- refs -------------------------------------------------------------
    def refs(self, text: str, line: int, where: str, *, claim: bool = True) -> List[PortRef]:
        try:
            first = parse_ref(text)
            names = expand(first.port)
        except ValueError as exc:
            self.finding(line, f"{where}: {exc}")
            return []
        if first.instance not in self.instances:
            self.finding(line, f"{where}: {text!r} names no instance in dies "
                               f"(dies: {', '.join(self.instances) or 'none'})")
            return []
        out = [PortRef(first.instance, name) for name in names]
        if claim:
            for ref in out:
                self.claim(ref, line)
        return out

    def claim(self, ref: PortRef, line: int) -> None:
        key = (ref.instance, ref.port)
        if key in self.seen_refs:
            self.finding(line, f"port {ref_text(ref)} is already accounted for on line "
                               f"{self.seen_refs[key]}; each port appears exactly once")
        else:
            self.seen_refs[key] = line

    # --- quoting ------------------------------------------------------------
    def quoting(self, node: yaml.Node, path: Tuple[str, ...],
                bad: List[Tuple[yaml.ScalarNode, Tuple[str, ...]]]) -> None:
        """The bare-string rule: a value may go bare only when :func:`bare` allows
        it, since both YAML reader families read every such word as one string.
        Keys stay bare. Every scalar that breaks the rule is collected in ``bad``."""
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode):
                    self.quoting(value, path + (key.value,), bad)
        elif isinstance(node, yaml.SequenceNode):
            for entry in node.value:
                self.quoting(entry, path, bad)
        elif isinstance(node, yaml.ScalarNode) and node.style is None:
            if node.tag == _STR_TAG:
                if not bare(node.value):
                    bad.append((node, path))
            elif path in _STRING_PATHS or path[:3] == ("system", "external", "edges"):
                self.retyped.add(id(node))
                bad.append((node, path))

    def quoting_findings(self, bad: List[Tuple[yaml.ScalarNode, Tuple[str, ...]]]) -> str:
        """One finding per scalar in ``bad``, each giving its line with every
        refused scalar on it quoted; the text with those lines so written."""
        lines = self.text.splitlines(True)
        fixes: Dict[int, List[yaml.ScalarNode]] = {}
        for node, _ in bad:
            if node.start_mark.line == node.end_mark.line:
                fixes.setdefault(node.start_mark.line, []).append(node)

        def fixed(row: int) -> str:
            text = lines[row]
            for each in sorted(fixes[row], key=lambda n: -n.start_mark.column):
                text = text[:each.start_mark.column] + quote(each.value) + text[each.end_mark.column:]
            return text

        for node, path in bad:
            where = ".".join(path[1:] if path[0] == "system" else path)
            if node.tag != _STR_TAG:
                kind = _READS_AS.get(node.tag.rsplit(":", 1)[-1], "something else")
                why = f"reads as {kind}, not a string"
            elif node.value.lower() in RETYPED_WORDS:
                why = "is a word YAML 1.1 readers take for a bool or a null"
            else:
                why = "is not a bare word ([A-Za-z_][A-Za-z0-9_./-]*)"
            row = node.start_mark.line
            if row in fixes:
                write = f"write the line as: {fixed(row).strip()}"
            else:
                write = f"write {quote(node.value)}"
            self.finding(_line(node), f"{where}: bare {node.value!r} {why}; {write}")
        for row in fixes:
            lines[row] = fixed(row)
        return "".join(lines)

    # --- blocks -------------------------------------------------------------
    def read(self) -> Optional[Source]:
        root = yaml.compose(self.text, Loader=yaml.SafeLoader)
        # The bare-string rule runs first, and the vendored reader then reads the
        # text with every refused scalar quoted, so what it reports is not an echo
        # of a bare word (``id: no`` is not a die without an id).
        bad: List[Tuple[yaml.ScalarNode, Tuple[str, ...]]] = []
        if isinstance(root, yaml.MappingNode):
            for key, value in root.value:
                if isinstance(key, yaml.ScalarNode) and key.value != "flow":
                    self.quoting(value, (key.value,), bad)
        quoted = self.quoting_findings(bad)
        try:
            data = cfio.loads(quoted, allow_source=True)
        except cfio.ChipletFormatError as exc:
            self.finding(0, str(exc))
            return None
        if "system" not in data:
            self.finding(0, "this is an assembly, not a source: it has no top-level 'system' block")
            return None
        self.reads_as = _newer_than_reader(data["format_version"])
        pairs = self.mapping(root, "the document") or []
        keys = [k for k, _ in pairs]
        for key, value in pairs:
            if key not in ROOT_KEYS:
                self.finding(_line(value) - (1 if isinstance(value, yaml.CollectionNode) else 0),
                             f"a source carries only {', '.join(ROOT_KEYS)}; {key!r} is not one")
        system_node = dict(pairs)["system"]
        items = self.mapping(system_node, "system") or []
        present = [k for k, _ in items]
        key_lines = {}
        for key, value in items:
            if key not in SYSTEM_KEYS:
                self.unknown_key(_line(value), "system", key, SYSTEM_KEYS)
        ordered = [k for k in present if k in SYSTEM_KEYS]
        if ordered != sorted(ordered, key=SYSTEM_KEYS.index):
            self.finding(_line(system_node), "system: keys are written in the order "
                                             + ", ".join(SYSTEM_KEYS))
        for key, value in items:
            line = _line(value)
            # A block value starts on the line after its key; the key's own line is
            # where an author looks for it.
            if isinstance(value, yaml.CollectionNode) and not value.flow_style:
                line = self._key_line(system_node, key)
            key_lines[key] = line
        nodes = dict(items)
        carrier = self.carrier(nodes["carrier"]) if "carrier" in nodes else None
        if "carrier" not in nodes:
            self.finding(_line(system_node), "system: missing required key 'carrier'")
        registry = None
        if "registry" in nodes:
            registry = self.string(nodes["registry"], "system.registry")
            if registry is not None:
                why = check_relative_path(registry)
                if why:
                    self.finding(_line(nodes["registry"]), f"system.registry: {why}")
        dies = self.dies(nodes["dies"]) if "dies" in nodes else ()
        if "dies" not in nodes:
            self.finding(_line(system_node), "system: missing required key 'dies'")
        rails = self.rails(nodes["rails"]) if "rails" in nodes else ()
        buses = self.buses(nodes["buses"]) if "buses" in nodes else ()
        links = self.links(nodes["links"]) if "links" in nodes else ()
        alone = self.ref_lines(nodes["alone"], "alone") if "alone" in nodes else ()
        open_ = self.ref_lines(nodes["open"], "open") if "open" in nodes else ()
        external = (self.external(nodes["external"], {r.name for r in rails}, key_lines["external"])
                    if "external" in nodes else None)
        fill = self.fill_keepout(nodes["fill_keepout"]) if "fill_keepout" in nodes else None
        return Source(path=self.path, text=self.text, data=data, carrier=carrier,
                      dies=tuple(dies), registry=registry, buses=tuple(buses),
                      links=tuple(links), rails=tuple(rails), alone=tuple(alone),
                      open=tuple(open_), external=external, fill_keepout=fill,
                      key_lines=key_lines)

    def _key_line(self, mapping: yaml.MappingNode, key: str) -> int:
        for k, _ in mapping.value:
            if isinstance(k, yaml.ScalarNode) and k.value == key:
                return _line(k)
        return _line(mapping)

    def carrier(self, node: yaml.Node) -> Optional[Carrier]:
        pairs = self.mapping(node, "system.carrier")
        if pairs is None:
            return None
        got = self.keys_in_order(pairs, "carrier", _line(node))
        tech = self.string(got["technology"], "carrier.technology") if "technology" in got else None
        top = self.string(got["top_cell"], "carrier.top_cell") if "top_cell" in got else None
        size = self.pair(got["size"], "carrier.size") if "size" in got else None
        if size is not None and min(size) <= 0:
            self.finding(_line(got["size"]), "carrier.size: width and height are greater than 0")
            size = None
        if None in (tech, top, size):
            return None
        return Carrier(tech, top, size, _line(node))

    def dies(self, node: yaml.Node) -> List[DieItem]:
        out = []
        for item in self.block_sequence(node, "dies"):
            if not self.one_line_item(item, "dies", flow_mapping=True):
                continue
            pairs = self.mapping(item, "dies")
            if pairs is None:
                continue
            line = _line(item)
            got = self.keys_in_order(pairs, "dies", line)
            ident = self.string(got["id"], "dies.id") if "id" in got else None
            if ident is not None and not INSTANCE_ID.match(ident):
                self.finding(line, f"dies: id {ident!r} is not [A-Za-z][A-Za-z0-9_]*")
                ident = None
            if ident is not None and ident in self.instances:
                self.finding(line, f"dies: id {ident!r} is already used on line "
                                   f"{self.instances[ident]}")
                ident = None
            if ident is not None:
                self.instances[ident] = line
            folder = self.string(got["die"], "dies.die") if "die" in got else None
            if folder is not None:
                why = check_relative_path(folder)
                if why:
                    self.finding(line, f"dies.die: {why}")
                    folder = None
            at = self.pair(got["at"], "dies.at") if "at" in got else None
            method = self.string(got["method"], "dies.method") if "method" in got else None
            rotation = 0
            if "rotation" in got:
                value = self.value(got["rotation"])
                if not _is_number(value) or value not in ROTATIONS:
                    self.finding(line, f"dies.rotation: {value!r} is not one of 0, 90, 180, 270")
                    rotation = None
                else:
                    rotation = int(value)
            thickness, bad = None, False
            if "thickness" in got:
                thickness = self.number(got["thickness"], "dies.thickness")
                bad = thickness is None
                if thickness is not None and thickness < 0:
                    self.finding(line, "dies.thickness is 0 or more")
                    bad = True
            if bad or None in (ident, folder, at, method, rotation):
                continue
            out.append(DieItem(ident, folder, at, method, rotation, thickness, line))
        return out

    def buses(self, node: yaml.Node) -> List[BusItem]:
        out = []
        for item in self.block_sequence(node, "buses"):
            if not self.one_line_item(item, "buses", flow_mapping=True):
                continue
            pairs = self.mapping(item, "buses")
            if pairs is None:
                continue
            line = _line(item)
            got = self.keys_in_order(pairs, "buses", line)
            ends = self.strings(got["ends"], "buses.ends") if "ends" in got else None
            if ends is not None:
                names = [e for e, _ in ends]
                if len(names) != 2 or names[0] == names[1]:
                    self.finding(line, "buses.ends names two different instances")
                    ends = None
                else:
                    unknown = [e for e in names if e not in self.instances]
                    if unknown:
                        self.finding(line, f"buses.ends: {', '.join(map(repr, unknown))} "
                                           "names no instance in dies")
                        ends = None
            ports: List[str] = []
            for text, _ in (self.strings(got["ports"], "buses.ports") or []) if "ports" in got else []:
                if "." in _RANGE_LIKE.sub("", text):
                    self.finding(line, f"buses.ports: {text!r} is a port name, not a ref; "
                                       "the bus's ends name the instances")
                    continue
                try:
                    ports.extend(expand(text))
                except ValueError as exc:
                    self.finding(line, f"buses.ports: {exc}")
            affix = {}
            for key in ("net_prefix", "net_suffix"):
                affix[key] = ""
                if key in got:
                    value = self.string(got[key], f"buses.{key}", empty=True)
                    affix[key] = value if value is not None else ""
            if ends is None or not ports:
                continue
            seen = set()
            for port in ports:
                if port in seen:
                    self.finding(line, f"buses.ports: {port!r} is written twice")
                seen.add(port)
                for end in names:
                    self.claim(PortRef(end, port), line)
            out.append(BusItem((names[0], names[1]), tuple(dict.fromkeys(ports)),
                               affix["net_prefix"], affix["net_suffix"], line))
        return out

    def links(self, node: yaml.Node) -> List[LinkItem]:
        out = []
        for item in self.block_sequence(node, "links"):
            if not self.one_line_item(item, "links", flow_mapping=True):
                continue
            pairs = self.mapping(item, "links")
            if pairs is None:
                continue
            line = _line(item)
            got = self.keys_in_order(pairs, "links", line)
            a_text = self.string(got["a"], "links.a") if "a" in got else None
            b_text = self.string(got["b"], "links.b") if "b" in got else None
            net = self.string(got["net"], "links.net") if "net" in got else None
            if a_text is None or b_text is None:
                continue
            a = self.refs(a_text, line, "links.a")
            b = self.refs(b_text, line, "links.b")
            nets: List[Optional[str]] = [None] * len(a)
            if net is not None:
                try:
                    nets = expand(net)
                except ValueError as exc:
                    self.finding(line, f"links.net: {exc}")
                    continue
            if not a or not b:
                continue
            if len(a) != len(b) or len(nets) != len(a):
                self.finding(line, f"links: a expands to {len(a)}, b to {len(b)}"
                             + (f" and net to {len(nets)}" if net is not None else "")
                             + "; a range pairs one to one")
                continue
            for x, y, n in zip(a, b, nets):
                if x.instance == y.instance:
                    self.finding(line, f"links: {ref_text(x)} and {ref_text(y)} are on one die; "
                                       "a link joins two dies")
                    continue
                out.append(LinkItem(x, y, n, line))
        return out

    def rails(self, node: yaml.Node) -> List[RailItem]:
        out: List[RailItem] = []
        names: Dict[str, int] = {}
        for item in self.block_sequence(node, "rails"):
            if not self.one_line_item(item, "rails", flow_mapping=True):
                continue
            pairs = self.mapping(item, "rails")
            if pairs is None:
                continue
            line = _line(item)
            got = self.keys_in_order(pairs, "rails", line)
            name = self.string(got["name"], "rails.name") if "name" in got else None
            if name is not None and _RANGE_LIKE.search(name):
                self.finding(line, f"rails.name {name!r}: a rail is one net, so its name holds no range")
                name = None
            if name is not None and name in names:
                self.finding(line, f"rails: {name!r} is already a rail on line {names[name]}")
                name = None
            net_class = None
            if "class" in got:
                net_class = self.value(got["class"])
                if net_class not in RAIL_CLASSES:
                    self.finding(line, f"rails.class: {net_class!r} is not power or ground")
                    net_class = None
            volts, bad = None, False
            if "volts" in got:
                volts = self.number(got["volts"], "rails.volts")
                bad = volts is None
                if volts is not None and volts <= 0:
                    self.finding(line, "rails.volts is greater than 0")
                    bad = True
                if net_class == "ground":
                    self.finding(line, "rails.volts: a ground rail is 0 by its class, so it "
                                       "declares no volts")
                    bad = True
            ports: List[PortRef] = []
            for text, _ in (self.strings(got["ports"], "rails.ports") or []) if "ports" in got else []:
                ports.extend(self.refs(text, line, "rails.ports"))
            if name is not None:
                names[name] = line
            if bad or name is None or net_class is None or not ports:
                continue
            close = got["ports"].end_mark.column - 1
            out.append(RailItem(name, net_class, tuple(ports), line, close, volts))
        return out

    def ref_lines(self, node: yaml.Node, block: str) -> List[RefItem]:
        out = []
        for item in self.block_sequence(node, block):
            if not self.one_line_item(item, block, flow_mapping=False):
                continue
            text = self.string(item, block)
            if text is None:
                continue
            refs = self.refs(text, _line(item), block)
            if refs:
                out.append(RefItem(tuple(refs), text, _line(item)))
        return out

    def external(self, node: yaml.Node, rail_names: set, line: int) -> Optional[External]:
        if not isinstance(node, yaml.MappingNode) or node.flow_style:
            self.finding(line, "external is a block mapping: profile, then edges")
            return None
        pairs = self.mapping(node, "external") or []
        keys = [k for k, _ in pairs]
        for key in keys:
            if key not in ("profile", "edges"):
                self.finding(line, f"external: unknown key {key!r} (keys: profile, edges)")
        if [k for k in keys if k in ("profile", "edges")] == ["edges", "profile"]:
            self.finding(line, "external: profile is written before edges")
        got = dict(pairs)
        profile = self.string(got["profile"], "external.profile") if "profile" in got else None
        if "profile" not in got:
            self.finding(line, "external: missing required key 'profile'")
        if profile is not None and profile.endswith(".json"):
            why = check_relative_path(profile)
            if why:
                self.finding(_line(got["profile"]), f"external.profile: {why}")
                profile = None
        if "edges" not in got:
            self.finding(line, "external: missing required key 'edges'")
            return None
        edges_node = got["edges"]
        if not isinstance(edges_node, yaml.MappingNode) or edges_node.flow_style:
            self.finding(_line(edges_node), "external.edges is a block mapping, one edge per line")
            return None
        edges: List[EdgeItem] = []
        entries_seen: Dict[str, int] = {}
        for edge, value in self.mapping(edges_node, "external.edges") or []:
            eline = _line(value)
            if edge not in EDGES:
                self.finding(eline, f"external.edges: {edge!r} is not north, south, west or east")
                continue
            if not (isinstance(value, yaml.SequenceNode) and value.flow_style) \
                    or value.start_mark.line != value.end_mark.line:
                self.finding(eline, f"external.edges.{edge} is one line holding a flow list [...]")
                continue
            entries: List[Union[PortRef, str]] = []
            for text, _ in self.strings(value, f"external.edges.{edge}") or []:
                if "." in text:
                    found = self.refs(text, eline, f"external.edges.{edge}")
                    keys_ = [ref_text(r) for r in found]
                else:
                    if text not in rail_names:
                        self.finding(eline, f"external.edges.{edge}: {text!r} is neither a port "
                                            "ref <instance>.<port> nor a rail in rails")
                        continue
                    found, keys_ = [text], [text]
                for entry, key in zip(found, keys_):
                    if key in entries_seen:
                        self.finding(eline, f"external: {key} is already on an edge (line "
                                            f"{entries_seen[key]}); each entry appears once")
                        continue
                    entries_seen[key] = eline
                    entries.append(entry)
            edges.append(EdgeItem(edge, tuple(entries), eline))
        if not edges:
            self.finding(_line(edges_node), "external.edges names at least one edge")
            return None
        if profile is None:
            return None
        return External(profile, tuple(edges), line)

    def fill_keepout(self, node: yaml.Node) -> Optional[FillKeepout]:
        pairs = self.mapping(node, "system.fill_keepout")
        if pairs is None:
            return None
        got = dict(pairs)
        for key in got:
            if key != "halo_um":
                self.finding(_line(node), f"fill_keepout: unknown key {key!r} (keys: halo_um)")
        if "halo_um" not in got:
            self.finding(_line(node), "fill_keepout: missing required key 'halo_um'")
            return None
        halo = []
        for layer, value in self.mapping(got["halo_um"], "fill_keepout.halo_um") or []:
            um = self.number(value, f"fill_keepout.halo_um.{layer}")
            if um is not None and um < 0:
                self.finding(_line(value), f"fill_keepout.halo_um.{layer} is 0 or more")
                continue
            if um is not None:
                halo.append((layer, um))
        return FillKeepout(tuple(halo), _line(node))


def check_text(text: str, path: Union[str, Path] = "system.chiplet"
               ) -> Tuple[Optional[Source], List[Finding]]:
    """Tier 0 on ``text``: the source (None if the document level refused) and
    every finding, in line order."""
    reader = _Reader(str(path), text)
    try:
        source = reader.read()
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        reader.finding(mark.line + 1 if mark else 0, f"not readable YAML: {exc}")
        source = None
    return source, sorted(reader.findings, key=lambda f: f.line)


def loads_source(text: str, path: Union[str, Path] = "system.chiplet") -> Source:
    """The source in ``text``, its tier 0 notes on :attr:`Source.notes`, or
    :class:`SourceError` with every tier 0 finding when one refuses. An
    editor, which could rewrite a line holding a key it does not know, refuses
    on a note too: it reads through :func:`check_text` and takes every finding
    as a refusal."""
    source, findings = check_text(text, path)
    if source is None or any(not f.note for f in findings):
        raise SourceError(path, findings or [Finding(0, "not a source document")])
    return replace(source, notes=tuple(findings))


def read_source(path: Union[str, Path]) -> Source:
    """The source at ``path``, or :class:`SourceError`."""
    path = Path(path)
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SourceError(path, [Finding(0, f"cannot be read: {exc}")]) from exc
    return loads_source(text, path)
