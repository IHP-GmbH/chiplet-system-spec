# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Placement written into a source: the dies a placer decided, the carrier size
and each external pin's edge, as edits the one writer applies.

``packaging-flow place`` elaborates an unplaced source provisionally
(:func:`.elaborate.elaborate_provisional`), has interposer-pnr's ``place``
decide the dies, and passes its ``interposer_pnr.placement`` 1.0 document here
with the external profile it resolved. :func:`placement_edits` returns the
:mod:`.patch` edits, the placed bytes, and reason rows saying what set each
number, so the printout is rendered from data and no reader parses it.

The placement document. ``format`` ``interposer_pnr.placement``, ``version``
``"1.0"``, ``file_sha256`` the sha256 of the exact source bytes placed, and
``dies``, one ``{id, at: [x, y], rotation}`` per die of the file: the die
centre in um, as ``dies[].at`` reads (the spec, "System"; a die centre in the
carrier frame, origin at its lower-left corner, y up), and a rotation of 0, 90,
180 or 270. ``units`` and ``frame``, when present, are ``um`` and
``gds_y_up``. Its ``reasons`` (the seed, the turns, the legalize moves) are the
placer's and come back on the result untouched. Every other key is the placer's
and is not read.

Which positions hold. A die the file already places keeps its ``at`` and
``rotation`` unless ``again`` is set, when the document's win; a kept die the
document puts elsewhere is not an error, and its reason row says the file won.
A die with no ``at`` takes the document's.

The carrier. When the carrier states ``size``, positions are taken as given in
its frame, and the dies with their margins must fit (``carrier_too_small``,
naming the size needed). When it does not, every position, kept dies included,
moves by one translation that puts the dies' bounding box (after rotation, die
sizes from the die folders) at the west and south margins, since a file with no
carrier anchors no frame; then the size is the smallest on a 50 um grid that
holds the dies, the margins and every pad row. The rule, in the terms of
packaging-flow's terminals stage (``terminals.pad_row`` and its checks T3 to
T5), with ``c`` the edge clearance, ``a`` the pad size, ``p`` the pitch and
``k`` the die keep-out of the resolved profile:

- margin, die box to a carrier edge: ``c + a + k`` on an edge that carries
  pads (the pad metal sits ``c`` in from the edge, ``a`` wide, and a die stays
  ``k`` from it, T4 and T5), ``c`` on one that carries none;
- pad row: ``n`` pads one pitch apart, centred on its edge, are ``(n - 1) p +
  a`` long, and leave ``r`` at each end, ``c`` when neither neighbouring edge
  carries pads and ``c + p`` when one does, so a row's last pad keeps a gap of
  ``p - a`` from the neighbouring row's band (T3) and ``c`` from the edge (T4):
  an edge of length ``s`` holds ``floor((s - a - 2 r) / p) + 1`` pads;
- width: the larger of the dies' extent plus the west and east margins, and
  each north and south row's length plus its two ends; height likewise.

A file with no ``external`` block has no pads and no profile: no margin.

Edges. Each ``unplaced.pins`` entry goes on the carrier edge nearest its die
pin by the planner's one rule (:func:`.plan.nearest_edge`, given the pin's
point); a rail goes by its first port's pin. A port's pin point is the centre
of its pads, from the die folder's inventory, mounted as elaboration mounts a
source die: about the die bbox centre, mirrored in x (flip chip), turned by
the die's rotation and moved to its ``at``; a pad the inventory gives no
position puts the point on the die centre. On a stated carrier the rule reads
that carrier. With no stated size the carrier is not known while the edges
are chosen, so the rule reads the dies' bounding box in its place (an entry
goes to the side of the dies its pin sits nearest), and the size follows from
the edges chosen, so only an edge that carries pads gets a band. Entries
already on an edge row are held where they are; the new ones follow them,
ordered along the edge by their pin's projection (y on west and east, x on
south and north, the way a pad row runs), then by their order in
``unplaced.pins``.

