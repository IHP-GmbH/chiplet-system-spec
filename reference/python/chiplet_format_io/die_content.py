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
alone. This module is that form, ``die-content/v1``, in one place for the
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

The two digests, :func:`build_record_content_sha256` and
:func:`contract_content_sha256`, hash the canonical JSON every signature in
the flow hashes (:func:`canonical_json`). Changing any of the lists above is
a new form name, never an edit in place: the shared-names golden pins them,
and a signature records the form it was keyed under.

Release 1.26.0 added this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from fnmatch import fnmatchcase
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

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
    "build_record_content",
    "build_record_content_sha256",
    "contract_content",
    "contract_content_sha256",
]

#: The name of this form. A key records it beside the digests it keyed.
DIE_CONTENT_FORM = "die-content/v1"

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


def project(writer: Optional[str], path: str, document: Any, roots: Mapping[str, Any]) -> dict:
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
    root, and a rewritten key that collides with another key."""
    if decision(writer, path) != _PROJECT:
        raise ChipletFormatError(f"{path}: not a file the content form projects for writer {writer!r}")
    if not isinstance(document, Mapping):
        raise ChipletFormatError(f"{path}: the document to project is not an object")
    prefixes = _root_prefixes(roots)
    projected = copy.deepcopy(dict(document))
    for out in _PROJECT_OUT.get(strip_step(path), ()):
        _remove(projected, out, path)
    return _rewrite(projected, prefixes, path)


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
