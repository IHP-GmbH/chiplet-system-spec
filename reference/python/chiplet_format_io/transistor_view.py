# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The transistor-level view of a block: a SPICE netlist as shipped, the text
of it that goes into a deck, and the structural reader that checks the view's
meta against that text.

A view is a ``chiplet-eda-views-transistor/v1`` JSON file. It names its
netlist files by relative path and sha256, in listing order, and states what a
consumer binds: the top subcircuit as written, whether the netlist holds the top
as a ``.subckt`` card or as Xschem's commented ``**.subckt`` pair, the top's
pins in order with a declared role each, the library subcircuits it uses
(``devices``) and the ones it defines besides the top (``local_subckts``). It
stands alone beside its files, or sits in an ``eda-views.json`` record as one
``transistor_view`` row plus the ``spice`` (or ``cdl``) rows it names.

:func:`deck_text` turns the shipped bytes into the text a deck holds before a
consumer namespaces it: the commented top uncommented, the cards a netlist
consists of, its structure checked against what can stand inside a subcircuit,
and every subcircuit name the view defines mapped to ``[A-Za-z0-9_]``. Its
sha256 is what a lock binds as ``deck_text_sha256``. :func:`transistor_view`
reads a view, standalone or from a record, and refuses unless the pins,
``local_subckts`` and ``devices`` derived from that text are the meta's.
:func:`derive` answers those three as the text gives them, so a writer states
the meta the reader then checks.

The scanner both use is exported by name (:func:`cards`, :func:`card_word`,
:func:`subckt_ports`, :func:`instance_subckt`, :data:`BLOCK_CARDS`,
:data:`ALWAYS_GLOBAL`), so the co-simulation that builds decks from these
views reads netlists the same way. Names are compared with ASCII case folding,
as ngspice compares them; whitespace is ASCII whitespace.

A refusal is :class:`TransistorViewRefused`, an
:class:`~chiplet_format_io.eda_views.EdaViewsRefused`. It carries ``reason``,
one word of :data:`REASONS`, which each consumer maps to its own verdict words.
A ``cdl`` view reads at this release only as far as its shape: the CDL
derivation lands with its writer.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from .eda_views import SCHEMA as _RECORD_SCHEMA
from .eda_views import EdaViewsRefused

__all__ = [
    "VIEW_FORMAT",
    "BLOCK_CARDS",
    "ALWAYS_GLOBAL",
    "REASONS",
    "TransistorViewRefused",
    "DeckText",
    "TransistorView",
    "cards",
    "card_word",
    "subckt_ports",
    "instance_subckt",
    "deck_text",
    "derive",
    "transistor_view",
]

VIEW_FORMAT = "chiplet-eda-views-transistor/v1"

#: Top-level cards a block netlist may hold; anything else would act on the whole deck.
BLOCK_CARDS = (".subckt", ".ends", ".end")
#: Cards that are global wherever they stand, even inside a subcircuit.
ALWAYS_GLOBAL = (".include", ".lib", ".option", ".options", ".temp", ".global",
                 ".control", ".endc", ".tran", ".ac", ".dc", ".op", ".noise", ".save",
                 ".csparam", ".func")

#: The words a refusal's ``reason`` takes.
REASONS = (
    "view_shape",       # the view file is not a view: format, enums, paths, names
    "selector",         # a record holds no view, or more than one, for the pair asked
    "dialect",          # a cdl view, whose derivation lands with its writer
    "digest",           # a file is not the bytes its sha256 binds
    "encoding",         # a file is not strict UTF-8
    "top_form",         # the commented top pair is not exactly where it must be
    "subckt_absent",    # no .subckt card defines the top
    "continuation",     # a file's first card line starts with +
    "comment_in_card",  # a comment terminator survives in a card
    "include_card",     # .include, .lib or a prefix of them
    "device_card",      # a card that is not a listed element, or a .model
    "block_global",     # a card that acts on the whole deck, or an element outside a subckt
    "nesting",          # .subckt inside a subckt, .ends at depth 0, an open subckt, a stray .end
    "card_syntax",      # a token that is or starts with =, an X card naming no subckt
    "name_clash",       # two subckt names, or a subckt and a device, equal after mapping
    "view_differs",     # a derived field is not the meta's
)


class TransistorViewRefused(EdaViewsRefused):
    """A transistor view does not read; ``reason`` is one of :data:`REASONS`."""

    def __init__(self, reason: str, message: str, field: Optional[str] = None) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.field = field


