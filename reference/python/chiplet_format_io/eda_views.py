# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The reader of an EDA views record: the files one built die's views consist
of, copied beside the record and bound by digest.

A die contract carries four files: the layout, the pin list, the build record
and the contract. That is enough to place a die and bump it, and not enough to
time it or load it into another tool, which want the DEF, the SDC, the
technology and cell LEFs, the Liberty files per corner, the parasitics and the
gate-level view. The die flow copies those into one directory,
``eda-views.json`` plus ``<kind>/<basename>``, and binds every copy by digest
to what its build stated.

Each row states what it claims and no more. ``recorded`` means copied and bound
by digest, never accurate; ``unqualified`` adds a ``limit`` naming the known
weakness (a macro timing model, extracted parasitics). The kinds with no row
are listed with their reason under ``not_recorded``.

:func:`read` answers the files of each kind at one corner, :func:`gate_view`
the gate-level sources in compile order, and :func:`check_bound` whether a
record was written for a given die contract. Each checks the schema, keeps
every file inside the record with symlinks resolved, re-hashes every row it
knows and refuses a question the record cannot answer once. A refusal is
:class:`EdaViewsRefused`, a :class:`~chiplet_format_io.ChipletFormatError` and
so a ``ValueError``. It is not the refusal class of the flow these readers
moved from, so a caller that switches its imports here maps it at every site
that caught that one.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Tuple

from . import ChipletFormatError
from .die_contract import DIE_CONTRACT_SCHEMA as CONTRACT_SCHEMA
from .die_contract import SUPPORTED_DIE_CONTRACT_VERSION as CONTRACT_VERSION
from .ready_die_names import CONTRACT

__all__ = [
    "SCHEMA",
    "RECORD",
    "KINDS",
    "READ_KINDS",
    "REQUIRED",
    "SINGLE",
    "RECORDED",
    "UNQUALIFIED",
    "GATE_VIEW_NOT_RECORDED",
    "PAD_MODEL",
    "EdaViewsRefused",
    "check_bound",
    "read",
    "gate_view",
]

SCHEMA = "chiplet-flow-eda-views/v1"
RECORD = "eda-views.json"

#: The kinds a record holds, in the order the rows are written.
KINDS = ("def", "tech_lef", "lef", "liberty", "sdc", "pins", "spef", "sim_model", "netlist",
         "cdl", "spice", "transistor_view")
#: The kinds :func:`read` re-hashes and returns. It skips the transistor-level
#: kinds (``cdl``, ``spice``, ``transistor_view``), whose reader is
#: :func:`chiplet_format_io.transistor_view.transistor_view`.
READ_KINDS = ("def", "tech_lef", "lef", "liberty", "sdc", "pins", "spef", "sim_model", "netlist")
#: What a consumer cannot do without, and of those, what is one file per corner.
REQUIRED = ("def", "tech_lef", "lef", "liberty", "sdc", "pins")
SINGLE = ("def", "tech_lef", "sdc", "pins")

RECORDED = "recorded"
UNQUALIFIED = "unqualified"
#: The fact the writer adds when the gate-level view has no rows.
GATE_VIEW_NOT_RECORDED = "gate_view_not_recorded"

#: The model generated for a pad master no Verilog source defines: an ideal
#: functional view with no electrical model, a module whose one port is the pad
#: pin.
PAD_MODEL = "module {macro} (\n    inout {pin}\n);\nendmodule\n"


class EdaViewsRefused(ChipletFormatError):
    """A record does not read, or is not the one a die contract binds."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path, what: str) -> Dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EdaViewsRefused(f"{what} {path} cannot be read: {exc}") from exc
    if not isinstance(body, dict):
        raise EdaViewsRefused(f"{what} {path} is not a JSON object")
    return body


def _text(body: Mapping[str, Any], key: str, what: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise EdaViewsRefused(f"{what} states no {key}")
    return value


def _within(path: Path, root: Path) -> bool:
    """``path.is_relative_to(root)``, which Python 3.8 lacks, spelled the way
    Python 3.9 implements it."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _inside(root: Path, relative: Any, what: str) -> Path:
    """``relative`` as a file under ``root``: a relative POSIX path with no
    ``..`` that, symlinks resolved, is still a file inside ``root``."""
    if not isinstance(relative, str) or not relative:
        raise EdaViewsRefused(f"{what} names no file")
    spelled = PurePosixPath(relative)
    if spelled.is_absolute() or ".." in spelled.parts or "\\" in relative:
        raise EdaViewsRefused(f"{what} {relative!r} is not a relative path inside {root}")
    path = (root / relative).resolve()
    if not _within(path, root.resolve()) or not path.is_file():
        raise EdaViewsRefused(f"{what} {relative!r} is not a file inside {root}")
    return path


def _contract(contract_dir: Path) -> Dict[str, Any]:
    """What the die contract binds: the layout digest and top cell, and its
    build record and pin list, each re-hashed against the digest it states."""
    contract = _json_object(contract_dir / CONTRACT, "die contract")
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise EdaViewsRefused(f"{CONTRACT} is {contract.get('schema')!r}, not {CONTRACT_SCHEMA}")
    version, major = contract.get("version"), CONTRACT_VERSION.split(".")[0]
    if not isinstance(version, str) or version.split(".")[0] != major:
        raise EdaViewsRefused(f"{CONTRACT} is version {version!r}; this writer reads major version {major}")
    die, record, inventory = (contract.get(key) for key in ("die", "build_record", "inventory"))
    if not all(isinstance(node, Mapping) for node in (die, record, inventory)):
        raise EdaViewsRefused(f"{CONTRACT} lacks its die, build_record or inventory block")
    if inventory.get("kind") != "pins_json":
        raise EdaViewsRefused(f"{CONTRACT} inventory is {inventory.get('kind')!r}, not a pins.json")
    bound = {"layout_sha256": _text(die, "layout_sha256", f"{CONTRACT} die"),
             "top_cell": _text(die, "top_cell", f"{CONTRACT} die")}
    for key, node in (("build_record", record), ("pins", inventory)):
        path = _inside(contract_dir, node.get("path"), f"{CONTRACT} {key}")
        actual = _sha(path)
        if actual != node.get("sha256"):
            raise EdaViewsRefused(f"{path.name} is {actual} and the contract binds it at {node.get('sha256')}")
        bound[key], bound[f"{key}_sha256"] = path, actual
    return bound


