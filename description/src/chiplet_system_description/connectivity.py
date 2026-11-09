# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The connectivity form a schematic front end writes for the schematic import.

One JSON file per edited sheet, written by the tool's own front end (KiCad
today; Xschem and Qucs-S later) and read only here, so neither side imports the
other. It holds the top (system) sheet only::

    format             "chiplet_system.connectivity"
    version            "1.3", under the family version policy ("1.0" reads as
                       a generated sheet; "blank" needs 1.1, "classic" 1.2)
    sheet              "generated" (the default), "blank" or "classic"
    base_sha256        the base .chiplet digest, as read from the edited sheet
    generator_report   {path, sha256} of the report the generator wrote
    library            {path, sha256, name?} of the die symbol library (blank
                       and classic only; name defaults to the file's stem)
    system_library     {path, sha256, name?} of the system symbol library,
                       the rails and the external pads (classic only)
    front_end          {tool, tool_version, reader, inputs: [{path, sha256}]}
    symbols            [{ref, identity, part?, key?, value?, footprint?,
                       fields?, units?, jumpers?, pins: [terminal, ...]}].
                       key, value, footprint and fields are read from 1.2:
                       required on a classic
                       sheet, and on a blank or generated sheet all four or
                       none. At 1.0 and 1.1, key, value and footprint are
                       ignored and fields is malformed. key is ``/`` plus the
                       symbol uuid (the root sheet is not part of it). value
                       and footprint are verbatim, ``~`` included. fields is
                       every symbol field as a raw string; a classic import
                       reads Die, Method, Thickness_um and Edge from it
    nets               [{name, auto_named, endpoints: [[ref, terminal], ...]}];
                       a name is the netlist's own text, escaped forms and a
                       one-pin ``unconnected-(...)`` net included, and nothing
                       here unescapes or drops one
    rails              [{name, class, volts}] (classic only)
    open               [[ref, terminal], ...], pins flagged no-connect
                       (classic only)
    refusals           [{code, message, ...}], the front end's own

Version 1.3 adds two keys to each classic symbol, ``units`` (one
``{name, pins}`` per unit of its library symbol) and ``jumpers`` (its
``duplicate_pin_numbers_are_jumpers``), which gds_to_kicad's classic reader
writes for the board interposer-pnr draws against the sheet. This module reads
neither: the import takes no unit, so a 1.3 form reads as a 1.2 one, and a
classic sheet stays readable from 1.2.

A generated sheet is one a generator drew from a base: each symbol carries its
component id as ``identity``, and the base digest and the report say what was
drawn. A blank sheet is one a person drew from a symbol library, against a
given ``.chiplet`` the import is handed: ``base_sha256`` and
``generator_report`` are null, every symbol's reference designator is its
identity, and ``part`` (``{lib, name}``, the library and symbol it was placed
from) says which die it is. ``library`` records the library file the front end
was given; it does not prove the sheet used it (a tool may keep copies of its
symbols in the sheet), so the pin checks against the dies are what hold.

A classic sheet (1.2) is a blank sheet that also says what the system is: no
given file stands behind it. Its symbols are read as a blank sheet's, and each
carries ``key``, ``value``, ``footprint`` and ``fields``. ``key`` is ``/`` plus
the symbol's uuid and nothing else: KiCad leaves the root sheet out of the
footprint path, and a second uuid is a different path. ``value`` and
``footprint`` are copied verbatim, so an empty value stays ``~``. ``fields``
is every symbol field as a raw string; which of them mean anything is the
import's to judge. The four are read from 1.2. A 1.0 or 1.1 form ignores
``key``, ``value`` and ``footprint``, and ``fields`` on a non-classic symbol
is malformed, as it was before they existed. At 1.2 a blank or generated
symbol carries all four or none; a partial set is malformed. ``rails`` are the power
symbols drawn from ``system_library``: ``class`` is ``"power"`` or
``"ground"`` and ``volts`` the written plain decimal (above 0), or null,
always null on a ground. ``open`` lists the terminals a no-connect flag marks.
``system_library``, ``rails`` and ``open`` appear on no other sheet. A net's
name is kept as the netlist wrote it.

This module checks the SHAPE of the form and nothing else: whether a ref is a
component, a terminal is on its die, or a name is legal is the import's policy
(:func:`chiplet_system_description.schematic_import.read_sheet`), decided once for
every tool. Paths are relative to the form's directory when the two share a root below ``/`` and
absolute otherwise.

A refusal is a record ``{code, message, component?, terminal?, net?}``, the
shape the KiCad generator uses, and every one is collected before
:class:`SchematicImportRefused` is raised. Tests assert codes, never messages.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple

from ._cfio import cfio
from .errors import SystemSpecError

#: A format identifier: it keeps the package's old name, and renaming it is a
#: format change.
FORMAT = "chiplet_system.connectivity"
VERSION = "1.3"
#: The kinds of sheet a form describes.
GENERATED, BLANK, CLASSIC = "generated", "blank", "classic"
#: The lowest minor each kind is written at.
_SINCE = {GENERATED: 0, BLANK: 1, CLASSIC: 2}
#: The rail classes a classic sheet draws.
POWER, GROUND = "power", "ground"
#: The system library's carrier-pad symbol. The classic import and the sheet
#: net map both match a symbol by this part name.
EXT_PAD = "EXT_PAD"
#: The keys only a classic sheet carries.
_CLASSIC_KEYS = ("system_library", "rails", "open")
#: A symbol's footprint path on the root sheet: ``/`` plus its own uuid.
#: A second uuid would be the root sheet, which KiCad leaves out of the path.
_KEY = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
#: A rail's volts as written: a plain decimal, checked above 0 apart.
_VOLTS = re.compile(r"[0-9]+(?:\.[0-9]+)?")

#: The plain name rule. chiplet-cosim reads component ids, terminals and net
#: names with it, since nothing wider has been measured to reach Xschem or CDL
#: unchanged, and the classic import reads a rail's name with it, less ``.``. A
#: KiCad sheet's net names are read with ``chiplet_format_io.sheet_names``,
#: which also admits ``[N]`` subscripts.
NAME_RULE = re.compile(r"[A-Za-z0-9_.+-]+")


def is_name(value: Any) -> bool:
    return isinstance(value, str) and NAME_RULE.fullmatch(value) is not None


def refusal(code: str, message: str, **where: Any) -> Dict[str, Any]:
    return {"code": code, "message": message, **where}


class SchematicImportRefused(SystemSpecError):
    """The import cannot write a revision. ``refusals`` lists every reason."""

    def __init__(self, refusals: List[Dict[str, Any]]):
        self.refusals = list(refusals)
        super().__init__("; ".join(r["message"] for r in self.refusals))

    @property
    def codes(self) -> List[str]:
        return sorted({r["code"] for r in self.refusals})


@dataclass(frozen=True)
class FileRef:
    path: str
    sha256: str


@dataclass(frozen=True)
class LibraryRef:
    name: str
    path: str
    sha256: str


@dataclass(frozen=True)
class Part:
    lib: str
    name: str


@dataclass(frozen=True)
class Symbol:
    ref: str
    identity: Optional[str]
    pins: Tuple[str, ...]
    part: Optional[Part] = None
    #: Read only at form 1.2. Required together on a classic sheet. On a
    #: blank or generated sheet, all four or none. ``fields`` is every
    #: symbol field, raw pairs, so a symbol stays hashable.
    key: Optional[str] = None
    value: Optional[str] = None
    footprint: Optional[str] = None
    fields: Optional[Tuple[Tuple[str, str], ...]] = None


@dataclass(frozen=True)
class Rail:
    name: str
    net_class: str                  # POWER or GROUND
    volts: Optional[str]            # the written decimal; None on a ground


@dataclass(frozen=True)
class Net:
    name: str
    auto_named: bool
    endpoints: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class ConnectivityForm:
    base_sha256: Optional[str]              # None on a blank sheet
    generator_report: Optional[FileRef]     # None on a blank sheet
    front_end: Dict[str, Any]
    symbols: Tuple[Symbol, ...]
    nets: Tuple[Net, ...]
    refusals: Tuple[Dict[str, Any], ...]
    sheet: str = GENERATED
    library: Optional[LibraryRef] = None    # a blank or classic sheet's only
    system_library: Optional[LibraryRef] = None     # the rest, a classic sheet's only
    rails: Tuple[Rail, ...] = ()
    open: Tuple[Tuple[str, str], ...] = ()

    @property
    def generated(self) -> bool:
        return self.sheet == GENERATED

    @property
    def blank(self) -> bool:
        return self.sheet == BLANK

    @property
    def classic(self) -> bool:
        return self.sheet == CLASSIC


def _malformed(message: str) -> Dict[str, Any]:
    return refusal("malformed_form", f"connectivity form: {message}")


def _file_ref(value: Any, field: str, out: List[Dict[str, Any]]) -> Optional[FileRef]:
    if (isinstance(value, Mapping) and isinstance(value.get("path"), str) and value["path"]
            and isinstance(value.get("sha256"), str) and value["sha256"]):
        return FileRef(value["path"], value["sha256"])
    out.append(_malformed(f"{field} must be {{path, sha256}}, got {value!r}"))
    return None


def _library(value: Any, out: List[Dict[str, Any]],
             field: str = "library") -> Optional[LibraryRef]:
    if isinstance(value, Mapping) and all(
            isinstance(value.get(k), str) and value[k] for k in ("path", "sha256")):
        name = value.get("name") or PurePosixPath(value["path"].replace("\\", "/")).stem
        if isinstance(name, str) and name:
            return LibraryRef(name, value["path"], value["sha256"])
    out.append(_malformed(f"{field} must be {{path, sha256, name?}}, got {value!r}"))
    return None


def _part(value: Any) -> Optional[Part]:
    if isinstance(value, Mapping) and all(
            isinstance(value.get(k), str) and value[k] for k in ("lib", "name")):
        return Part(value["lib"], value["name"])
    return None


def _string(row: Mapping[str, Any], name: str) -> Tuple[bool, Optional[str]]:
    """``(True, text)`` when ``name`` is a string, ``(True, None)`` when it is
    absent, ``(False, None)`` when it is present and not a string."""
    if name not in row:
        return True, None
    value = row[name]
    if isinstance(value, str):
        return True, value
    return False, None


def _fields(value: Any) -> Optional[Tuple[Tuple[str, str], ...]]:
    """Every field as raw pairs, or None when ``value`` is not that map."""
    if not isinstance(value, Mapping) or not all(
            isinstance(k, str) and k and isinstance(v, str) for k, v in value.items()):
        return None
    return tuple(value.items())


_IDENTITY = ("key", "value", "footprint", "fields")


class _Shape(NamedTuple):
    refusal: Optional[Dict[str, Any]]
    ref: str = ""
    identity: Optional[str] = None
    pins: Tuple[str, ...] = ()
    part: Optional[Part] = None


class _Identity(NamedTuple):
    refusal: Optional[Dict[str, Any]]
    key: Optional[str] = None
    value: Optional[str] = None
    footprint: Optional[str] = None
    fields: Optional[Tuple[Tuple[str, str], ...]] = None


def _needs(row: Any, *, drawn: bool, classic: bool, fields: bool) -> Dict[str, Any]:
    """The one wording for a symbol that is not the shape this minor reads."""
    if fields:
        body = ("ref, identity (or null), part {lib, name} and pins, and no fields" if drawn else
                "ref, identity (or null) and pins, and no fields")
    elif classic:
        body = "ref, identity null, part {lib, name} and pins"
    elif drawn:
        body = "ref, identity (or null), part {lib, name} and pins"
    else:
        body = "ref, identity (or null) and pins"
    return _malformed(f"symbol {row!r} needs {body}")


def _check_ref(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    ref = row.get("ref")
    if isinstance(ref, str) and ref:
        return None
    return _malformed(f"symbol {row!r} needs a ref")


def _check_identity_slot(row: Mapping[str, Any], classic: bool) -> Optional[Dict[str, Any]]:
    identity = row.get("identity")
    if classic and identity is not None:
        return _malformed(f"symbol {row!r} needs identity null")
    if identity is None or isinstance(identity, str):
        return None
    return _malformed(f"symbol {row!r} needs identity null or a string")


def _check_pins(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    pins = row.get("pins")
    if isinstance(pins, list) and all(isinstance(pin, str) and pin for pin in pins):
        return None
    return _malformed(f"symbol {row!r} needs pins, a list of names")


def _check_part(row: Mapping[str, Any], drawn: bool) -> Optional[Dict[str, Any]]:
    raw = row.get("part")
    if raw is None and not drawn:
        return None
    if _part(raw) is None:
        return _malformed(f"symbol {row!r} needs part {{lib, name}}")
    return None


def _symbol_shape(row: Mapping[str, Any], drawn: bool, classic: bool) -> _Shape:
    """The ref, identity, pins and part. The first failing check is the refusal."""
    refusal = (_check_ref(row) or _check_identity_slot(row, classic) or _check_pins(row)
               or _check_part(row, drawn))
    if refusal is not None:
        return _Shape(refusal)
    raw = row.get("part")
    return _Shape(None, row["ref"], row.get("identity"), tuple(row["pins"]),
                  _part(raw) if raw is not None else None)


def _identity_message(row: Mapping[str, Any]) -> Dict[str, Any]:
    return _malformed(
        f"symbol {row!r} needs key (`/` plus the symbol uuid), value, footprint "
        "and fields (a map of raw strings)")


def _symbol_identity(row: Mapping[str, Any], minor: int, classic: bool, drawn: bool) -> _Identity:
    """The four identity values. Below 1.2 the three path keys are ignored and
    fields is refused, in the same sentence as a bad shape."""
    if minor < 2:
        if "fields" in row:
            return _Identity(_needs(row, drawn=drawn, classic=False, fields=True))
        return _Identity(None)
    present = [key for key in _IDENTITY if key in row]
    if not classic and not present:
        return _Identity(None)
    if not classic and len(present) != len(_IDENTITY):
        return _Identity(_malformed(
            f"symbol {row!r} carries some of key, value, footprint and fields; "
            "it needs all four or none"))
    key_ok, key = _string(row, "key")
    value_ok, value = _string(row, "value")
    foot_ok, footprint = _string(row, "footprint")
    fields = _fields(row.get("fields")) if "fields" in row else None
    if (not key_ok or not value_ok or not foot_ok or key is None or value is None
            or footprint is None or fields is None or _KEY.fullmatch(key) is None):
        return _Identity(_identity_message(row))
    return _Identity(None, key, value, footprint, fields)


def _symbols(rows: Any, out: List[Dict[str, Any]], sheet: str,
             minor: int) -> Tuple[Symbol, ...]:
    if not isinstance(rows, list):
        out.append(_malformed("symbols must be a list"))
        return ()
    drawn, classic = sheet != GENERATED, sheet == CLASSIC
    symbols, refs, keys = [], set(), set()
    for row in rows:
        if not isinstance(row, Mapping):
            out.append(_needs(row, drawn=drawn, classic=classic, fields=False))
            continue
        parsed = _symbol_shape(row, drawn, classic)
        if parsed.refusal is not None:
            out.append(parsed.refusal)
            continue
        ref, identity, pins, part = parsed.ref, parsed.identity, parsed.pins, parsed.part
        ident = _symbol_identity(row, minor, classic, drawn)
        if ident.refusal is not None:
            out.append(ident.refusal)
            continue
        key, value, footprint, fields = ident.key, ident.value, ident.footprint, ident.fields
        if ref in refs and not drawn:
            out.append(_malformed(f"symbol ref {ref} appears twice"))
            continue        # a drawn sheet's import names the symbols that share it
        if key is not None and key in keys:
            out.append(_malformed(f"symbol key {key} appears twice"))
            continue
        if len(set(pins)) != len(pins):
            out.append(_malformed(f"symbol {ref} lists a pin twice"))
            continue
        refs.add(ref)
        if key is not None:
            keys.add(key)
        symbols.append(Symbol(ref, identity, pins, part, key, value, footprint, fields))
    return tuple(symbols)


def _volts(value: Any) -> bool:
    return (isinstance(value, str) and _VOLTS.fullmatch(value) is not None
            and value.strip("0.") != "")


def _rails(rows: Any, out: List[Dict[str, Any]]) -> Tuple[Rail, ...]:
    if not isinstance(rows, list):
        out.append(_malformed("rails must be a list"))
        return ()
    rails, names = [], set()
    for row in rows:
        name = row.get("name") if isinstance(row, Mapping) else None
        net_class = row.get("class") if isinstance(row, Mapping) else None
        volts = row.get("volts") if isinstance(row, Mapping) else None
        if (not isinstance(row, Mapping) or not isinstance(name, str) or not name
                or net_class not in (POWER, GROUND) or "volts" not in row
                or not (volts is None or (net_class == POWER and _volts(volts)))):
            out.append(_malformed(
                f"rail {row!r} needs name, class ({POWER!r} or {GROUND!r}) and volts: "
                "a plain decimal above 0 on a power rail, or null; null on a ground"))
            continue
        if name in names:
            out.append(_malformed(f"rail {name} appears twice"))
            continue
        names.add(name)
        rails.append(Rail(name, net_class, volts))
    return tuple(rails)


def _terminals(rows: Any, field: str,
               out: List[Dict[str, Any]]) -> Tuple[Tuple[str, str], ...]:
    if not isinstance(rows, list) or not all(
            isinstance(t, list) and len(t) == 2 and all(isinstance(p, str) and p for p in t)
            for t in rows):
        out.append(_malformed(f"{field} must be a list of [ref, terminal]"))
        return ()
    terminals = tuple((ref, pin) for ref, pin in rows)
    if len(set(terminals)) != len(terminals):
        out.append(_malformed(f"{field} lists a terminal twice"))
        return ()
    return terminals


def _nets(rows: Any, out: List[Dict[str, Any]]) -> Tuple[Net, ...]:
    if not isinstance(rows, list):
        out.append(_malformed("nets must be a list"))
        return ()
    nets, names = [], set()
    for row in rows:
        name = row.get("name") if isinstance(row, Mapping) else None
        auto = row.get("auto_named") if isinstance(row, Mapping) else None
        endpoints = row.get("endpoints") if isinstance(row, Mapping) else None
        if (not isinstance(name, str) or not name or not isinstance(auto, bool)
                or not isinstance(endpoints, list) or not endpoints
                or not all(isinstance(ep, list) and len(ep) == 2
                           and all(isinstance(part, str) and part for part in ep)
                           for ep in endpoints)):
            out.append(_malformed(
                f"net {row!r} needs name, auto_named and a nonempty list of [ref, terminal]"))
            continue
        if name in names:
            out.append(_malformed(f"net {name} appears twice"))
            continue
        names.add(name)
        nets.append(Net(name, auto, tuple((ref, pin) for ref, pin in endpoints)))
    return tuple(nets)


def _front_end_refusals(rows: Any, out: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], ...]:
    if rows is None:
        return ()
    if not isinstance(rows, list) or not all(
            isinstance(r, Mapping) and isinstance(r.get("code"), str) and r["code"]
            and isinstance(r.get("message"), str) for r in rows):
        out.append(_malformed("refusals must be a list of {code, message}"))
        return ()
    return tuple(dict(r) for r in rows)


def parse_form(doc: Any) -> ConnectivityForm:
    """Check the form's shape; raise :class:`SchematicImportRefused` listing
    every defect (code ``malformed_form``) or return the parsed form."""
    if not isinstance(doc, Mapping):
        raise SchematicImportRefused([_malformed("not a JSON object")])
    if doc.get("format") != FORMAT:
        raise SchematicImportRefused([_malformed(
            f"format is {doc.get('format')!r}, not {FORMAT!r}")])
    try:
        version = cfio.check_contract_version(doc.get("version"), VERSION, name=FORMAT)
    except cfio.ContractVersionError as exc:
        raise SchematicImportRefused([_malformed(str(exc))]) from exc
    out: List[Dict[str, Any]] = []
    sheet = doc.get("sheet", GENERATED)
    if not isinstance(sheet, str) or sheet not in _SINCE:
        raise SchematicImportRefused([_malformed(
            f"sheet is {sheet!r}, not {GENERATED!r}, {BLANK!r} or {CLASSIC!r}")])
    if int(version.split(".")[1]) < _SINCE[sheet]:
        raise SchematicImportRefused([_malformed(
            f"a {sheet} sheet needs version 1.{_SINCE[sheet]}")])
    classic = sheet == CLASSIC
    base, report, library, system_library = doc.get("base_sha256"), None, None, None
    rails: Tuple[Rail, ...] = ()
    opened: Tuple[Tuple[str, str], ...] = ()
    if sheet != GENERATED:
        for key in ("base_sha256", "generator_report"):
            if doc.get(key) is not None:
                out.append(_malformed(f"a {sheet} sheet has no {key}; it must be null"))
        base = None
        library = _library(doc.get("library"), out)
    else:
        if not isinstance(base, str) or not base:
            out.append(_malformed("base_sha256 must be a nonempty string"))
        report = _file_ref(doc.get("generator_report"), "generator_report", out)
    if classic:
        system_library = _library(doc.get("system_library"), out, "system_library")
        rails = _rails(doc.get("rails"), out)
        opened = _terminals(doc.get("open"), "open", out)
    else:
        out.extend(_malformed(f"a {sheet} sheet has no {key}; only a classic sheet does")
                   for key in _CLASSIC_KEYS if key in doc)
    front_end = doc.get("front_end")
    if not isinstance(front_end, Mapping):
        out.append(_malformed("front_end must be an object"))
    symbols = _symbols(doc.get("symbols"), out, sheet, int(version.split(".")[1]))
    nets = _nets(doc.get("nets"), out)
    refusals = _front_end_refusals(doc.get("refusals"), out)
    if out:
        raise SchematicImportRefused(out)
    return ConnectivityForm(base, report, dict(front_end), symbols, nets, refusals,
                            sheet, library, system_library, rails, opened)


def _no_constant(name: str):
    raise ValueError(f"{name} is not a JSON value")


def load_form(form_path, kind: Optional[str] = None) -> ConnectivityForm:
    """The connectivity form in ``form_path``, or :class:`SchematicImportRefused`
    when it is no form, or, with ``kind``, a form of another kind of sheet
    (``sheet_kind``, naming the reader that takes it)."""
    try:
        doc = json.loads(Path(form_path).read_bytes().decode("utf-8"),
                         parse_constant=_no_constant)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SchematicImportRefused([refusal(
            "malformed_form", f"connectivity form {form_path}: {exc}")]) from exc
    form = parse_form(doc)
    if kind is not None and form.sheet != kind:
        raise SchematicImportRefused([refusal(
            "sheet_kind",
            f"{form_path} is a {form.sheet} sheet, which is read "
            + ("against the file it was drawn on" if form.blank else
               "into its project file, by read_classic_sheet" if form.classic else
               "against the run's design.chiplet"))])
    return form


__all__ = [
    "FORMAT",
    "VERSION",
    "GENERATED",
    "BLANK",
    "CLASSIC",
    "POWER",
    "GROUND",
    "NAME_RULE",
    "is_name",
    "refusal",
    "SchematicImportRefused",
    "FileRef",
    "LibraryRef",
    "Part",
    "Symbol",
    "Rail",
    "Net",
    "ConnectivityForm",
    "parse_form",
    "load_form",
]