class DeckText(NamedTuple):
    """The text a deck holds before namespacing, the mapped top and the text's sha256."""
    text: str
    top: str
    sha256: str


class TransistorView(NamedTuple):
    """A view as read, its deck text, and the directory its file paths are relative to."""
    view: Dict[str, Any]
    deck: DeckText
    root: Path


_DIALECTS = ("spice", "cdl")
_NETLIST_KINDS = ("schematic", "pnr", "extracted", "pex")
_TOP_FORMS = ("subckt", "commented")
_ROLES = ("signal", "supply", "internal")

#: ASCII case folding: only A to Z move, as ngspice compares names.
_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
#: ASCII whitespace, without the newline the text was split on.
_WHITE = " \t\r\x0b\x0c"
_SPLIT = re.compile(r"[ \t\r\x0b\x0c]+")
#: An inline comment: $ or ; with a space or tab on each side.
_INLINE = re.compile(r"[ \t][$;][ \t]")
#: What ngspice would still end a card at: ; or // anywhere, $ at the start or after whitespace or a comma.
_TERMINATOR = re.compile(r";|//|(?:^|[ \t,])\$")
#: Element letters allowed inside a subckt, and the ones allowed only with a value and no model.
_ELEMENTS = "xkviefghb"
_VALUED = "rcl"
#: An R, C or L value: a SPICE number with one optional scale suffix and nothing
#: after it, ASCII case-insensitive, or one brace expression. ``r=1k`` and
#: ``'rval'`` refuse, which is stricter than ngspice.
_VALUE = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:e[+-]?[0-9]+)?(?:t|g|meg|k|mil|m|u|n|p|f)?"
                    r"|\{[^ ]*\}", re.IGNORECASE | re.ASCII)
#: The dot cards a subckt may hold besides the structural ones.
_SUBCKT_DOT_CARDS = (".param",)
#: Subckt names map every character outside these to _.
_UNMAPPED = re.compile(r"[^A-Za-z0-9_]")
#: Cards ngspice knows by their start: ``.incl`` is ``.include``, ``.library``
#: is ``.lib``, ``.endlibrary`` is ``.endl``, ``.controls`` is ``.control`` and
#: ``.endcontrol`` is ``.endc``.
_BY_PREFIX = ((".inc", ".include"), (".lib", ".lib"), (".endl", ".endl"),
              (".control", ".control"), (".endc", ".endc"))


def _fold(text: str) -> str:
    return text.translate(_FOLD)


def _tokens(line: str) -> List[str]:
    return [token for token in _SPLIT.split(line.strip(_WHITE)) if token]


def _refuse(reason: str, message: str, field: Optional[str] = None) -> TransistorViewRefused:
    return TransistorViewRefused(reason, message, field)


# ----------------------------------------------------------------------
# The scanner
# ----------------------------------------------------------------------
def card_word(word: str) -> str:
    """A card's first word as ngspice reads it, ASCII lower-cased, a card ngspice
    knows by its start by its full name."""
    word = _fold(word)
    for prefix, card in _BY_PREFIX:
        if word.startswith(prefix):
            return card
    return word


def cards(text: str, where: str = "the netlist") -> List[str]:
    """The cards of one file's text, split on ``\\n`` only: blank lines and lines
    whose first non-whitespace character is ``*`` dropped, an inline comment
    (``$`` or ``;`` with a space or tab on each side) dropped, ``+`` lines joined
    to the card before, each card's tokens joined by one space. A file whose
    first card line starts with ``+`` refuses (``continuation``), and so does a
    card still holding ``;``, ``//`` or a ``$`` after whitespace or a comma
    (``comment_in_card``): ngspice would end the card there."""
    found: List[str] = []
    for raw in text.split("\n"):
        line = raw.strip(_WHITE)
        if not line or line.startswith("*"):
            continue
        inline = _INLINE.search(raw)
        if inline:
            line = raw[:inline.start()].strip(_WHITE)
            if not line:
                continue
        if line.startswith("+"):
            if not found:
                raise _refuse("continuation", f"{where} starts with a continuation line")
            found[-1] += " " + line[1:]
        else:
            found.append(line)
    result = []
    for card in found:
        card = " ".join(_tokens(card))
        if _TERMINATOR.search(card):
            raise _refuse("comment_in_card", f"{where} holds {card!r}, which ngspice would end early")
        result.append(card)
    return result


