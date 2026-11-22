# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The content form of a die's build record and contract, and its digest.

A die's build record and die contract name the die as it was built: its
layout, its inputs, the checks that ran on it and what the writer that emitted
it was. Two of those records can describe one and the same die and still differ
byte for byte: the writer's release and revision, the run directory's name, the
provenance of the PDK's materialized copy, a LibreLane step number in a path.
A reviewer who signed off on the die signed off on its content, not on those
bytes, so a key that asks for the die again must be a digest of the content
alone. This module is that form, ``die-content/v2``, in one place for the
writer that emits a record and the reader that keys it.

The form is data with a name. :data:`BUILD_RECORD_OUT` lists the record fields
that leave; :data:`CONTRACT_OUT` and :data:`CONTRACT_REPLACED` do the same for
the contract, whose ``build_record.sha256`` is replaced by the record's content
digest. The ``bound`` rows of a record, each a file of the run the record
binds, are decided by :data:`BOUND_TABLE`, one row per writer and per file or
path pattern: ``keep`` keeps the row with its digest, ``project`` keeps the
row with the digest of the file's projection (which the writer computed with
the run in hand, :func:`project`, and wrote beside the file's digest as
``content_sha256``; the row then loses ``sha256``), and ``leave`` drops the
row. A row the table does not name, a writer the table does not name and a
projected row without a projection are all ``keep``: the table can only take
a file out on purpose, never by omission. The rows that stay are sorted by
path, two rows of one path in the record's order. The step number LibreLane
puts on a step directory leaves every path that stays (:func:`strip_step`),
and every digest equal to the record's own layout digest becomes the word
``layout``, so the layout is keyed once, by the canon digest the gate's
subject already holds, and a digest of some other layout stays raw.

A projected file can name, by digest, files a tool stamped with the time it
wrote them: a core's GDS, SPEF and SDF, and the JSON files that record those
digests. Version 2 keys them by content too. The writer computes a digest
map with the files in hand (:func:`digest_map`): each stamped file's digest
becomes ``canon:`` and the digest of the file without its time fields
(:func:`canon_sha256`, by the kind :func:`file_kind` reads from the bytes),
and each JSON file that records a mapped digest becomes ``content:`` and the
digest of its canonical JSON with those digests replaced. The candidates are
the objects of the projected file that hold a digest with a path, and,
recursively, those of every candidate that parses as a JSON object. The row
records the map beside its ``content_sha256`` (:data:`DIGEST_MAP`), and
:func:`project` replaces the digests it maps, so a reader holding the row and
the projected file recomputes the digest without the stamped files, and a
reader holding them checks the map (:func:`check_digest_map`).

The two digests, :func:`build_record_content_sha256` and
:func:`contract_content_sha256`, hash the canonical JSON every signature in
the flow hashes (:func:`canonical_json`). Changing any of the lists above is
a new form name, never an edit in place: the shared-names golden pins them,
and a signature records the form it was keyed under.