def check_bound(record: Path, contract_dir: Path) -> None:
    """Refuse unless the record at ``record`` was written for the die contract
    in ``contract_dir``: the contract's layout and build record are the ones
    the record is bound to, its top cell is the record's design, and its pin
    list is the bytes the record's pins row binds. A consumer names the
    contract it selected for an instance; a top-cell name match is not enough.
    """
    document = _json_object(Path(record), "eda-views record")
    if document.get("schema") != SCHEMA:
        raise EdaViewsRefused(f"{record} is not a {SCHEMA} record")
    contract = _contract(Path(contract_dir))
    bound_to = document.get("bound_to")
    if (not isinstance(bound_to, Mapping)
            or bound_to.get("layout_sha256") != contract["layout_sha256"]
            or bound_to.get("build_record_sha256") != contract["build_record_sha256"]):
        raise EdaViewsRefused(f"{record} is not bound to the layout and build record of {contract_dir}")
    if document.get("design") != contract["top_cell"]:
        raise EdaViewsRefused(
            f"{record} describes {document.get('design')}, and {contract_dir} is {contract['top_cell']}")
    pins = [row.get("sha256") for row in document.get("views", ())
            if isinstance(row, Mapping) and row.get("kind") == "pins"]
    if pins != [contract["pins_sha256"]]:
        raise EdaViewsRefused(f"the pin list of {record} is not the one {contract_dir} binds")


def _load(record: Path) -> Dict[str, Any]:
    try:
        document = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EdaViewsRefused(f"eda-views record {record} cannot be read: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise EdaViewsRefused(f"{record} is not a {SCHEMA} record")
    return document


def read(record: Path, corner: str) -> Dict[str, List[Path]]:
    """The files of each kind that apply at ``corner``, from the record at
    ``record`` (an ``eda-views.json``), each re-hashed against its row.

    Rows of a kind this reader does not know are skipped, and so are the
    transistor-level kinds (``cdl``, ``spice``, ``transistor_view``), which it
    does not re-hash and does not return. Every other row must
    name a file inside the record directory, symlinks resolved, whose bytes are
    its digest. A file two rows name is returned once, where it first appears.
    A kind a consumer cannot do without that has no row at the corner refuses,
    and so does a corner with two DEFs, SDCs, pin lists or tech LEFs: an
    ambiguous answer is not an answer.
    """
    record = Path(record)
    document = _load(record)
    corners = document.get("corners")
    if not isinstance(corners, list) or not all(isinstance(c, str) for c in corners):
        raise EdaViewsRefused(f"{record} states no corners")
    if corner not in corners:
        raise EdaViewsRefused(f"{record} has no corner {corner!r}; it states {corners}")
    rows = document.get("views")
    if not isinstance(rows, list):
        raise EdaViewsRefused(f"{record} lists no views")
    found: Dict[str, List[Path]] = {kind: [] for kind in READ_KINDS}
    for row in rows:
        kind = row.get("kind") if isinstance(row, Mapping) else None
        if kind not in READ_KINDS:
            continue
        applies = row.get("corners")
        if applies is not None and (not isinstance(applies, list)
                                    or not all(isinstance(c, str) for c in applies)):
            raise EdaViewsRefused(f"{record}: a {kind} row states corners that are not a list of names")
        path = _inside(record.parent, row.get("file"), f"{record}: the {kind} row")
        if _sha(path) != row.get("sha256"):
            raise EdaViewsRefused(f"{record}: {row['file']} is not the bytes its row binds")
        if (applies is None or corner in applies) and path not in found[kind]:
            found[kind].append(path)
    for kind in REQUIRED:
        if not found[kind]:
            raise EdaViewsRefused(f"{record} has no {kind} row at corner {corner}")
    for kind in SINGLE:
        if len(found[kind]) != 1:
            raise EdaViewsRefused(
                f"{record} has {len(found[kind])} {kind} rows at corner {corner}; one is expected")
    return found


def gate_view(record: Path) -> Tuple[str, List[Path]]:
    """The design a record describes and its gate-level sources in compile
    order: the sim_model files, then the netlist files, each in row order and
    re-hashed by :func:`read` at the default corner. A record with no netlist
    row refuses with the reason it states. A consumer that selected the record
    for a die contract calls :func:`check_bound` first."""
    record = Path(record)
    document = _load(record)
    found = read(record, document.get("default_corner"))
    if not found["netlist"]:
        reasons = [row.get("reason") for row in document.get("not_recorded") or ()
                   if isinstance(row, Mapping) and row.get("kind") == "netlist"]
        raise EdaViewsRefused(f"{record} records no netlist" + (f": {reasons[0]}" if reasons else ""))
    if not isinstance(document.get("design"), str) or not document["design"]:
        raise EdaViewsRefused(f"{record} names no design")
    return document["design"], found["sim_model"] + found["netlist"]
