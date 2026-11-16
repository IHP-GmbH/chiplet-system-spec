# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The connectivity form a schematic front end writes for the schematic import.

One JSON file per edited sheet, written by the tool's own front end (KiCad
today; Xschem and Qucs-S later) and read only here, so neither side imports the
other. It holds the top (system) sheet only::

    format             "chiplet_system.connectivity"
    version            "1.1", under the family version policy ("1.0" reads as
                       a generated sheet)
    sheet              "generated" (the default) or "blank"
    base_sha256        the base .chiplet digest, as read from the edited sheet
    generator_report   {path, sha256} of the report the generator wrote
    library            {path, sha256, name?} of the symbol library (blank only;
                       name defaults to the file's stem)
    front_end          {tool, tool_version, reader, inputs: [{path, sha256}]}
    symbols            [{ref, identity, part?, pins: [terminal, ...]}]
    nets               [{name, auto_named, endpoints: [[ref, terminal], ...]}]
    refusals           [{code, message, ...}], the front end's own

A generated sheet is one a generator drew from a base: each symbol carries its
component id as ``identity``, and the base digest and the report say what was
drawn. A blank sheet is one a person drew from a symbol library, against a
given ``.chiplet`` the import is handed: ``base_sha256`` and
``generator_report`` are null, every symbol's reference designator is its
identity, and ``part`` (``{lib, name}``, the library and symbol it was placed
from) says which die it is. ``library`` records the library file the front end
was given; it does not prove the sheet used it (a tool may keep copies of its
symbols in the sheet), so the pin checks against the dies are what hold.

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

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ._cfio import cfio
from .errors import SystemSpecError

#: A format identifier: it keeps the package's old name, and renaming it is a
#: format change.
FORMAT = "chiplet_system.connectivity"
VERSION = "1.1"
#: The kinds of sheet a form describes.
GENERATED, BLANK = "generated", "blank"

#: The one name rule, stated identically in the generator's contract and
#: changed only in both places at once: component ids, terminals and net names
#: reach every schematic tool's netlist unchanged only inside this set.
NAME_RULE = re.compile(r"[A-Za-z0-9_.+-]+")


def is_name(value: Any) -> bool:
    return isinstance(value, str) and NAME_RULE.fullmatch(value) is not None


def refusal(code: str, message: str, **where: str) -> Dict[str, Any]:
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
    library: Optional[LibraryRef] = None    # a blank sheet's only

    @property
    def blank(self) -> bool:
        return self.sheet == BLANK


def _malformed(message: str) -> Dict[str, Any]:
    return refusal("malformed_form", f"connectivity form: {message}")


def _file_ref(value: Any, field: str, out: List[Dict[str, Any]]) -> Optional[FileRef]:
    if (isinstance(value, Mapping) and isinstance(value.get("path"), str) and value["path"]
            and isinstance(value.get("sha256"), str) and value["sha256"]):
        return FileRef(value["path"], value["sha256"])
    out.append(_malformed(f"{field} must be {{path, sha256}}, got {value!r}"))
    return None


def _library(value: Any, out: List[Dict[str, Any]]) -> Optional[LibraryRef]:
    if isinstance(value, Mapping) and all(
            isinstance(value.get(k), str) and value[k] for k in ("path", "sha256")):
        name = value.get("name") or PurePosixPath(value["path"].replace("\\", "/")).stem
        if isinstance(name, str) and name:
            return LibraryRef(name, value["path"], value["sha256"])
    out.append(_malformed(f"library must be {{path, sha256, name?}}, got {value!r}"))
    return None


def _part(value: Any) -> Optional[Part]:
    if isinstance(value, Mapping) and all(
            isinstance(value.get(k), str) and value[k] for k in ("lib", "name")):
        return Part(value["lib"], value["name"])
    return None


def _symbols(rows: Any, out: List[Dict[str, Any]], blank: bool) -> Tuple[Symbol, ...]:
    if not isinstance(rows, list):
        out.append(_malformed("symbols must be a list"))
        return ()
    symbols, refs = [], set()
    for row in rows:
        ref = row.get("ref") if isinstance(row, Mapping) else None
        identity = row.get("identity") if isinstance(row, Mapping) else None
        pins = row.get("pins") if isinstance(row, Mapping) else None
        has_part = isinstance(row, Mapping) and row.get("part") is not None
        part = _part(row.get("part")) if has_part else None
        if (not isinstance(ref, str) or not ref
                or not (identity is None or isinstance(identity, str))
                or not isinstance(pins, list)
                or not all(isinstance(p, str) and p for p in pins)
                or (has_part and part is None) or (blank and part is None)):
            needs = "ref, identity (or null), part {lib, name} and pins" if blank else \
                "ref, identity (or null) and pins"
            out.append(_malformed(f"symbol {row!r} needs {needs}"))
            continue
        if ref in refs and not blank:
            out.append(_malformed(f"symbol ref {ref} appears twice"))
            continue        # a blank sheet's import names the symbols that share it
        if len(set(pins)) != len(pins):
            out.append(_malformed(f"symbol {ref} lists a pin twice"))
            continue
        refs.add(ref)
        symbols.append(Symbol(ref, identity, tuple(pins), part))
    return tuple(symbols)


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
    if sheet not in (GENERATED, BLANK):
        raise SchematicImportRefused([_malformed(
            f"sheet is {sheet!r}, not {GENERATED!r} or {BLANK!r}")])
    if sheet == BLANK and version == "1.0":
        raise SchematicImportRefused([_malformed("a blank sheet needs version 1.1")])
    blank = sheet == BLANK
    base, report, library = doc.get("base_sha256"), None, None
    if blank:
        for key in ("base_sha256", "generator_report"):
            if doc.get(key) is not None:
                out.append(_malformed(f"a blank sheet has no {key}; it must be null"))
        base = None
        library = _library(doc.get("library"), out)
    else:
        if not isinstance(base, str) or not base:
            out.append(_malformed("base_sha256 must be a nonempty string"))
        report = _file_ref(doc.get("generator_report"), "generator_report", out)
    front_end = doc.get("front_end")
    if not isinstance(front_end, Mapping):
        out.append(_malformed("front_end must be an object"))
    symbols = _symbols(doc.get("symbols"), out, blank)
    nets = _nets(doc.get("nets"), out)
    refusals = _front_end_refusals(doc.get("refusals"), out)
    if out:
        raise SchematicImportRefused(out)
    return ConnectivityForm(base, report, dict(front_end), symbols, nets, refusals,
                            sheet, library)


__all__ = [
    "FORMAT",
    "VERSION",
    "GENERATED",
    "BLANK",
    "NAME_RULE",
    "is_name",
    "refusal",
    "SchematicImportRefused",
    "FileRef",
    "LibraryRef",
    "Part",
    "Symbol",
    "Net",
    "ConnectivityForm",
    "parse_form",
]