Release 1.26.0 added this module, as ``die-content/v1``; release 1.27.0
made it ``die-content/v2``, adding the digest map and the three canons.
"""
from __future__ import annotations

import collections
import copy
import hashlib
import json
import os
import re
from fnmatch import fnmatchcase
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from . import ChipletFormatError
from .ready_die_names import EMITTED_RECORD_SCHEMA

__all__ = [
    "DIE_CONTENT_FORM",
    "BUILD_RECORD_OUT",
    "CONTRACT_OUT",
    "CONTRACT_REPLACED",
    "BUILD_DIE",
    "PADRING",
    "WRITERS",
    "PADRING_RECORD_SCHEMA",
    "BOUND_TABLE",
    "canonical_json",
    "canonical_sha256",
    "writer_of",
    "strip_step",
    "decision",
    "project",
    "DIGEST_MAP",
    "CANON",
    "CONTENT",
    "GDS",
    "SPEF",
    "SDF",
    "SPEF_DROPPED",
    "SDF_DROPPED",
    "CANDIDATE_PATHS",
    "CanonError",
    "DigestMapRefused",
    "CandidateUnreadable",
    "CandidateChanged",
    "CandidateNamedTwice",
    "CandidateMalformed",
    "DigestCycle",
    "file_kind",
    "canon",
    "canon_sha256",
    "candidates",
    "digest_map",
    "check_digest_map",
    "build_record_content",
    "build_record_content_sha256",
    "contract_content",
    "contract_content_sha256",
]

#: The name of this form. A key records it beside the digests it keyed.
DIE_CONTENT_FORM = "die-content/v2"

#: Record fields that leave, as document paths: what the writer was, the run
#: directory's name, the provenance of the PDK's materialized copy. An absent
#: path is skipped, so a record written before a field existed reads; a path
#: whose parent is not an object is refused.
BUILD_RECORD_OUT = (
    ("emitted_under",),
    ("build_revision",),
    ("process", "pdk_version_source"),
    ("process", "pdk_version_from"),
    ("pdk_version_from",),
    ("run",),
    ("librelane_version",),
)
#: Contract fields that leave.
CONTRACT_OUT = (("generator",),)
#: The contract field replaced by the build record's content digest.
CONTRACT_REPLACED = ("build_record", "sha256")

#: The writers :data:`BOUND_TABLE` names, by the record schema each writes.
BUILD_DIE = "build-die"
PADRING = "padring"
WRITERS = (BUILD_DIE, PADRING)
#: The schema of a padring record (the build-die schema is
#: :data:`~chiplet_format_io.ready_die_names.EMITTED_RECORD_SCHEMA`).
PADRING_RECORD_SCHEMA = "chiplet-flow-external-build-record/v1"

#: The decisions, as :func:`decision` returns them.
_KEEP = "keep"
_PROJECT = "project"
_LEAVE = "leave"

#: One row per writer and per bound file or path pattern: (writer, pattern,
#: decision). A pattern is matched against the row's path after
#: :func:`strip_step`, segment by segment, so ``*`` never crosses a ``/``; the
#: first match wins; no match is ``keep``.
BOUND_TABLE = (
    # build-die: the declared design and what the flow observed, projected.
    (BUILD_DIE, "resolved.json", _PROJECT),
    (BUILD_DIE, "chiplet.inputs-before.json", _PROJECT),
    (BUILD_DIE, "chiplet.build-observations.json", _PROJECT),
    # build-die: the placed and routed die, the layout the checks read, the
    # netlists LVS compared.
    (BUILD_DIE, "final/def/*.def", _KEEP),
    (BUILD_DIE, "chipletflow-bondpadfiller/rewritten/*.gds", _KEEP),
    (BUILD_DIE, "openroad-writecdl/*.cdl", _KEEP),
    (BUILD_DIE, "chipletflow-bondpadlvs/cell-cdls/*/*.cdl", _KEEP),
    # build-die: step states, configuration, reports, logs and databases; what
    # each check judged stays in the record's checks block.
    (BUILD_DIE, "klayout-drc/state_in.json", _LEAVE),
    (BUILD_DIE, "klayout-drc/state_out.json", _LEAVE),
    (BUILD_DIE, "klayout-drc/config.json", _LEAVE),
    (BUILD_DIE, "klayout-density/state_in.json", _LEAVE),
    (BUILD_DIE, "klayout-density/state_out.json", _LEAVE),
    (BUILD_DIE, "klayout-density/config.json", _LEAVE),
    (BUILD_DIE, "klayout-antenna/state_in.json", _LEAVE),
    (BUILD_DIE, "klayout-antenna/state_out.json", _LEAVE),
    (BUILD_DIE, "klayout-antenna/config.json", _LEAVE),
    (BUILD_DIE, "final/metrics.json", _LEAVE),
    (BUILD_DIE, "chipletflow-bondpadlvs/fill-normalization.json", _LEAVE),
    (BUILD_DIE, "chipletflow-bondpadlvs/config.json", _LEAVE),
    (BUILD_DIE, "chipletflow-bondpadlvs/state_in.json", _LEAVE),
    (BUILD_DIE, "openroad-writecdl/state_out.json", _LEAVE),
    (BUILD_DIE, "chipletflow-bondpadlvs/lvs-deck.log", _LEAVE),
    (BUILD_DIE, "chipletflow-bondpadlvs/chipletflow-bondpadlvs.log", _LEAVE),
    (BUILD_DIE, "chipletflow-bondpadlvs/reports/lvs.klayout.lvsdb", _LEAVE),
    (BUILD_DIE, "chipletflow-bondpadlvs/*.spice", _LEAVE),
    (BUILD_DIE, "chipletflow-bondpadlvs/deck-tree.json", _LEAVE),
    (BUILD_DIE, "chipletflow-bondpadlvs/*.lvs-view.gds", _LEAVE),
    # padring: the configuration projected, the report left, the placed pads
    # and their nets kept; the pad master LEFs and the source netlist are kept
    # by no match.
    (PADRING, "resolved.json", _PROJECT),
    (PADRING, "final/metrics.json", _LEAVE),
    (PADRING, "final/def/*.def", _KEEP),
    (PADRING, "final/pnl/*.pnl.v", _KEEP),
)

#: What a projection takes out of each projected file, by its stripped path:
#: the tool's version, the run directory, and the digests of files that are
#: bound on their own terms or belong to the implementation.
_PROJECT_OUT = {
    "resolved.json": (("meta", "librelane_version"),),
    "chiplet.inputs-before.json": (("run_dir",), ("shared_implementation",)),
    "chiplet.build-observations.json": (("final_artifacts",), ("maintained_step_sources",)),
}
#: The roots a projection rewrites absolute paths under, to ``<name>:<path>``.
_ROOT_NAMES = ("run", "design", "pdk")
#: The word that stands for the record's own layout digest in the form.
_LAYOUT = "layout"
#: A LibreLane step number on the first segment of a run-relative path.
_STEP = re.compile(r"^[0-9]+-(?=[A-Za-z])")

#: The field of a projected bound row that holds its digest map: each raw
#: digest the projected file names and the map replaces, by its value.
DIGEST_MAP = "digest_map"
#: The two kinds of value a digest map holds, each followed by a hex digest.
CANON = "canon:"
CONTENT = "content:"
#: The kinds of stamped file, as :func:`file_kind` reads them from the bytes.
GDS = "GDS"
SPEF = "SPEF"
SDF = "SDF"
#: What each canon takes out: the header lines of a SPEF and the header
#: entries of an SDF that say when, and by which program, the file was
#: written. A GDS loses the times of its BGNLIB and BGNSTR records.
SPEF_DROPPED = ("*DATE", "*VENDOR", "*PROGRAM", "*VERSION")
SDF_DROPPED = ("DATE", "VENDOR", "PROGRAM", "VERSION")
#: The keys that make an object holding a ``sha256`` a candidate: the first
#: of these, in this order, whose value is an absolute path. A relative path,
#: a ``destination`` among them, makes no candidate.
CANDIDATE_PATHS = ("resolved_path", "path", "source")

#: A raw digest, as a map's key and after a map value's prefix.
_HEX = re.compile(r"[0-9a-f]{64}")
#: The first record of a GDS stream: HEADER, six bytes long.
_GDS_HEADER = b"\x00\x06\x00\x02"
#: The GDS record types whose payload is a time (BGNLIB, BGNSTR), and the
#: record that ends the stream (ENDLIB).
_GDS_TIMED = (0x01, 0x05)
_GDS_ENDLIB = 0x04
#: The SPEF keywords that end its header.
_SPEF_SECTIONS = (b"*NAME_MAP", b"*POWER_NETS", b"*GROUND_NETS", b"*PORTS", b"*PHYSICAL_PORTS",
                  b"*DEFINE", b"*PDEFINE", b"*VARIATION_PARAMETERS", b"*D_NET", b"*R_NET",
                  b"*D_PNET", b"*R_PNET")


def canonical_json(value: Any) -> bytes:
    """The bytes every signature hashes: sorted keys, no whitespace, ASCII,
    no NaN."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def canonical_sha256(value: Any) -> str:
    """The hex digest of :func:`canonical_json` of ``value``."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def writer_of(record: Any) -> Optional[str]:
    """Which writer of :data:`WRITERS` wrote ``record``, by its schema; None
    for any other schema, so every bound row of such a record is kept."""
    schema = record.get("schema") if isinstance(record, Mapping) else None
    if schema == EMITTED_RECORD_SCHEMA:
        return BUILD_DIE
    if schema == PADRING_RECORD_SCHEMA:
        return PADRING
    return None


def strip_step(path: str) -> str:
    """``path`` without a LibreLane step number on its first segment:
    ``60-klayout-drc/state_in.json`` reads ``klayout-drc/state_in.json``.
    Nothing else changes."""
    return _STEP.sub("", path, count=1)


def decision(writer: Optional[str], path: str) -> str:
    """The decision of :data:`BOUND_TABLE` for a bound row's ``path``, as the
    record writes it: ``keep``, ``project`` or ``leave``. The path takes
    :func:`strip_step` first. An unnamed writer and an unmatched path are
    ``keep``."""
    if writer is None:
        return _KEEP
    segments = strip_step(path).split("/")
    for row_writer, pattern, row_decision in BOUND_TABLE:
        if row_writer != writer:
            continue
        parts = pattern.split("/")
        if len(parts) == len(segments) and all(fnmatchcase(s, p) for s, p in zip(segments, parts)):
            return row_decision
    return _KEEP


def project(writer: Optional[str], path: str, document: Any, roots: Mapping[str, Any],
            digest_map: Optional[Mapping[str, str]] = None) -> dict:
    """The projection of a bound file the table projects: ``document`` (the
    file's JSON) without its out fields, with every absolute path under one
    of ``roots`` rewritten to ``<name>:<relative posix path>``.

    ``roots`` maps names among ``run``, ``design`` and ``pdk`` to absolute
    directories (str or PathLike; None for a root the writer does not have). A
    string value or dict key, at any depth, equal to a root becomes
    ``<name>:.``, and one under it becomes ``<name>:<the rest>``; the longest
    root wins; a path inside a longer string stays as written. Refused with
    :class:`~chiplet_format_io.ChipletFormatError`: a document that is not an
    object, a path whose decision is not ``project``, an unknown or relative
    root, and a rewritten key that collides with another key.

    ``digest_map`` is the row's :data:`DIGEST_MAP` (None for a row without
    one): every string value equal to one of its digests becomes its value.
    Refused too: an entry that is not a raw digest with a ``canon:`` or
    ``content:`` value, and an entry for a digest the document, without its
    out fields, does not name."""
    prefixes = _root_prefixes(roots)
    projected = _projected_input(writer, path, document)
    mapping = _check_map_form(path, projected, digest_map)
    return _replace_digests(_rewrite(projected, prefixes, path), mapping)


def _root_prefixes(roots: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """``(name, absolute root)`` pairs, longest root first, no trailing
    slash but on the root directory itself."""
    out = []
    for name, value in roots.items():
        if name not in _ROOT_NAMES:
            raise ChipletFormatError(f"roots: {name!r} is not one of {', '.join(_ROOT_NAMES)}")
        if value is None:
            continue
        try:
            root = os.fspath(value)
        except TypeError:
            root = value
        if not isinstance(root, str) or not os.path.isabs(root):
            raise ChipletFormatError(f"roots: {name} is not an absolute path: {root!r}")
        root = root.rstrip("/") or "/"
        if any(root == other for _, other in out):
            raise ChipletFormatError(f"roots: {name} is the same directory as another root: {root}")
        out.append((name, root))
    return sorted(out, key=lambda item: len(item[1]), reverse=True)


def _rewrite_string(value: str, prefixes: Iterable[Tuple[str, str]]) -> str:
    for name, root in prefixes:
        if value == root:
            return f"{name}:."
        base = root if root.endswith("/") else root + "/"
        if value.startswith(base):
            return f"{name}:{value[len(base):] or '.'}"
    return value


def _rewrite(node: Any, prefixes: List[Tuple[str, str]], where: str) -> Any:
    if isinstance(node, dict):
        out: Dict[Any, Any] = {}
        for key, value in node.items():
            new_key = _rewrite_string(key, prefixes) if isinstance(key, str) else key
            if new_key in out:
                raise ChipletFormatError(f"{where}: key {key!r} rewritten to {new_key!r} collides "
                                         "with another key")
            out[new_key] = _rewrite(value, prefixes, where)
        return out
    if isinstance(node, list):
        return [_rewrite(item, prefixes, where) for item in node]
    if isinstance(node, str):
        return _rewrite_string(node, prefixes)
    return node


def _remove(document: dict, path: Tuple[str, ...], where: str) -> None:
    """Remove ``path`` from ``document``: absent is skipped; a parent that is
    not an object is refused, naming the path."""
    node: Any = document
    for index, segment in enumerate(path):
        if not isinstance(node, dict):
            raise ChipletFormatError(
                f"{where}: {'.'.join(path[:index])} is not an object, so {'.'.join(path)} "
                "cannot be read")
        if segment not in node:
            return
        if index == len(path) - 1:
            del node[segment]
            return
        node = node[segment]


class CanonError(ChipletFormatError):
    """A file of a stamped kind whose canon cannot be computed: ``kind`` is
    :data:`GDS`, :data:`SPEF` or :data:`SDF`, ``reason`` a short phrase."""

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(f"{kind}: {reason}")
        self.kind = kind
        self.reason = reason


def file_kind(data: bytes) -> Optional[str]:
    """The stamped kind of a file, read from its first bytes: :data:`GDS` when
    its first record is HEADER, :data:`SPEF` when its first line starts
    ``*SPEF``, :data:`SDF` when it starts ``(DELAYFILE``; None for any other
    file, which the form keeps by its raw digest."""
    if data[:4] == _GDS_HEADER:
        return GDS
    if data.startswith(b"*SPEF"):
        return SPEF
    if data.startswith(b"(DELAYFILE"):
        return SDF
    return None


def _gds_canon(data: bytes) -> bytes:
    """The stream with the payload of each BGNLIB and BGNSTR record zeroed and
    every other byte kept. Zero padding after ENDLIB is kept when it is four
    bytes or more and left out when shorter, as packaging-flow's canon reads
    it, so the two homes of this canon give one digest."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        if i + 4 > n:
            raise CanonError(GDS, "the stream ends inside a record header")
        length = int.from_bytes(data[i:i + 2], "big")
        rtype = data[i + 2]
        if length < 4:
            raise CanonError(GDS, f"a record at byte {i} is shorter than its own header")
        if i + length > n:
            raise CanonError(GDS, "the stream ends inside a record")
        chunk = bytearray(data[i:i + length])
        if rtype in _GDS_TIMED:
            chunk[4:] = bytes(length - 4)
        out += chunk
        i += length
        if rtype == _GDS_ENDLIB:
            rest = data[i:]
            if rest.strip(b"\x00"):
                raise CanonError(GDS, "bytes other than padding follow the ENDLIB record")
            if len(rest) >= 4:
                out += rest
            return bytes(out)
    raise CanonError(GDS, "the stream ends before its ENDLIB record")


def _spef_canon(data: bytes) -> bytes:
    """The file without the header lines whose keyword is one of
    :data:`SPEF_DROPPED`; the header ends at the first section keyword."""
    out = bytearray()
    start, n = 0, len(data)
    while start < n:
        end = data.find(b"\n", start)
        end = n if end < 0 else end + 1
        line = data[start:end]
        words = line.split()
        keyword = words[0] if words else b""
        if keyword in _SPEF_SECTIONS:
            return bytes(out) + data[start:]
        if keyword.decode("latin-1") not in SPEF_DROPPED:
            out += line
        start = end
    raise CanonError(SPEF, "the file ends inside its header, before any section")


def _sdf_canon(data: bytes) -> bytes:
    """The file without the entries directly under ``DELAYFILE`` whose keyword
    is one of :data:`SDF_DROPPED`, up to the first ``CELL``; a line that an
    entry leaves blank goes with it. A backslash escapes the next character,
    inside a quoted string and in a name alike, as SDF reads it."""
    dropped = {word.encode("ascii") for word in SDF_DROPPED}
    spans: List[Tuple[int, int]] = []
    depth, i, n = 0, 0, len(data)
    header = True
    start = None
    while i < n:
        byte = data[i:i + 1]
        if byte == b'"':
            i += 1
            while i < n and data[i:i + 1] != b'"':
                i += 2 if data[i:i + 1] == b"\\" else 1
            if i >= n:
                raise CanonError(SDF, "the file ends inside a quoted string")
        elif byte == b"\\":
            i += 1
        elif byte == b"(":
            depth += 1
            if depth == 2 and header:
                keyword = re.match(rb"\(\s*([A-Za-z_]+)", data[i:i + 64])
                word = keyword.group(1) if keyword else b""
                if word == b"CELL":
                    header = False
                elif word in dropped:
                    start = i
        elif byte == b")":
            if depth == 0:
                raise CanonError(SDF, f"a closing parenthesis at byte {i} has no opening one")
            if depth == 2 and start is not None:
                spans.append((start, i + 1))
                start = None
            depth -= 1
        i += 1
    if depth:
        raise CanonError(SDF, "the file ends inside an open parenthesis")
    out = bytearray()
    kept = 0
    for begin, end in spans:
        line_start = data.rfind(b"\n", 0, begin) + 1
        line_end = data.find(b"\n", end)
        line_end = n if line_end < 0 else line_end + 1
        if not data[line_start:begin].strip() and not data[end:line_end].strip():
            begin, end = line_start, line_end
        out += data[kept:begin]
        kept = end
    out += data[kept:]
    return bytes(out)


_CANONS = {GDS: _gds_canon, SPEF: _spef_canon, SDF: _sdf_canon}


def canon(kind: str, data: bytes) -> bytes:
    """The bytes of a stamped file of ``kind`` without what its tool wrote
    about when and by which program it wrote it. Refused with
    :class:`CanonError` when the file is cut short or malformed past the bytes
    :func:`file_kind` read; a SPEF or SDF with none of the dropped lines is
    neither."""
    if kind not in _CANONS:
        raise ValueError(f"{kind!r} is not one of {GDS}, {SPEF}, {SDF}")
    return _CANONS[kind](data)


def canon_sha256(kind: str, data: bytes) -> str:
    """The hex digest of :func:`canon`."""
    return hashlib.sha256(canon(kind, data)).hexdigest()


class DigestMapRefused(ChipletFormatError):
    """The digest map of a projected file cannot be computed. ``file`` is the
    candidate, ``named_by`` the file that names it (the projected row's path,
    or a nested JSON file's path), ``at`` the JSON pointer of the object that
    names it. A writer words each subclass for the person who built the die."""

    def __init__(self, message: str, *, file: str = "", named_by: str = "", at: str = "") -> None:
        super().__init__(message)
        self.file = file
        self.named_by = named_by
        self.at = at


class CandidateUnreadable(DigestMapRefused):
    """A candidate cannot be read; ``reason`` is the system's word."""

    def __init__(self, *, file: str, named_by: str, at: str, reason: str) -> None:
        super().__init__(f"{file}, which {named_by} names at {at}, cannot be read: {reason}",
                         file=file, named_by=named_by, at=at)
        self.reason = reason


class CandidateChanged(DigestMapRefused):
    """A candidate's bytes do not give the digest recorded for it: the file
    changed, or the digest was not written as the file's digest is."""

    def __init__(self, *, file: str, named_by: str, at: str) -> None:
        super().__init__(f"{file}, which {named_by} names at {at}, does not have the digest "
                         f"recorded for it there", file=file, named_by=named_by, at=at)


class CandidateNamedTwice(DigestMapRefused):
    """One candidate path named with two different digests: first by
    ``other_named_by`` at ``other_at``, then by ``named_by`` at ``at``. The
    two namers may be one file or two."""

    def __init__(self, *, file: str, named_by: str, at: str, other_named_by: str,
                 other_at: str) -> None:
        super().__init__(f"{file} is named with two different digests, by {other_named_by} at "
                         f"{other_at} and by {named_by} at {at}",
                         file=file, named_by=named_by, at=at)
        self.other_named_by = other_named_by
        self.other_at = other_at


class CandidateMalformed(DigestMapRefused):
    """A candidate starts as a stamped kind and its canon fails."""

    def __init__(self, *, file: str, named_by: str, at: str, kind: str, reason: str) -> None:
        super().__init__(f"{file}, which {named_by} names at {at}, starts as a {kind} file but is "
                         f"cut short or malformed: {reason}", file=file, named_by=named_by, at=at)
        self.kind = kind
        self.reason = reason


class DigestCycle(DigestMapRefused):
    """JSON candidates that record each other's digests; ``files`` in the
    order the cycle runs."""

    def __init__(self, files: List[str]) -> None:
        super().__init__(f"{', '.join(files)} record each other's digests in a circle")
        self.files = list(files)


def _pointer(where: Tuple[Any, ...]) -> str:
    """``where`` as a JSON pointer (RFC 6901)."""
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in where)