def subckt_ports(card: str) -> Tuple[str, List[str]]:
    """The name and the pins of a ``.subckt`` card; its parameters are not pins."""
    tokens = _tokens(card)[1:]
    if not tokens:
        raise _refuse("card_syntax", "a .subckt card with no name")
    pins = []
    for token in tokens[1:]:
        if _fold(token) == "params:" or "=" in token:
            break
        pins.append(token)
    return tokens[0], pins


def instance_subckt(tokens: Sequence[str]) -> int:
    """The index of the subcircuit name on an ``X`` card: the last token before its parameters."""
    end = len(tokens)
    for k, token in enumerate(tokens):
        if k > 0 and (_fold(token) == "params:" or "=" in token):
            end = k
            break
    return end - 1


# ----------------------------------------------------------------------
# The view's shape
# ----------------------------------------------------------------------
def _relative(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise _refuse("view_shape", f"{what} names no file")
    spelled = PurePosixPath(value)
    if spelled.is_absolute() or ".." in spelled.parts or "\\" in value:
        raise _refuse("view_shape", f"{what} {value!r} is not a relative path with no ..")
    return value


def _file(root: Path, value: Any, sha256: Any, what: str) -> bytes:
    """The bytes of ``value`` under ``root``, symlinks resolved and still inside
    ``root``, re-hashed against ``sha256``."""
    name = _relative(value, what)
    path = (Path(root) / name).resolve()
    try:
        path.relative_to(Path(root).resolve())
    except ValueError:
        raise _refuse("view_shape", f"{what} {name!r} resolves outside {root}") from None
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise _refuse("digest", f"{what} {name!r} cannot be read: {exc}") from exc
    if hashlib.sha256(data).hexdigest() != sha256:
        raise _refuse("digest", f"{name} is not the bytes its sha256 binds")
    return data


def _names(view: Mapping[str, Any], key: str) -> List[str]:
    value = view.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise _refuse("view_shape", f"{key} is not a list of names", key)
    if len({_fold(v) for v in value}) != len(value):
        raise _refuse("view_shape", f"{key} names something twice", key)
    return value


def _shape(view: Any) -> Dict[str, Any]:
    """The view's own checks: format, enums, names, paths."""
    if not isinstance(view, dict) or view.get("format") != VIEW_FORMAT:
        raise _refuse("view_shape", f"not a {VIEW_FORMAT} file")
    for key, allowed in (("dialect", _DIALECTS), ("netlist_kind", _NETLIST_KINDS),
                         ("top_form", _TOP_FORMS)):
        if view.get(key) not in allowed:
            raise _refuse("view_shape", f"{key} is {view.get(key)!r}, not one of {allowed}", key)
    if not isinstance(view.get("top"), str) or not view["top"]:
        raise _refuse("view_shape", "top names no subcircuit", "top")
    pins = view.get("pins")
    if not isinstance(pins, list) or not all(
            isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"]
            and p.get("role") in _ROLES for p in pins):
        raise _refuse("view_shape", f"pins is not a list of {{name, role}} with a role of {_ROLES}", "pins")
    if len({_fold(p["name"]) for p in pins}) != len(pins):
        raise _refuse("view_shape", "two pins have the same name, ASCII case-folded", "pins")
    devices, local = _names(view, "devices"), _names(view, "local_subckts")
    if {_fold(d) for d in devices} & {_fold(s) for s in local}:
        raise _refuse("view_shape", "devices and local_subckts share a name", "devices")
    pdk = view.get("pdk")
    if not isinstance(pdk, dict) or not all(isinstance(pdk.get(k), str) and pdk[k]
                                            for k in ("name", "revision")):
        raise _refuse("view_shape", "pdk states no name and revision", "pdk")
    files = view.get("files")
    if not isinstance(files, list) or not files or not all(
            isinstance(f, dict) and isinstance(f.get("sha256"), str) for f in files):
        raise _refuse("view_shape", "files is not a list of {file, sha256}", "files")
    for entry in files:
        _relative(entry.get("file"), "a files entry")
    return view


# ----------------------------------------------------------------------
# deck_text
# ----------------------------------------------------------------------
def _read(root: Path, entry: Any) -> List[str]:
    """Step 1: one file's lines, re-hashed and decoded as strict UTF-8."""
    if not isinstance(entry, Mapping):
        raise _refuse("view_shape", "a files entry is not {file, sha256}")
    data = _file(root, entry.get("file"), entry.get("sha256"), "a files entry")
    try:
        return data.decode("utf-8", errors="strict").split("\n")
    except UnicodeDecodeError as exc:
        raise _refuse("encoding", f"{entry['file']} is not UTF-8: {exc}") from exc


def _starred(line: str, word: str) -> bool:
    """Whether ``line`` starts at column 0 with ``**`` and ``word``, ASCII folded."""
    return _fold(line[:2 + len(word)]) == "**" + word


def _uncomment(files: List[List[str]], top: str, names: Sequence[str]) -> None:
    """Step 2 for ``top_form: commented``: exactly one ``**.subckt <top>`` line and,
    after it in the same file, exactly one ``**.ends`` line, both in the last
    file, with no ``.subckt`` card between; the two lose their leading ``**``."""
    heads = [(f, k) for f, lines in enumerate(files) for k, line in enumerate(lines)
             if _starred(line, ".subckt") and _top_line(line, top)]
    if len(heads) != 1:
        raise _refuse("top_form", f"the files hold {len(heads)} lines **.subckt {top}; one is expected")
    f, head = heads[0]
    if f != len(files) - 1:
        raise _refuse("top_form", f"**.subckt {top} is in {names[f]}, which is not the last file")
    lines = files[f]
    ends = [k for k, line in enumerate(lines) if _fold(line.rstrip(_WHITE)) == "**.ends"]
    if len(ends) != 1 or ends[0] < head:
        raise _refuse("top_form", f"{names[f]} holds {len(ends)} lines **.ends after the top; one is expected")
    for line in lines[head + 1:ends[0]]:
        words = _tokens(line)
        if words and not words[0].startswith("*") and card_word(words[0]) == ".subckt":
            raise _refuse("top_form", f"{names[f]} defines a subckt between **.subckt {top} and **.ends")
    lines[head], lines[ends[0]] = lines[head][2:], lines[ends[0]][2:]


def _top_line(line: str, top: str) -> bool:
    rest = line[len("**.subckt"):]
    if not rest or rest[0] not in _WHITE:
        return False
    words = _tokens(rest)
    return bool(words) and _fold(words[0]) == _fold(top)


def _rcl(tokens: List[str]) -> bool:
    """An R, C or L card with a value as its fourth token and no model: every later token holds =."""
    return len(tokens) >= 4 and bool(_VALUE.fullmatch(tokens[3])) and all("=" in t for t in tokens[4:])


def _structure(per_file: List[List[str]], names: Sequence[str]) -> List[List[str]]:
    """Step 4: the cards that go into the deck, each as its tokens, ``.end`` dropped."""
    out: List[List[str]] = []
    last = len(per_file) - 1
    for f, file_cards in enumerate(per_file):
        depth = 0
        for k, card in enumerate(file_cards):
            tokens = card.split(" ")
            if any(t.startswith("=") for t in tokens):
                raise _refuse("card_syntax", f"{names[f]}: {card!r} has a token starting with =")
            word = card_word(tokens[0])
            if word in (".include", ".lib"):
                raise _refuse("include_card", f"{names[f]} holds {tokens[0]!r}")
            if word == ".model":
                raise _refuse("device_card", f"{names[f]} holds a .model card")
            if word in ALWAYS_GLOBAL:
                raise _refuse("block_global", f"{names[f]} holds {tokens[0]!r}, which acts on the whole deck")
            if word == ".subckt":
                if depth:
                    raise _refuse("nesting", f"{names[f]} defines a subckt inside a subckt")
                depth = 1
            elif word == ".ends":
                if not depth:
                    raise _refuse("nesting", f"{names[f]} holds .ends outside a subckt")
                depth = 0
            elif word == ".end":
                if f != last or k != len(file_cards) - 1:
                    raise _refuse("nesting", f"{names[f]} holds .end before the last card of the last file")
                continue
            elif word.startswith("."):
                if not depth:
                    raise _refuse("block_global", f"{names[f]} holds {tokens[0]!r} outside a subckt")
                if word not in _SUBCKT_DOT_CARDS:
                    raise _refuse("block_global", f"{names[f]} holds {tokens[0]!r} in a subckt, "
                                                  f"which allows only {_SUBCKT_DOT_CARDS}")
            else:
                if not depth:
                    raise _refuse("block_global", f"{names[f]} has an element outside a subckt: {tokens[0]!r}")
                letter = word[0]
                if letter in _VALUED:
                    if not _rcl(tokens):
                        raise _refuse("device_card", f"{names[f]}: {card!r} names a model or no value")
                elif letter not in _ELEMENTS:
                    raise _refuse("device_card", f"{names[f]}: {tokens[0]!r} is not a listed element card")
                if letter == "x" and instance_subckt(tokens) < 1:
                    raise _refuse("card_syntax", f"{names[f]}: {card!r} names no subckt")
            out.append(tokens)
        if depth:
            raise _refuse("nesting", f"{names[f]} ends inside a subckt")
    return out


def _subckts(tokens: List[List[str]]) -> List[List[str]]:
    found = [t for t in tokens if card_word(t[0]) == ".subckt"]
    if any(len(t) < 2 for t in found):
        raise _refuse("card_syntax", "a .subckt card with no name")
    return found


def _derive(tokens: List[List[str]], top: str) -> Tuple[List[str], List[str], List[str], str]:
    """Step 5, before mapping: the top's pins in order, the other subckt names and the devices."""
    found = _subckts(tokens)
    at = next(k for k, t in enumerate(found) if _fold(t[1]) == _fold(top))
    spelled, pins = subckt_ports(" ".join(found[at]))
    local = [t[1] for k, t in enumerate(found) if k != at]
    known = {_fold(t[1]) for t in found}
    devices: List[str] = []
    for t in tokens:
        if card_word(t[0])[0] == "x":
            name = t[instance_subckt(t)]
            if _fold(name) not in known and _fold(name) not in {_fold(d) for d in devices}:
                devices.append(name)
    return pins, local, devices, spelled


def _map(name: str) -> str:
    return _UNMAPPED.sub("_", name)


def deck_text(view: Mapping[str, Any], root: Path) -> DeckText:
    """The text a deck holds for ``view`` before namespacing, its mapped top and
    its sha256. ``root`` is the directory the view's file paths are relative to.
    Refuses with :class:`TransistorViewRefused` at the first step that fails."""
    text, _ = _deck(view, root)
    return text


def derive(view: Mapping[str, Any], root: Path) -> Tuple[List[str], List[str], List[str]]:
    """The top's pins in order, the ``local_subckts`` and the ``devices`` that
    :func:`transistor_view` derives from the deck text of ``view`` and checks
    the meta against, each name as the text spells it. Only ``dialect``,
    ``files``, ``top`` and ``top_form`` are read, so a writer can state the
    rest from what this returns. Refuses as :func:`deck_text` does."""
    _, (pins, local, devices) = _deck(view, Path(root))
    return list(pins), list(local), list(devices)


def _deck(view: Mapping[str, Any], root: Path) -> Tuple[DeckText, Tuple[List[str], List[str], List[str]]]:
    if view.get("dialect") == "cdl":
        raise _refuse("dialect", "a cdl view has no deck text at this release")
    files, top, form = view.get("files"), view.get("top"), view.get("top_form")
    if not isinstance(files, list) or not files or not isinstance(top, str) or not top \
            or form not in _TOP_FORMS:
        raise _refuse("view_shape", "the view states no files, top or top_form")
    lines = [_read(root, entry) for entry in files]
    names = [str(entry["file"]) for entry in files]
    if form == "commented":
        _uncomment(lines, top, names)
    elif any(_starred(line, ".subckt") for file_lines in lines for line in file_lines):
        raise _refuse("top_form", "a **.subckt line under top_form: subckt")
    per_file = [cards("\n".join(file_lines), names[f]) for f, file_lines in enumerate(lines)]
    if not any(_fold(t[1]) == _fold(top) for t in _subckts([c.split(" ") for fc in per_file for c in fc])):
        raise _refuse("subckt_absent", f"no .subckt card defines {top!r}")
    tokens = _structure(per_file, names)
    pins, local, devices, spelled = _derive(tokens, top)
    defined = [spelled] + local
    mapped: Dict[str, str] = {}
    for name in defined:
        key = _fold(_map(name))
        if key in mapped:
            raise _refuse("name_clash", f"{mapped[key]!r} and {name!r} are the same name after mapping")
        mapped[key] = name
    for device in devices:
        if _fold(device) in mapped:
            raise _refuse("name_clash", f"subckt {mapped[_fold(device)]!r} maps to device {device!r}")
    out = []
    for t in tokens:
        t = list(t)
        word = card_word(t[0])
        at = 1 if word in (".subckt", ".ends") and len(t) > 1 else \
            instance_subckt(t) if word[0] == "x" else None
        if at is not None and _fold(t[at]) in {_fold(name) for name in defined}:
            t[at] = _map(t[at])
        out.append(" ".join(t))
    body = "\n".join(out) + "\n"
    deck = DeckText(body, _map(spelled), hashlib.sha256(body.encode("utf-8")).hexdigest())
    return deck, (pins, local, devices)


# ----------------------------------------------------------------------
# transistor_view
# ----------------------------------------------------------------------
def _json(path: Path, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _refuse("view_shape", f"{what} {path} cannot be read: {exc}") from exc


def _from_record(record: Path, document: Mapping[str, Any], dialect: str,
                 netlist_kind: str) -> Dict[str, Any]:
    """The one view of a record with this ``(dialect, netlist_kind)``, after the
    record's netlist rows are checked to split exactly into its views' files.
    Cdl rows split all or none: a record with no cdl view may leave every cdl
    row to no view, and a record with one holds each cdl row in exactly one.
    Spice rows always belong to exactly one view."""
    rows = document.get("views")
    if not isinstance(rows, list):
        raise _refuse("view_shape", f"{record} lists no views")
    views = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("kind") != "transistor_view":
            continue
        data = _file(record.parent, row.get("file"), row.get("sha256"), f"{record}: a transistor_view row")
        try:
            view = json.loads(data.decode("utf-8"))
        except ValueError as exc:
            raise _refuse("view_shape", f"{record}: {row['file']} is not JSON: {exc}") from exc
        views.append(_shape(view))
    for kind in _DIALECTS:
        kind_rows = [(row.get("file"), row.get("sha256")) for row in rows
                     if isinstance(row, Mapping) and row.get("kind") == kind]
        owners = [0] * len(kind_rows)
        kind_views = [v for v in views if v["dialect"] == kind]
        if kind == "cdl" and not kind_views:
            continue
        for view in kind_views:
            named = [(f["file"], f["sha256"]) for f in view["files"]]
            at = [k for k, row in enumerate(kind_rows) if row in named]
            if [kind_rows[k] for k in at] != named:
                raise _refuse("selector", f"{record}: a {kind} view's files are not, in order, "
                                          f"{kind} rows of the record")
            for k in at:
                owners[k] += 1
        if any(n != 1 for n in owners):
            raise _refuse("selector", f"{record}: a {kind} row belongs to no view or to more than one")
    chosen = [v for v in views if (v["dialect"], v["netlist_kind"]) == (dialect, netlist_kind)]
    if len(chosen) != 1:
        raise _refuse("selector", f"{record} holds {len(chosen)} views of ({dialect}, {netlist_kind}); "
                                  "one is expected")
    return chosen[0]


def transistor_view(source: Path, dialect: str, netlist_kind: str) -> TransistorView:
    """The view of ``(dialect, netlist_kind)`` at ``source``, a standalone view
    file or an ``eda-views.json`` record, checked against its deck text: the
    top's pins in order, ``local_subckts`` and ``devices`` derived from the text
    equal the meta's, ASCII case-folded, or the call refuses with the field
    named. A ``cdl`` view refuses at this release."""
    if dialect == "cdl":
        raise _refuse("dialect", "a cdl view is not read at this release; its derivation lands with "
                                 "its writer")
    if dialect not in _DIALECTS or netlist_kind not in _NETLIST_KINDS:
        raise _refuse("view_shape", f"({dialect!r}, {netlist_kind!r}) is not a dialect and netlist kind")
    source = Path(source)
    document = _json(source, "view or record")
    if isinstance(document, dict) and document.get("schema") == _RECORD_SCHEMA:
        view = _from_record(source, document, dialect, netlist_kind)
    else:
        view = _shape(document)
        if (view["dialect"], view["netlist_kind"]) != (dialect, netlist_kind):
            raise _refuse("selector", f"{source} is a ({view['dialect']}, {view['netlist_kind']}) view")
    root = source.parent
    deck, (pins, local, devices) = _deck(view, root)
    for field, derived, meta, ordered in (
            ("pins", pins, [p["name"] for p in view["pins"]], True),
            ("local_subckts", local, view["local_subckts"], False),
            ("devices", devices, view["devices"], False)):
        a, b = [_fold(x) for x in derived], [_fold(x) for x in meta]
        if (a != b) if ordered else (sorted(a) != sorted(b)):
            raise _refuse("view_differs", f"{field} is {meta} and the deck text gives {derived}", field)
    return TransistorView(view, deck, root)