Refusals, each an :class:`~.patch.EditFinding` on an :class:`~.patch.EditRefused`,
the code the contract: ``placement_malformed`` (not a placement 1.0 document),
``placement_stale`` (the file changed since place ran), ``placement_dies_differ``
(a die the file does not have, or one it has that the document leaves out),
``placement_bad_rotation``, ``profile_malformed`` (the resolved profile lacks
a number), ``carrier_too_small``, ``edge_full`` (more pads on an edge than its
length holds at the stated size; one finding per entry to move, the entry its
``port``), ``kept_overlap`` (a kept die overlaps a placed one, the code
interposer-pnr's place gives the same case) and ``placement_overlap`` (two dies
the document places overlap), and ``not_a_source`` for a file tier 0, or tier
1 read as unplaced, refuses.

Reason rows, each a mapping with ``kind``, ``subject`` and a ``message`` for a
person: ``die`` (its at and rotation, and whether placed, kept or re-placed),
``translation`` (when the dies moved), ``margin`` (one per edge, and what set
it), ``carrier_size`` (one per axis: stated, the dies' extent, or a pad row)
and ``edge`` (one per external entry: its edge, its place in the row, held or
nearest which die).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple, Union

from ..errors import SystemSpecError
from .document import EDGES, ROTATIONS, Source, check_text, entry_text, is_number
from .geometry import _grid_extent, die_box, overlaps
from .inventory import DieFolder
from .patch import (
    Edit,
    EditFinding,
    EditRefused,
    Patch,
    Place,
    Replace,
    SetEntries,
    SetSize,
    decode,
    not_a_source,
    patch_system,
)
from .plan import Plan, nearest_edge, plan
from .scalars import number, quantized
from .unplaced import PLACE

__all__ = ["GRID_UM", "PLACEMENT_FORMAT", "PLACEMENT_VERSION", "PROFILE_KEYS", "Placed",
           "placement_edits"]

PLACEMENT_FORMAT = "interposer_pnr.placement"
PLACEMENT_VERSION = "1.0"
#: The grid a chosen carrier size is on, um.
GRID_UM = Decimal(50)
#: The resolved profile's numbers the rule reads, named as packaging-flow's
#: terminal profile names them.
PROFILE_KEYS = ("pad_size_um", "pitch_um", "edge_clearance_um", "die_keepout_um")

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
#: Each edge's two neighbours, and the axis its pad row runs along.
_NEIGHBOURS = {"north": ("west", "east"), "south": ("west", "east"),
               "west": ("south", "north"), "east": ("south", "north")}
_ALONG = {"north": 0, "south": 0, "west": 1, "east": 1}


@dataclass(frozen=True)
class Placed:
    """What :func:`placement_edits` returns."""

    #: The placed source bytes (the input's own when nothing changes).
    text: bytes
    #: The edits that write them, in the order made.
    edits: Tuple[Edit, ...]
    patch: Patch
    #: The carrier size of the placed file, um.
    size: Tuple[float, float]
    #: Why each number is what it is (the module docstring, "Reason rows").
    reasons: Tuple[Dict[str, Any], ...]
    #: The placement document's own ``reasons``, untouched (None when absent).
    placer_reasons: Any = None


@dataclass(frozen=True)
class _Die:
    id: str
    line: int
    at: Tuple[Decimal, Decimal]
    rotation: int
    extent: Tuple[Decimal, Decimal]  # width, height before rotation
    how: str  # placed (had no at), kept, re-placed (again)
    asked: Optional[Tuple[Decimal, Decimal]] = None  # the document's, for a kept die

    def box(self) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        return die_box(self.extent, self.at, self.rotation)


@dataclass(frozen=True)
class _Profile:
    pad: Decimal
    pitch: Decimal
    clearance: Decimal
    keepout: Decimal


@dataclass
class _Layout:
    """What the rule decided: margins, size, each edge's row, and per axis the
    length it needs and what set it."""

    size: Tuple[Decimal, Decimal]
    margins: Dict[str, Decimal]
    rows: Dict[str, List[Tuple[str, str, str]]]  # edge -> (entry, how, die), in row order
    needs: Dict[str, Tuple[Decimal, "_By"]] = field(default_factory=dict)  # axis -> (um, by)
    #: The one translation every die makes (an unsized carrier's frame).
    shift: Tuple[Decimal, Decimal] = (Decimal(0), Decimal(0))


def _refuse(code: str, message: str, line: int = 0, port: Optional[str] = None) -> EditRefused:
    return EditRefused([EditFinding(code, line, port, message)])


class _By(NamedTuple):
    """What set an axis. ``by`` is ``die_extent``, ``stated`` or ``pad_row``,
    and ``edge`` names the pad row when ``by`` is ``pad_row``."""

    by: str
    edge: Optional[str] = None


def _pair(value) -> Optional[Tuple[Decimal, Decimal]]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(is_number(v) for v in value)):
        return None
    return quantized(value[0]), quantized(value[1])