def candidates(document: Any) -> List[Tuple[str, str, str]]:
    """The candidates a document names, in document order: ``(pointer, path,
    sha256)`` for every object, at any depth, that holds a string ``sha256``
    with an absolute path under one of :data:`CANDIDATE_PATHS`. The path is
    normalized."""
    found: List[Tuple[str, str, str]] = []

    def walk(node: Any, where: Tuple[Any, ...]) -> None:
        if isinstance(node, dict):
            digest = node.get("sha256")
            if isinstance(digest, str):
                path = _candidate_path(node)
                if path is not None:
                    found.append((_pointer(where), path, digest))
            for key, value in node.items():
                walk(value, where + (key,))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, where + (index,))

    walk(document, ())
    return found


def _candidate_path(node: Mapping[str, Any]) -> Optional[str]:
    for key in CANDIDATE_PATHS:
        value = node.get(key)
        if isinstance(value, str) and os.path.isabs(value):
            return os.path.normpath(value)
    return None


def _named_digests(node: Any, where: Tuple[Any, ...] = ()) -> Iterable[Tuple[str, str]]:
    """Every string value of a document that reads as a raw digest, with its
    pointer, in document order."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _named_digests(value, where + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _named_digests(value, where + (index,))
    elif isinstance(node, str) and _HEX.fullmatch(node):
        yield _pointer(where), node


def _replace_digests(node: Any, mapping: Mapping[str, str]) -> Any:
    """``node`` with every string value equal to a key of ``mapping`` replaced
    by its value; keys stay."""
    if isinstance(node, dict):
        return {key: _replace_digests(value, mapping) for key, value in node.items()}
    if isinstance(node, list):
        return [_replace_digests(item, mapping) for item in node]
    if isinstance(node, str):
        return mapping.get(node, node)
    return node


class _Candidate:
    """One candidate file read: its digest, where it was first named, its
    kind, value, and its JSON."""

    def __init__(self, path: str, sha256: str, kind: Optional[str], named_by: str = "",
                 at: str = "") -> None:
        self.path = path
        self.sha256 = sha256
        self.named_by = named_by
        self.at = at
        self.kind = kind
        self.value: Optional[str] = None
        self.document: Any = None


def _projected_input(writer: Optional[str], path: str, document: Any) -> dict:
    """The document a projection starts from: a copy without its out fields.
    Refused as :func:`project` refuses."""
    if decision(writer, path) != _PROJECT:
        raise ChipletFormatError(f"{path}: not a file the content form projects for writer {writer!r}")
    if not isinstance(document, Mapping):
        raise ChipletFormatError(f"{path}: the document to project is not an object")
    projected = copy.deepcopy(dict(document))
    for out in _PROJECT_OUT.get(strip_step(path), ()):
        _remove(projected, out, path)
    return projected


def _read_candidates(document: Any, path: str, read: Callable[[str], bytes]) -> Dict[str, _Candidate]:
    """Every candidate reached from ``document`` (named by ``path``), read
    once each: through every candidate that parses as a JSON object, whatever
    it holds. Each stamped file takes its canon value here."""
    files: Dict[str, _Candidate] = {}
    pending = collections.deque([(path, document)])
    while pending:
        named_by, holder = pending.popleft()
        for at, file, digest in candidates(holder):
            if file in files:
                first = files[file]
                if first.sha256 != digest:
                    raise CandidateNamedTwice(file=file, named_by=named_by, at=at,
                                              other_named_by=first.named_by, other_at=first.at)
                continue
            try:
                data = read(file)
            except (OSError, ValueError) as error:
                reason = getattr(error, "strerror", None) or str(error)
                raise CandidateUnreadable(file=file, named_by=named_by, at=at, reason=reason) from None
            if hashlib.sha256(data).hexdigest() != digest:
                raise CandidateChanged(file=file, named_by=named_by, at=at)
            entry = _Candidate(file, digest, file_kind(data), named_by, at)
            files[file] = entry
            if entry.kind is not None:
                try:
                    entry.value = CANON + canon_sha256(entry.kind, data)
                except CanonError as error:
                    raise CandidateMalformed(file=file, named_by=named_by, at=at, kind=entry.kind,
                                             reason=error.reason) from None
                continue
            try:
                parsed = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                entry.document = parsed
                pending.append((file, parsed))
    return files


def _evidence_values(files: Dict[str, _Candidate]) -> None:
    """Give each JSON candidate that records a mapped digest its ``content:``
    value, files before the JSON files that record their digests; a cycle
    among them is refused."""
    by_digest = {entry.sha256: entry for entry in files.values()}
    done: Dict[str, bool] = {}
    stack: List[str] = []

    def value_of(entry: _Candidate) -> None:
        if entry.document is None or done.get(entry.path):
            return
        if entry.path in stack:
            raise DigestCycle(stack[stack.index(entry.path):])
        stack.append(entry.path)
        mapping = {}
        for _, digest in _named_digests(entry.document):
            other = by_digest.get(digest)
            if other is None or other is entry:
                continue
            value_of(other)
            if other.value is not None:
                mapping[digest] = other.value
        stack.pop()
        if mapping:
            entry.value = CONTENT + canonical_sha256(_replace_digests(entry.document, mapping))
        done[entry.path] = True

    for entry in files.values():
        value_of(entry)


def _digest_map(projected: dict, path: str,
                read: Callable[[str], bytes]) -> Tuple[Dict[str, str], Dict[str, _Candidate]]:
    """The map of a projected document, and every candidate read for it."""
    files = _read_candidates(projected, path, read)
    _evidence_values(files)
    values = {entry.sha256: entry.value for entry in files.values() if entry.value is not None}
    mapping = {digest: values[digest] for _, digest in _named_digests(projected) if digest in values}
    return mapping, files


def digest_map(writer: Optional[str], path: str, document: Any, roots: Mapping[str, Any],
               read: Callable[[str], bytes]) -> Dict[str, str]:
    """The digest map of the projected file at ``path`` (its JSON
    ``document``), computed with the files in hand: for each raw digest the
    document names, after its out fields leave, whose file is a stamped kind
    or JSON evidence, its ``canon:`` or ``content:`` value.

    The candidates are those of :func:`candidates`, and, recursively, those
    of every candidate that parses as a JSON object, read whole. ``roots`` is
    checked as :func:`project` checks it; no path inside a candidate is
    rewritten. ``read`` reads a file by its
    absolute path and raises OSError when it cannot. A stamped file
    (:func:`file_kind`) takes ``canon:`` and its :func:`canon_sha256`; a JSON
    object that records a digest of the map, after its own candidates are
    mapped, takes ``content:`` and the :func:`canonical_sha256` of itself with
    those digests replaced; every other file stays raw and has no entry.

    Refused, with a :class:`DigestMapRefused`: a candidate that cannot be
    read (:class:`CandidateUnreadable`), that does not give the digest
    recorded for it (:class:`CandidateChanged`), that is named with two
    different digests (:class:`CandidateNamedTwice`), that starts as a stamped kind and is cut
    short or malformed (:class:`CandidateMalformed`), JSON files that record
    each other's digests (:class:`DigestCycle`); and as :func:`project`
    refuses."""
    _root_prefixes(roots)
    return _digest_map(_projected_input(writer, path, document), path, read)[0]


def _check_map_form(path: str, document: Any, recorded: Any) -> Dict[str, str]:
    """A row's recorded map, checked against the projected document alone:
    each entry a raw digest with a ``canon:`` or ``content:`` value, each
    digest one the document names."""
    if recorded is None:
        return {}
    if not isinstance(recorded, Mapping):
        raise ChipletFormatError(f"{path}: its {DIGEST_MAP} is not an object")
    named = {digest for _, digest in _named_digests(document)}
    for key, value in recorded.items():
        if (not isinstance(key, str) or not _HEX.fullmatch(key) or not isinstance(value, str)
                or not any(value.startswith(prefix) and _HEX.fullmatch(value[len(prefix):])
                           for prefix in (CANON, CONTENT))):
            raise ChipletFormatError(f"{path}: digest map entry {key} is malformed: the key must be "
                                     f"a raw SHA-256 and the value must start with canon: or content:")
        if key not in named:
            raise ChipletFormatError(f"{path}: its digest map holds {key}, which the file does not name")
    return dict(recorded)


def check_digest_map(writer: Optional[str], path: str, document: Any, roots: Mapping[str, Any],
                     recorded: Any, read: Callable[[str], bytes]) -> None:
    """Check a row's recorded digest map against the files it maps, with them
    in hand: refused, naming the digest, when the map lacks an entry the
    files give, or records a value other than theirs; and as
    :func:`digest_map` and :func:`project` refuse."""
    _root_prefixes(roots)
    projected = _projected_input(writer, path, document)
    recorded = _check_map_form(path, projected, recorded)
    expected, files = _digest_map(projected, path, read)
    by_digest = {entry.sha256: entry for entry in files.values()}
    for at, digest in _named_digests(projected):
        if digest in expected and digest not in recorded:
            raise ChipletFormatError(
                f"{path}: its digest map has no entry for {digest}, which the file names at {at}; "
                f"a {DIE_CONTENT_FORM} row records the content value of every digest it replaces.")
    for digest, value in recorded.items():
        actual = expected.get(digest)
        if actual != value:
            entry = by_digest.get(digest)
            file = entry.path if entry is not None else digest
            if entry is not None and entry.kind is not None:
                said = f"its {entry.kind} canon is {actual}"
            else:
                said = f"its content digest is {actual or 'its raw digest'}"
            raise ChipletFormatError(f"{file}: {said}, but {path}'s digest map records {value} for "
                                     f"raw digest {digest}; the map was not written from this file")


def build_record_content(record: Any) -> dict:
    """The content form of a build record: the record without
    :data:`BUILD_RECORD_OUT`, its bound rows decided by :data:`BOUND_TABLE`
    and sorted by their stripped path, every path that stays without its step
    number, and every digest equal to the record's own layout digest replaced
    by ``layout``. Refused: a record that is not an object, an out path under
    a parent that is not an object, a ``bound`` that is not a list of objects
    with a string ``path``."""
    if not isinstance(record, Mapping):
        raise ChipletFormatError("build record: not an object")
    writer = writer_of(record)
    content = copy.deepcopy(dict(record))
    for out in BUILD_RECORD_OUT:
        _remove(content, out, "build record")
    layout = content.get("layout")
    anchor = layout.get("sha256") if isinstance(layout, Mapping) else None
    if not isinstance(anchor, str):
        anchor = None
    if "bound" in content:
        content["bound"] = _bound_rows(writer, content["bound"])
    _strip_steps(content)
    if anchor is not None:
        _canon(content, anchor)
    return content


def _bound_rows(writer: Optional[str], bound: Any) -> List[dict]:
    if not isinstance(bound, list):
        raise ChipletFormatError("build record: bound is not a list")
    rows = []
    for index, row in enumerate(bound):
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise ChipletFormatError(f"build record: bound[{index}] is not an object with a path")
        verdict = decision(writer, row["path"])
        if verdict == _LEAVE:
            continue
        kept = dict(row)
        kept["path"] = strip_step(row["path"])
        if verdict == _PROJECT and isinstance(kept.get("content_sha256"), str):
            kept.pop("sha256", None)
            kept.pop(DIGEST_MAP, None)
        rows.append(kept)
    return sorted(rows, key=lambda row: row["path"])


def _strip_list(node: Any) -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            if isinstance(item, str):
                node[index] = strip_step(item)


def _strip_steps(content: dict) -> None:
    layout = content.get("layout")
    if isinstance(layout, Mapping):
        _strip_list(layout.get("streamed_in"))
    reported = content.get("producer_reported")
    if isinstance(reported, Mapping):
        for metric in reported.values():
            if isinstance(metric, Mapping):
                _strip_list(metric.get("measured_in"))
    checks = content.get("checks")
    lvs = checks.get("lvs") if isinstance(checks, Mapping) else None
    references = lvs.get("reference") if isinstance(lvs, Mapping) else None
    if isinstance(references, list):
        for reference in references:
            if (isinstance(reference, dict) and reference.get("root") == "run"
                    and isinstance(reference.get("path"), str)):
                reference["path"] = strip_step(reference["path"])


def _canon_key(node: Any, key: str, anchor: str) -> None:
    if isinstance(node, dict) and node.get(key) == anchor:
        node[key] = _LAYOUT


def _canon(content: dict, anchor: str) -> None:
    _canon_key(content.get("layout"), "sha256", anchor)
    checks = content.get("checks")
    if isinstance(checks, dict):
        _canon_key(checks, "layout_sha256", anchor)
        for part in checks.values():
            _canon_key(part, "input_sha256", anchor)
        _canon_key(checks.get("lvs"), "fabricated_sha256", anchor)
    for row in content.get("bound") or ():
        _canon_key(row, "sha256", anchor)


def _digest(where: str, content: Any) -> str:
    try:
        return canonical_sha256(content)
    except (TypeError, ValueError) as error:
        raise ChipletFormatError(f"{where}: not JSON content, so it has no digest: {error}") from None


def build_record_content_sha256(record: Any) -> str:
    """The digest of :func:`build_record_content`; a value JSON cannot
    carry (a NaN, a set, a key that is not a string) is refused."""
    return _digest("build record", build_record_content(record))


def contract_content(contract: Any, build_record: Any = None) -> dict:
    """The content form of a die contract: the contract without
    :data:`CONTRACT_OUT`, its ``build_record.sha256`` replaced by the build
    record's content digest, and its ``die.layout_sha256`` replaced by
    ``layout`` where it equals the record's layout digest (the contract's own
    where the record has none, or there is no record). A contract that names
    a build record needs it; one that names none (null or absent) refuses
    one."""
    if not isinstance(contract, Mapping):
        raise ChipletFormatError("die contract: not an object")
    content = copy.deepcopy(dict(contract))
    for out in CONTRACT_OUT:
        _remove(content, out, "die contract")
    named = content.get("build_record")
    if isinstance(named, Mapping):
        if build_record is None:
            raise ChipletFormatError("die contract: it names a build record and none was given")
        named = dict(named)
        named[CONTRACT_REPLACED[1]] = build_record_content_sha256(build_record)
        content[CONTRACT_REPLACED[0]] = named
    elif named is None:
        if build_record is not None:
            raise ChipletFormatError("die contract: it names no build record and one was given")
    else:
        raise ChipletFormatError("die contract: build_record is neither an object nor null")
    anchor = None
    if isinstance(build_record, Mapping) and isinstance(build_record.get("layout"), Mapping):
        anchor = build_record["layout"].get("sha256")
    die = content.get("die")
    if not isinstance(anchor, str) and isinstance(die, Mapping):
        anchor = die.get("layout_sha256")
    if isinstance(anchor, str):
        _canon_key(die, "layout_sha256", anchor)
    return content


def contract_content_sha256(contract: Any, build_record: Any = None) -> str:
    """The digest of :func:`contract_content`, refused as
    :func:`build_record_content_sha256` is."""
    return _digest("contract", contract_content(contract, build_record))