# ----------------------------------------------------------------------
# Reading the inputs
# ----------------------------------------------------------------------
def _read_bytes(source: Union[bytes, str, Path], path) -> Tuple[bytes, Union[str, Path]]:
    if isinstance(source, bytes):
        return source, path if path is not None else "system.chiplet"
    try:
        return Path(source).read_bytes(), path if path is not None else source
    except OSError as exc:
        raise _refuse("not_a_source", f"The file cannot be read ({exc}).") from None


def _document(doc: Any) -> Tuple[str, Dict[str, Tuple[Tuple[Decimal, Decimal], Any]]]:
    """The bound digest and each die's (at, rotation as written), or
    ``placement_malformed`` naming what is wrong."""
    def malformed(what: str) -> EditRefused:
        return _refuse("placement_malformed",
                       f"The placement is not an {PLACEMENT_FORMAT} {PLACEMENT_VERSION} "
                       f"document: {what}; run {PLACE} again.")

    if not isinstance(doc, Mapping):
        raise malformed("it is not a mapping")
    if doc.get("format") != PLACEMENT_FORMAT or doc.get("version") != PLACEMENT_VERSION:
        raise malformed(f"format {doc.get('format')!r} version {doc.get('version')!r}")
    for key, want in (("units", "um"), ("frame", "gds_y_up")):
        if key in doc and doc[key] != want:
            raise malformed(f"{key} {doc[key]!r}, not {want!r}")
    sha = doc.get("file_sha256")
    if not isinstance(sha, str) or not _SHA256.match(sha):
        raise malformed("file_sha256 is not a sha256 in lower-case hex")
    rows = doc.get("dies")
    if not isinstance(rows, list) or not rows:
        raise malformed("dies is not a non-empty list")
    dies: Dict[str, Tuple[Tuple[Decimal, Decimal], Any]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            raise malformed(f"dies[{i}] is not a mapping with a string id")
        at = _pair(row.get("at"))
        if at is None:
            raise malformed(f"dies[{i}] ({row['id']}): at is not two finite numbers")
        if "rotation" not in row:
            raise malformed(f"dies[{i}] ({row['id']}): no rotation")
        if row["id"] in dies:
            raise malformed(f"die {row['id']} is placed twice")
        dies[row["id"]] = (at, row["rotation"])
    return sha, dies


def _profile(profile: Optional[Mapping], source: Source) -> Optional[_Profile]:
    """The resolved profile's numbers, or None for a file with no external block."""
    if source.external is None:
        return None
    if not isinstance(profile, Mapping):
        raise _refuse("profile_malformed",
                      f"The file has an external block, so placing it needs the resolved "
                      f"profile {source.external.profile!r}: {', '.join(PROFILE_KEYS)}.",
                      source.external.line)
    values = []
    for key in PROFILE_KEYS:
        value = profile.get(key)
        if not is_number(value) or value <= 0:
            raise _refuse("profile_malformed",
                          f"The resolved profile's {key} is {value!r}, not a positive "
                          "number of um.", source.external.line)
        values.append(quantized(value))
    return _Profile(*values)


def _dies(source: Source, topology: Plan, placed: Mapping, again: bool) -> List[_Die]:
    """Each die of the file, in file order, at the position that holds."""
    findings = []
    known = {d.id for d in source.dies}
    for name in placed:
        if name not in known:
            findings.append(EditFinding(
                "placement_dies_differ", 0, None,
                f"The placement places {name}, which the file does not have; run {PLACE} "
                "again."))
    for die in source.dies:
        if die.id not in placed:
            findings.append(EditFinding(
                "placement_dies_differ", die.line, None,
                f"The placement leaves out {die.id}; run {PLACE} again."))
        elif placed[die.id][1] not in ROTATIONS or isinstance(placed[die.id][1], bool):
            findings.append(EditFinding(
                "placement_bad_rotation", die.line, None,
                f"The placement turns {die.id} by {placed[die.id][1]!r}; a die turns by "
                "0, 90, 180 or 270 degrees."))
    if findings:
        raise EditRefused(findings)
    out = []
    for die in source.dies:
        at, rotation = placed[die.id]
        folder = topology.folders[die.id]
        how, asked = "placed", None
        if die.at is not None and not again:
            how = "kept"
            if (quantized(die.at[0]), quantized(die.at[1])) != at or die.rotation != rotation:
                asked = at
            at, rotation = (quantized(die.at[0]), quantized(die.at[1])), die.rotation
        elif die.at is not None:
            how = "re-placed"
        w, h = _grid_extent(folder)
        out.append(_Die(die.id, die.line, at, int(rotation), (w, h), how, asked))
    return out


# ----------------------------------------------------------------------
# The rule
# ----------------------------------------------------------------------
def _margins(padded, rules: Optional[_Profile]) -> Dict[str, Decimal]:
    if rules is None:
        return {edge: Decimal(0) for edge in EDGES}
    band = rules.clearance + rules.pad + rules.keepout
    return {edge: band if edge in padded else rules.clearance for edge in EDGES}


def _end(edge: str, padded, rules: _Profile) -> Decimal:
    """What a row on ``edge`` leaves free at each end."""
    if any(n in padded for n in _NEIGHBOURS[edge]):
        return rules.clearance + rules.pitch
    return rules.clearance


def row_length(count: int, edge: str, padded, rules: _Profile) -> Decimal:
    """The edge length a row of ``count`` pads on ``edge`` needs, ends included."""
    if count == 0:
        return Decimal(0)
    return (count - 1) * rules.pitch + rules.pad + 2 * _end(edge, padded, rules)


def capacity(span: Decimal, edge: str, padded, rules: _Profile) -> int:
    """How many pads a row on ``edge`` of length ``span`` holds."""
    free = span - rules.pad - 2 * _end(edge, padded, rules)
    return 0 if free < 0 else int(free // rules.pitch) + 1


def _on_grid(value: Decimal) -> Decimal:
    return (value / GRID_UM).to_integral_value(rounding=ROUND_CEILING) * GRID_UM


def _bbox(dies: Sequence[_Die]) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
    boxes = [d.box() for d in dies]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


class _Entries:
    """The external entries: those held on an edge row, and those to place,
    each with the (die, port) whose pin it goes by."""

    def __init__(self, source: Source):
        self.held: Dict[str, List[Tuple[str, str]]] = {}  # edge -> (entry, die)
        external = source.external
        for row in (external.edges if external else ()):
            self.held[row.edge] = [(entry_text(e), self.pin(source, e)[0]) for e in row.entries]
        pins = source.unplaced.pins if source.unplaced else ()
        self.new = [(i, entry_text(e), self.pin(source, e)) for i, e in enumerate(pins)]

    @staticmethod
    def pin(source: Source, entry) -> Tuple[str, str]:
        """The (die, port) an entry goes by: its own, or a rail's first port
        (tier 0 reads no rail without one)."""
        if not isinstance(entry, str):
            return entry.instance, entry.port
        rail = next(r for r in source.rails if r.name == entry)
        return rail.ports[0].instance, rail.ports[0].port


#: A die-local offset turned by a rotation, counter-clockwise.
_TURN = {0: lambda x, y: (x, y), 90: lambda x, y: (-y, x),
         180: lambda x, y: (-x, -y), 270: lambda x, y: (y, -x)}


def pin_point(die: _Die, folder: DieFolder, port: str) -> Tuple[Decimal, Decimal]:
    """Where ``port``'s pins sit in the frame ``die.at`` is in: the centre of
    its pads, mounted as elaboration mounts a source die (about the die bbox
    centre, mirrored in x as a flip-chip die, then turned and moved). The die
    centre when the inventory gives a pad no position."""
    pads = set(folder.port(port).pads)
    xy = [(p.x_um, p.y_um) for p in folder.pins if p.name in pads]
    if not xy or any(x is None or y is None for x, y in xy):
        return die.at
    dx = folder.origin[0] + folder.width / 2 - sum(x for x, _ in xy) / len(xy)
    dy = sum(y for _, y in xy) / len(xy) - folder.origin[1] - folder.height / 2
    tx, ty = _TURN[die.rotation](dx, dy)
    return die.at[0] + quantized(tx), die.at[1] + quantized(ty)


def _rows(entries: _Entries, dies: Mapping[str, _Die], folders: Mapping[str, DieFolder],
          size, origin=(0, 0)) -> Dict[str, List]:
    """Each edge's row: held entries in place, then the new ones on the edge
    nearest their pin in the frame ``size`` with its corner at ``origin``,
    by projection then entry order."""
    rows = {edge: [(e, "held", d) for e, d in entries.held.get(edge, ())] for edge in EDGES}
    new: Dict[str, List] = {edge: [] for edge in EDGES}
    for i, entry, (die, port) in entries.new:
        x, y = pin_point(dies[die], folders[die], port)
        edge = nearest_edge((x - origin[0], y - origin[1]), size)
        new[edge].append(((x, y)[_ALONG[edge]], i, entry, die))
    for edge in EDGES:
        rows[edge].extend((e, "nearest", d) for _, _, e, d in sorted(new[edge]))
    return rows


def _choose(dies: List[_Die], entries: _Entries, rules: Optional[_Profile],
            folders: Mapping[str, DieFolder]) -> _Layout:
    """The size an unsized carrier gets, and the translation that moves the
    dies to its west and south margins.

    The carrier is not known while the edges are chosen, so the rule reads the
    dies' bounding box as the carrier: an entry goes to the side of the dies
    its pin sits nearest. The margins then follow from the edges chosen, so no
    edge carries a band it does not need, and nothing is iterated."""
    x0, y0, x1, y1 = _bbox(dies)
    rows = _rows(entries, {d.id: d for d in dies}, folders, (x1 - x0, y1 - y0), (x0, y0))
    padded = {edge for edge, row in rows.items() if row}
    margins = _margins(padded, rules)
    shift = (margins["west"] - x0, margins["south"] - y0)
    needs = {"width": (margins["west"] + x1 - x0 + margins["east"], _By("die_extent")),
             "height": (margins["south"] + y1 - y0 + margins["north"], _By("die_extent"))}
    if rules is not None:
        for edge, row in rows.items():
            axis = "width" if _ALONG[edge] == 0 else "height"
            length = row_length(len(row), edge, padded, rules)
            if length > needs[axis][0]:
                needs[axis] = (length, _By("pad_row", edge))
    size = (_on_grid(needs["width"][0]), _on_grid(needs["height"][0]))
    return _Layout(size, margins, rows, needs, shift)


def _stated(dies: List[_Die], entries: _Entries, rules: Optional[_Profile],
            source: Source, folders: Mapping[str, DieFolder]) -> _Layout:
    """The layout on the carrier's stated size, or the refusals it meets."""
    size = tuple(quantized(v) for v in source.carrier.size)
    by_id = {d.id: d for d in dies}
    rows = _rows(entries, by_id, folders, size)
    padded = {edge for edge, row in rows.items() if row}
    margins = _margins(padded, rules)
    _, _, x1, y1 = _bbox(dies)
    findings: List[EditFinding] = []
    line = source.carrier.line
    stated = f"{number(size[0])} x {number(size[1])} um"
    for d in dies:
        bx0, by0, _, _ = d.box()
        for edge, low in (("west", bx0), ("south", by0)):
            if low < margins[edge]:
                findings.append(EditFinding(
                    "carrier_too_small", d.line, None,
                    f"{d.id} comes within {number(max(low, Decimal(0)))} um of the {edge} edge "
                    f"of the {stated} carrier, which keeps {number(margins[edge])} um free; move "
                    f"it, or drop size to let {PLACE} choose."))
    # The stated size is held as stated: the dies need only fit it. The grid
    # is for a size place chooses, so it rounds the size suggested, not the test.
    need = (x1 + margins["east"], y1 + margins["north"])
    if need[0] > size[0] or need[1] > size[1]:
        want = tuple(_on_grid(n) if n > s else s for n, s in zip(need, size))
        findings.append(EditFinding(
            "carrier_too_small", line, None,
            f"The dies with their margins need a {number(want[0])} x {number(want[1])} um carrier "
            f"at these positions, and it states {stated}; state that size, or drop size to "
            f"let {PLACE} choose."))
    if rules is not None:
        for edge in EDGES:
            span = size[0] if _ALONG[edge] == 0 else size[1]
            fit = capacity(span, edge, padded, rules)
            row = rows[edge]
            # One finding per entry to move, the entry its port, so a caller
            # maps each to the pin to change.
            for entry, _, _ in row[fit:]:
                findings.append(EditFinding(
                    "edge_full", line, entry,
                    f"The {edge} edge would hold {len(row)} pads and {fit} fit on "
                    f"{number(span)} um at a {number(rules.pitch)} um pitch; move {entry} to "
                    f"another edge (set Edge on its EXT_PAD), or drop size to let {PLACE} "
                    "choose."))
    if findings:
        raise EditRefused(findings)
    return _Layout(size, margins, rows, {"width": (size[0], _By("stated")),
                                         "height": (size[1], _By("stated"))})


def _overlaps(dies: Sequence[_Die]) -> None:
    findings = []
    for i, a in enumerate(dies):
        for b in dies[:i]:
            if overlaps(a.box(), b.box()):
                kept = [d for d in (b, a) if d.how == "kept"]
                if kept:
                    findings.append(EditFinding(
                        "kept_overlap", kept[0].line, None,
                        f"{kept[0].id} keeps its at and overlaps "
                        f"{(a if kept[0] is b else b).id}; rerun {PLACE} with --again, or "
                        f"move {kept[0].id}."))
                else:
                    findings.append(EditFinding(
                        "placement_overlap", a.line, None,
                        f"The placement puts {a.id} on {b.id}; run {PLACE} again."))
    if findings:
        raise EditRefused(findings)


# ----------------------------------------------------------------------
# Reasons
# ----------------------------------------------------------------------
def _xy(pair) -> List[float]:
    return [float(pair[0]), float(pair[1])]


def _die_data(d: _Die) -> Dict[str, Any]:
    row = {"kind": "die", "subject": d.id, "at": d.at, "rotation": d.rotation, "why": d.how}
    if d.asked is not None:
        row["placement_at"] = d.asked
    return row


def _margin_data(edge: str, layout: _Layout, rules: Optional[_Profile]) -> Dict[str, Any]:
    um = layout.margins[edge]
    return {"kind": "margin", "subject": edge, "um": um,
            "band": rules is not None and um > rules.clearance}


def _size_data(axis: str, layout: _Layout) -> Dict[str, Any]:
    lo, hi = ("west", "east") if axis == "width" else ("south", "north")
    um = layout.size[0 if axis == "width" else 1]
    need, why = layout.needs[axis]
    row = {"kind": "carrier_size", "subject": axis, "um": um,
           "set_by": why.by, "need_um": need,
           "margins": {lo: layout.margins[lo], hi: layout.margins[hi]}}
    if why.by == "pad_row":
        row["edge"] = why.edge
    return row


def _edge_data(edge: str, position: int, entry: str, how: str, die: str) -> Dict[str, Any]:
    return {"kind": "edge", "subject": entry, "edge": edge, "position": position,
            "why": how, "die": die}


def _put_message(row: Dict[str, Any], message: str) -> Dict[str, Any]:
    """``message`` before ``placement_at`` or a size row's ``edge``, else last."""
    before = None
    if "placement_at" in row:
        before = "placement_at"
    elif row["kind"] == "carrier_size" and "edge" in row:
        before = "edge"
    if before is None:
        row["message"] = message
        return row
    out: Dict[str, Any] = {}
    for key, value in row.items():
        if key == before:
            out["message"] = message
        out[key] = value
    return out


def _publish(row: Dict[str, Any]) -> None:
    """The numbers a caller sees are floats. The sentence already used the Decimals."""
    for key in ("at", "placement_at", "by"):
        if key in row:
            row[key] = _xy(row[key])
    for key in ("um", "need_um"):
        if key in row:
            row[key] = float(row[key])
    if "margins" in row:
        row["margins"] = {edge: float(um) for edge, um in row["margins"].items()}


def _reasons(dies: Sequence[_Die], layout: _Layout, shift, rules: Optional[_Profile],
             stated: bool) -> List[Dict[str, Any]]:
    """Data rows first, then one sentence each, formatted from the Decimals.

    ``message`` is inserted before ``placement_at`` on a die row and before
    ``edge`` on a pad-row size row. The numbers then become floats, in place.
    """
    rows: List[Dict[str, Any]] = [_die_data(d) for d in dies]
    if any(shift):
        rows.append({"kind": "translation", "subject": "dies", "by": shift})
    rows.extend(_margin_data(edge, layout, rules) for edge in EDGES)
    rows.extend(_size_data(axis, layout) for axis in ("width", "height"))
    for edge in EDGES:
        for position, (entry, how, die) in enumerate(layout.rows[edge]):
            rows.append(_edge_data(edge, position, entry, how, die))

    def _message(row: Dict[str, Any]) -> str:
        kind = row["kind"]
        if kind == "die":
            at = f"[{number(row['at'][0])}, {number(row['at'][1])}]"
            if row["why"] == "placed":
                return f"{row['subject']} at {at}, rotation {row['rotation']}: placed by {PLACE}"
            if row["why"] == "re-placed":
                return (f"{row['subject']} at {at}, rotation {row['rotation']}: "
                        "re-placed (--again)")
            message = f"{row['subject']} at {at}, rotation {row['rotation']}: kept from the file"
            if "placement_at" in row:
                message += ("; the placement placed it otherwise and the file wins (rerun "
                            "with --again to take the placement's)")
            return message
        if kind == "translation":
            by = row["by"]
            return (f"every die moved by [{number(by[0])}, {number(by[1])}] so the "
                    "dies' bounding box sits at the west and south margins")
        if kind == "margin":
            if rules is None:
                why = "no external block, so no pad and no profile"
            elif row["band"]:
                why = (f"edge clearance {number(rules.clearance)} + pad {number(rules.pad)} + "
                       f"die keep-out {number(rules.keepout)}, the band of an edge that "
                       "carries pads")
            else:
                why = f"edge clearance {number(rules.clearance)}, an edge that carries no pads"
            return f"{row['subject']} margin {number(row['um'])} um: {why}"
        if kind == "carrier_size":
            axis, um, need = row["subject"], row["um"], row["need_um"]
            if row["set_by"] == "stated":
                return f"{axis} {number(um)} um: stated in the file"
            if row["set_by"] == "die_extent":
                lo, hi = ("west", "east") if axis == "width" else ("south", "north")
                return (f"{axis} {number(um)} um: the dies with a {number(row['margins'][lo])} um "
                        f"{lo} and a {number(row['margins'][hi])} um {hi} margin need "
                        f"{number(need)} um, on the {number(GRID_UM)} um grid")
            edge = row["edge"]
            return (f"{axis} {number(um)} um: the {edge} pad row of "
                    f"{len(layout.rows[edge])} needs {number(need)} um, on the "
                    f"{number(GRID_UM)} um grid")
        where = ("held, the file puts it on that edge" if row["why"] == "held" else
                 f"the edge nearest its pin on {row['die']}" if stated else
                 f"the side of the dies its pin on {row['die']} sits nearest")
        return f"{row['subject']} on {row['edge']}, pad {row['position'] + 1}: {where}"

    out: List[Dict[str, Any]] = []
    for row in rows:
        written = _put_message(row, _message(row))
        _publish(written)
        out.append(written)
    return out


# ----------------------------------------------------------------------
# The edits
# ----------------------------------------------------------------------
def placement_edits(source: Union[bytes, str, Path], placement: Mapping,
                    profile: Optional[Mapping], *, again: bool = False,
                    path: Union[str, Path, None] = None) -> Placed:
    """The edits that write ``placement`` into the source, and the placed bytes.

    ``source`` is the file's bytes, with ``path`` naming where they were read
    from (the die folders resolve from its folder), or the path itself.
    ``placement`` is the parsed ``interposer_pnr.placement`` 1.0 document;
    ``profile`` the resolved external profile's numbers (:data:`PROFILE_KEYS`),
    None for a file with no external block. ``again`` re-places the dies the
    file already places. Raises :class:`EditRefused`.
    """
    data, path = _read_bytes(source, path)
    text, _ = decode(data)
    src, findings = check_text(text, path)
    if findings or src is None:
        raise not_a_source(findings)
    topology = plan(src, unplaced=True)
    if topology.refusals:
        raise not_a_source(topology.refusals)
    if not src.dies:
        raise _refuse("not_a_source", "The file has no dies yet; import the sheet before "
                                      f"{PLACE}.", src.key_lines.get("carrier", 0))
    sha, placed = _document(placement)
    if sha != hashlib.sha256(data).hexdigest():
        raise _refuse("placement_stale", f"The file changed since place ran; run {PLACE} "
                                         "again.")
    rules = _profile(profile, src)
    dies = _dies(src, topology, placed, again)
    _overlaps(dies)
    entries = _Entries(src)
    stated = src.carrier.size is not None
    layout = (_stated(dies, entries, rules, src, topology.folders) if stated
              else _choose(dies, entries, rules, topology.folders))
    shift = layout.shift
    dies = [replace(d, at=(d.at[0] + shift[0], d.at[1] + shift[1])) for d in dies]

    edits: List[Edit] = []
    if not stated:
        edits.append(SetSize(layout.size))
    for d, item in zip(dies, src.dies):
        written = None if item.at is None else (quantized(item.at[0]), quantized(item.at[1]))
        if item.at is None:
            edits.append(Place(d.id, d.at, d.rotation))
        else:
            if written != d.at:
                edits.append(Replace("dies", d.id, "at", d.at))
            if item.rotation != d.rotation:
                edits.append(Replace("dies", d.id, "rotation", d.rotation))
    for edge in EDGES:
        if any(how == "nearest" for _, how, _ in layout.rows[edge]):
            edits.append(SetEntries(edge, tuple(e for e, _, _ in layout.rows[edge])))
    if entries.new:
        edits.append(SetEntries("unplaced", ()))
    patch = patch_system(data, edits, name=Path(str(path)).name)
    _prove(patch.text, path)
    return Placed(text=patch.text, edits=tuple(edits), patch=patch,
                  size=(float(layout.size[0]), float(layout.size[1])),
                  reasons=tuple(_reasons(dies, layout, shift, rules, stated)),
                  placer_reasons=placement.get("reasons"))


def _prove(data: bytes, path) -> None:
    """The placed file passes tiers 0 and 1 as a placed file, or this module
    has a defect and nothing is returned."""
    src, findings = check_text(decode(data)[0], path)
    refusals = list(findings) if src is None else [
        *findings, *plan(src).refusals]
    if refusals:
        raise SystemSpecError(f"{path}: the placed file does not pass tier 1 "
                              f"({refusals[0].message}); this is a defect of placement_edits, "
                              "nothing was written")
