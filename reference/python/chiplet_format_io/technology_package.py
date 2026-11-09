# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Validator for technology packages (``schemas/technology_package.schema.json``).

A technology package is how a technology enters the flow as data: one manifest,
``libs.tech/adk/technology.json``, names the package's files, and the directory
tree that holds it is the package root. The validator answers one question per
capability set, A (geometry and assembly), B (automated design) and C
(analysis): does this package carry every fact the set needs? A technology is
offered only for the sets its package passes.

Two kinds of outcome, kept apart on purpose:

* **A refusal** stops the read: no manifest, a manifest that is not JSON, a
  schema major this reader does not accept, a manifest the schema rejects, or a
  role this version does not model. Nothing about the sets can be said then.
* **A finding** fails a set and names the missing or wrong fact, where it is
  and a stable message id. Findings are collected, not raised one at a time, so
  a package author sees every gap in one run.

What is checked here, and what is not:

* Every member path is relative to the package root, has no ``..``, drive,
  ``~`` or variable, does not lead out of the root through a link, and names a
  file (or, for a folder member, a folder) that exists. A path that resolves
  elsewhere on another machine is the silent case this format exists to
  remove. The schema marks which strings are member paths, and the walk over
  them is derived from it, so a new member is covered without a new check.
* Each routing descriptor variant is bound by sha256, and its
  ``schema_version`` falls in the manifest's accepted range. The descriptor's
  own schema is interposer-pnr's and is enforced by its loader when it reads the
  file; this validator does not keep a second copy, reads only the keys it
  judges, and refuses to vouch for a shape it does not recognise.
* Names the manifest gives for layers are looked up in the package's layer
  sheet, so a pad layer that the sheet does not define fails set A here and not
  as an empty layer in some later GDS.
* ``escape_capacity``, when present, is held to its own facts: one entry per
  (method, crossing axis, supply), a method of ``attach``, a failing depth
  above the clean one, the edge load it derives from them, and the one routing
  variant it names, which routes with the multi-layer router (more than one
  route layer) and has a route layer on the axis. Its pitch is the method's,
  which only the interconnect method registry knows: given the registry
  (``methods=``), a different pitch is a finding; without it, the report notes
  the pitch as not checked, and the set's verdict carries the count.
* ``attach`` names methods of that registry, a separate package, so the ids
  are resolved only when the registry is given; ``seal_ring`` and ``pcells``
  name cells of a library that only a layout tool can open. The schema checks
  their shape and nothing more is checked.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import re
import sys
import traceback
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional, Sequence, Tuple, Union

from . import (ChipletFormatError, ContractVersionError, ContractVersionWarning,
               _parse_contract_version, check_contract_version)
from .die_contract import _read_packaged_schema, digest_file

__all__ = [
    "MANIFEST_PATH",
    "SUPPORTED_TECHNOLOGY_PACKAGE_VERSION",
    "MODELLED_ROLES",
    "SET_MEMBERS",
    "OPTIONAL_MEMBERS",
    "MESSAGE_IDS",
    "SUPPORTED_METHODS_VERSION",
    "TechnologyPackageError",
    "Finding",
    "PackageReport",
    "manifest_path",
    "load_manifest",
    "load_method_pitches",
    "check_package",
    "validate_package",
    "main",
]

#: Where the manifest sits inside a package root.
MANIFEST_PATH = PurePosixPath("libs.tech/adk/technology.json")

TECHNOLOGY_PACKAGE_SCHEMA = "technology-package"
SUPPORTED_TECHNOLOGY_PACKAGE_VERSION = "1.0"

#: The interconnect method registry minor this reader reads a method's pitch from.
SUPPORTED_METHODS_VERSION = "1.1"

#: Roles this version validates. The die half of the format is a later minor.
MODELLED_ROLES = frozenset({"carrier"})

#: The members each capability set needs, for the carrier role. The schema
#: carries the same sets on each member (``x-capability-set``) and conformance
#: holds the two equal.
SET_MEMBERS: Dict[str, Tuple[str, ...]] = {
    "A": ("dbu_um", "manufacturing_grid_um", "layer_properties", "routing",
          "stack", "attach", "external", "occupied_pairs"),
    "B": ("drc", "fill", "seal_ring", "kicad_template"),
    "C": ("routing", "em_limits"),
}

#: Members a set reads when present and does not need, with that set. Without
#: ``pcells`` the export draws vias and pads as rectangles from the descriptor;
#: without ``escape_capacity`` the bump planner says the escape is not measured.
OPTIONAL_MEMBERS: Dict[str, str] = {"pcells": "B", "escape_capacity": "A"}

#: Every message id this module emits, its kind and what it means. A GUI or a
#: log reader keys on these, so an id is added here before it is used, and
#: never renamed. A refusal stops the read, a verdict is ``validate_package``
#: refusing a set that failed, a finding fails one set, and a note is said
#: without failing anything.
MESSAGE_IDS: Dict[str, Tuple[str, str]] = {
    "PACKAGE_MANIFEST_MISSING": ("refusal", "no manifest at libs.tech/adk/technology.json inside the root"),
    "PACKAGE_MANIFEST_UNREADABLE": ("refusal", "the manifest is not a JSON object"),
    "PACKAGE_SCHEMA_VERSION": ("refusal", "the manifest's schema_version is malformed or of another major"),
    "PACKAGE_SCHEMA_INVALID": ("refusal", "the manifest does not match the schema"),
    "PACKAGE_SCHEMA_ABSENT": ("refusal", "this installation cannot validate (schema or jsonschema missing)"),
    "PACKAGE_ROLE_NOT_MODELLED": ("refusal", "the manifest names a role this version does not model"),
    "PACKAGE_METHODS_UNREADABLE": ("refusal", "the interconnect method registry given cannot be read"),
    "PACKAGE_VALIDATOR_FAILED": ("refusal", "the validator itself failed; a defect to report, not a verdict"),
    "PACKAGE_SET_INCOMPLETE": ("verdict", "validate_package: a required set has findings"),
    "PACKAGE_MEMBER_ABSENT": ("finding", "a member the set needs is not declared"),
    "PACKAGE_MEMBER_PATH": ("finding", "a member path would not travel with the package"),
    "PACKAGE_MEMBER_MISSING": ("finding", "a member path names nothing, or the wrong kind of entry"),
    "PACKAGE_MEMBER_UNREADABLE": ("finding", "a member file cannot be read or parsed"),
    "PACKAGE_DIGEST_MISMATCH": ("finding", "a descriptor's bytes differ from the recorded sha256"),
    "PACKAGE_DEFAULT_VARIANT": ("finding", "default_variant is not one of the variants"),
    "PACKAGE_DESCRIPTOR_VERSION": ("finding", "a descriptor's schema_version is absent or outside the accepted range"),
    "PACKAGE_DESCRIPTOR_READER": ("finding", "a descriptor's requires_reader names another major"),
    "PACKAGE_DESCRIPTOR_DISAGREES": ("finding", "a descriptor's dbu or grid differs from the manifest's"),
    "PACKAGE_DESCRIPTOR_NAME": ("finding", "two routing variants share one identity.name"),
    "PACKAGE_STACK_KEY": ("finding", "the stack file lacks a positive number the consumers read"),
    "PACKAGE_LAYER_UNKNOWN": ("finding", "a layer name is not in the package's layer sheet"),
    "PACKAGE_PAIRS_DECLARED": ("finding", "occupied pairs are not measured"),
    "PACKAGE_RESISTANCE_MISSING": ("finding", "a routing layer or via has no resistance, or cannot be checked"),
    "PACKAGE_ESCAPE_KEY": ("finding", "two escape capacity entries share (method, crossing_axis, supply)"),
    "PACKAGE_ESCAPE_METHOD": ("finding", "an escape capacity entry's method is not an attach method"),
    "PACKAGE_ESCAPE_MEASURE": ("finding", "an entry's depths or derived edge load do not agree"),
    "PACKAGE_ESCAPE_AXIS": ("finding", "an entry's descriptor is not one multi-layer variant with a route layer on the axis"),
    "PACKAGE_ESCAPE_PITCH": ("finding", "an entry's pitch is not its method's pitch in the registry"),
    "PACKAGE_PITCH_NOT_CHECKED": ("note", "no registry was given, so an entry's pitch was not checked"),
}

_SCHEMA_RESOURCE = "technology_package.schema.json"

def _home_sets() -> Dict[str, str]:
    """The set a member's path and file findings go to: its first set. Set C
    reports an unreadable routing variant again, on its own terms."""
    homes = dict(OPTIONAL_MEMBERS)
    for set_name, members in SET_MEMBERS.items():
        for member in members:
            homes.setdefault(member, set_name)
    return homes


_HOME_SET = _home_sets()

#: Keys of the stack file that consumers read for the body and the mount plane.
_STACK_KEYS = (("substrate", "thickness_um"), ("beol", "attachment_surface_z_um"))

#: The two schema definitions whose strings are member paths, and what each names.
_PATH_KINDS = {"#/$defs/member_path": "file", "#/$defs/member_folder": "folder"}

_DRIVE = re.compile(r"^[A-Za-z]:")


class TechnologyPackageError(ChipletFormatError):
    """The package cannot be read, or a required set does not pass.

    ``message_id`` is stable and leads the message, so a log reader and a GUI
    can key on it without parsing prose.
    """

    def __init__(self, message_id: str, text: str):
        super().__init__(f"{message_id}: {text}")
        self.message_id = message_id
        self.text = text


class Finding(NamedTuple):
    """One fact a set is missing or has wrong; ``where`` is a dotted manifest location."""

    message_id: str
    capability_set: str
    where: str
    text: str

    def line(self) -> str:
        kind = "note, " if MESSAGE_IDS[self.message_id][0] == "note" else ""
        return f"{self.message_id}: {kind}set {self.capability_set}, {self.where}: {self.text}"


class PackageReport(NamedTuple):
    """What a package passes: findings per set, empty for a passing set."""

    id: str
    root: str
    findings: Dict[str, List[Finding]]
    notes: List[Finding]

    def passes(self, set_name: str) -> bool:
        return not self.findings[set_name]

    def not_checked(self, set_name: str) -> int:
        """How many facts of the set were noted as not checked; a pass with
        any is not a plain pass."""
        return sum(1 for note in self.notes if note.capability_set == set_name)

    def verdict(self, set_name: str) -> str:
        """``set A: pass``, ``set A: fail``, or a pass with what it did not check."""
        word = "pass" if self.passes(set_name) else "fail"
        unchecked = self.not_checked(set_name)
        return f"set {set_name}: {word}" + (f", {unchecked} not checked" if unchecked else "")

    def failing(self, require: Sequence[str]) -> List[str]:
        """The sets of ``require`` that do not pass, in order."""
        return [name for name in _known_sets(require) if not self.passes(name)]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "root": self.root,
            "sets": {
                name: {"passes": not found, "not_checked": self.not_checked(name),
                       "findings": [f._asdict() for f in found]}
                for name, found in self.findings.items()
            },
            "notes": [n._asdict() for n in self.notes],
            "refused": None,
        }


def _refused_report(root, message_id: str, text: str) -> Dict[str, Any]:
    """The report of a package that could not be judged, in the same envelope."""
    return {"id": None, "root": str(root), "sets": {}, "notes": [],
            "refused": {"message_id": message_id, "text": text}}


def _known_sets(require: Sequence[str]) -> Sequence[str]:
    unknown = sorted(set(require) - set(SET_MEMBERS))
    if unknown:
        raise ValueError(f"unknown capability set {', '.join(unknown)}")
    if not require:
        raise ValueError("no capability set is required")
    return require


def manifest_path(root) -> Path:
    """The manifest of the package rooted at ``root``."""
    return Path(root).joinpath(*MANIFEST_PATH.parts)


@functools.lru_cache(maxsize=None)
def _schema_text() -> str:
    text = _read_packaged_schema(_SCHEMA_RESOURCE)
    if text is None:
        raise TechnologyPackageError(
            "PACKAGE_SCHEMA_ABSENT",
            f"{_SCHEMA_RESOURCE} is not present in this installation of "
            "chiplet_format_io, so a package cannot be validated; refusing "
            "rather than passing on a partial check")
    return text


def _schema() -> Dict[str, Any]:
    return json.loads(_schema_text())


def _jsonschema():
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise TechnologyPackageError(
            "PACKAGE_SCHEMA_ABSENT",
            "jsonschema is required to validate a technology package (install "
            "chiplet-format-io[strict]); refusing rather than passing on a "
            "partial check") from exc
    return jsonschema


def _warn(text: str, on_warn: Optional[Callable[[str], None]]) -> None:
    """Deliver as :func:`check_contract_version` does: ``on_warn`` and the warnings channel."""
    if on_warn is not None:
        on_warn(text)
    warnings.warn(text, ContractVersionWarning, stacklevel=4)


def load_manifest(root, *, on_warn: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Read and schema-check the manifest of the package at ``root``.

    Raises :class:`TechnologyPackageError` for every refusal listed in the
    module docstring. A manifest at a higher minor of the supported major is
    read with its unknown top-level members ignored and reported, as the
    version policy reports them for every governed document. Only the top level
    is opened: the members are closed objects, so a key added inside one needs
    this reader's next minor. A die contract's per-node rules would be
    machinery a manifest of a dozen members does not need.
    """
    path = manifest_path(root)
    if not path.is_file():
        raise TechnologyPackageError(
            "PACKAGE_MANIFEST_MISSING",
            f"{path} does not exist; a technology package is a directory tree "
            f"with its manifest at {MANIFEST_PATH}")
    try:
        path.resolve().relative_to(Path(root).resolve())
    except ValueError:
        raise TechnologyPackageError(
            "PACKAGE_MANIFEST_MISSING",
            f"{path} leads out of the package root through a link; the manifest "
            "is part of the package it describes") from None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TechnologyPackageError(
            "PACKAGE_MANIFEST_UNREADABLE", f"{path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise TechnologyPackageError(
            "PACKAGE_MANIFEST_UNREADABLE", f"{path}: the manifest is not a JSON object")

    try:
        version = check_contract_version(
            manifest.get("schema_version"), SUPPORTED_TECHNOLOGY_PACKAGE_VERSION,
            name=str(path), on_warn=on_warn)
    except ContractVersionError as exc:
        raise TechnologyPackageError("PACKAGE_SCHEMA_VERSION", str(exc)) from exc

    schema = _schema()
    if version != SUPPORTED_TECHNOLOGY_PACKAGE_VERSION:
        known = schema["properties"]
        for key in sorted(set(manifest) - set(known)):
            _warn(f"{path}: member {key!r} is not known to technology package "
                  f"{SUPPORTED_TECHNOLOGY_PACKAGE_VERSION} and is ignored", on_warn)
        manifest = {k: v for k, v in manifest.items() if k in known}
    errors = sorted(_jsonschema().Draft202012Validator(schema).iter_errors(manifest),
                    key=lambda e: list(map(str, e.absolute_path)))
    if errors:
        where = "/".join(str(p) for p in errors[0].absolute_path) or "(top level)"
        raise TechnologyPackageError(
            "PACKAGE_SCHEMA_INVALID",
            f"{path}: {where}: {errors[0].message}"
            + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""))

    unmodelled = sorted(set(manifest["roles"]) - MODELLED_ROLES)
    if unmodelled:
        raise TechnologyPackageError(
            "PACKAGE_ROLE_NOT_MODELLED",
            f"{path}: role {', '.join(unmodelled)} is not modelled by technology "
            f"package {SUPPORTED_TECHNOLOGY_PACKAGE_VERSION}; only "
            f"{', '.join(sorted(MODELLED_ROLES))} is")
    return manifest


def load_method_pitches(path, *, on_warn: Optional[Callable[[str], None]] = None
                        ) -> Dict[str, Optional[float]]:
    """Each method's pitch (``pitch_rules.IXN_pitch``) from an interconnect
    method registry, None where the registry states none. Raises
    :class:`TechnologyPackageError` when the registry cannot be read."""
    path = Path(path)
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(registry, dict) or not isinstance(registry.get("methods"), dict):
            raise ValueError("not a registry: no methods object")
        check_contract_version(registry.get("schema_version"), SUPPORTED_METHODS_VERSION,
                               name=str(path), on_warn=on_warn)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        # json.JSONDecodeError and ContractVersionError are both ValueErrors.
        raise TechnologyPackageError("PACKAGE_METHODS_UNREADABLE", f"{path}: {exc}") from exc
    pitches: Dict[str, Optional[float]] = {}
    for method, spec in registry["methods"].items():
        pitch = _dig(spec, ("pitch_rules", "IXN_pitch"))
        pitches[method] = pitch if _positive(pitch) else None
    return pitches


# --- member paths -------------------------------------------------------------

def _member_paths(value: Any, node: Dict[str, Any], defs: Dict[str, Any],
                  where: str) -> Iterator[Tuple[str, str, str]]:
    """Yield ``(where, raw, kind)`` for every member path in a schema-valid ``value``."""
    ref = node.get("$ref")
    if ref in _PATH_KINDS:
        yield where, value, _PATH_KINDS[ref]
        return
    if ref:
        node = defs[ref.rsplit("/", 1)[1]]
    for branch in node.get("oneOf", ()):
        if isinstance(value, dict) and branch.get("type") == "object":
            yield from _member_paths(value, branch, defs, where)
    if isinstance(value, list) and isinstance(node.get("items"), dict):
        for k, item in enumerate(value):
            yield from _member_paths(item, node["items"], defs, f"{where}[{k}]")
    if isinstance(value, dict):
        props = node.get("properties", {})
        extra = node.get("additionalProperties")
        for key, item in sorted(value.items()):
            sub = props.get(key, extra if isinstance(extra, dict) else None)
            if sub is not None:
                yield from _member_paths(item, sub, defs, f"{where}.{key}")


def _unportable(raw: str) -> Optional[str]:
    """Why a member path would not travel with the package, as written, or None."""
    if "\0" in raw:
        return "carries a NUL byte, which no file system takes"
    if "\\" in raw:
        return "uses a backslash; member paths use forward slashes"
    if "$" in raw or "%" in raw:
        return "carries a variable; member paths are literal"
    if raw.startswith("~"):
        return "starts at a home folder; member paths are relative to the package root"
    if _DRIVE.match(raw):
        return "names a drive; member paths are relative to the package root"
    path = PurePosixPath(raw)
    if path.is_absolute():
        return "is absolute; member paths are relative to the package root"
    if ".." in path.parts:
        return "climbs out of the package root"
    return None


def _dig(data: Any, keys: Sequence[str]) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _route_layers(descriptor: Dict[str, Any]) -> List[str]:
    """The descriptor's ``routability.route_layers`` names; empty when absent."""
    route = _dig(descriptor, ("routability", "route_layers"))
    return [name for name in route if isinstance(name, str)] if isinstance(route, list) else []


def _axis_layers(descriptor: Dict[str, Any], axis: str) -> List[str]:
    """The descriptor's route layers whose preferred direction is ``axis``."""
    layers = descriptor.get("routing_layers")
    if not isinstance(layers, list):
        return []
    direction = {layer.get("abstract_name"): layer.get("preferred_direction")
                 for layer in layers
                 if isinstance(layer, dict) and isinstance(layer.get("abstract_name"), str)}
    return [name for name in _route_layers(descriptor) if direction.get(name) == axis.upper()]


def _finite(value: Any) -> bool:
    """A number that a float holds; a bool is not a number here."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int beyond the float range
        return False


def _positive(value: Any) -> bool:
    return _finite(value) and value > 0


def _same(a: float, b: float) -> bool:
    """Equal within the 1e-9 relative the escape capacity contract states."""
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=0.0)


def _measure_problems(entry: Dict[str, Any]) -> List[str]:
    """What disagrees among an escape capacity entry's own numbers; empty when nothing does."""
    clean, failing = entry["depth_clean"], entry["depth_failing"]
    pitch, load = entry["pitch_um"], entry["edge_load_per_mm"]
    if not all(_finite(v) for v in (clean, failing, pitch, load)):
        return ["a depth, the pitch or the edge load is too large to be a measured value"]
    problems = []
    if failing <= clean:
        problems.append(f"depth_failing {failing} is not above depth_clean {clean}")
    derived = float(clean) * 1000 / pitch
    if not _same(load, derived):
        problems.append(f"edge_load_per_mm is {load!r}, depth_clean x 1000 / pitch_um is "
                        f"{derived!r}; the entry was edited by hand")
    return problems


class _Unreadable(NamedTuple):
    """A routing variant this reader cannot vouch for, and why (set A says how)."""

    reason: str


_Variant = Union[Dict[str, Any], _Unreadable]


class _Checker:
    """Collects the findings of one package.

    The order in :meth:`run` is the data flow: member paths are resolved once,
    the routing descriptors are read once from them, and each check after that
    reads those results and records under its own set.
    """

    def __init__(self, root: Path, manifest: Dict[str, Any],
                 pitches: Optional[Dict[str, Optional[float]]] = None):
        self.root = root
        self.pitches = pitches
        self.notes: List[Finding] = []
        self.real_root = root.resolve()
        self.manifest = manifest
        self.findings: Dict[str, List[Finding]] = {name: [] for name in SET_MEMBERS}
        self.files: Dict[str, Path] = {}

    def add(self, message_id: str, set_name: str, where: str, text: str) -> None:
        self.findings[set_name].append(Finding(message_id, set_name, where, text))

    def note(self, message_id: str, set_name: str, where: str, text: str) -> None:
        self.notes.append(Finding(message_id, set_name, where, text))

    def run(self) -> None:
        self.check_absent()
        self.resolve_member_paths()
        variants = self.read_variants()
        names = _by_identity(variants)
        self.check_layer_sheet()
        self.check_stack()
        self.check_pairs()
        self.check_routing(variants, names)
        self.check_resistances(variants)
        self.check_escape(variants, names)
        self.check_em_limits()

    def check_absent(self) -> None:
        for set_name, members in SET_MEMBERS.items():
            for member in members:
                if member not in self.manifest:
                    self.add("PACKAGE_MEMBER_ABSENT", set_name, member,
                             "the manifest does not declare it")

    # Paths, for every set --------------------------------------------------

    def resolve_member_paths(self) -> None:
        schema = _schema()
        for key, value in sorted(self.manifest.items()):
            node = schema["properties"].get(key, {})
            for where, raw, kind in _member_paths(value, node, schema["$defs"], key):
                path, problem = self.resolve(raw, kind)
                if path is not None:
                    self.files[where] = path
                else:
                    self.add(problem[0], _HOME_SET[key], where, f"{raw!r} {problem[1]}")

    def resolve(self, raw: str, kind: str) -> Tuple[Optional[Path], Tuple[str, str]]:
        """The member's path inside the root, or None and (message id, reason)."""
        reason = _unportable(raw)
        if reason:
            return None, ("PACKAGE_MEMBER_PATH", reason)
        path = self.root.joinpath(*PurePosixPath(raw).parts)
        try:
            path.resolve().relative_to(self.real_root)
        except ValueError:
            return None, ("PACKAGE_MEMBER_PATH", "leads out of the package root through a link")
        except (OSError, RuntimeError) as exc:
            return None, ("PACKAGE_MEMBER_MISSING", f"cannot be resolved: {exc}")
        try:
            present = path.exists()
            right_kind = path.is_dir() if kind == "folder" else path.is_file()
        except OSError as exc:
            return None, ("PACKAGE_MEMBER_MISSING", f"cannot be inspected: {exc}")
        if not present:
            return None, ("PACKAGE_MEMBER_MISSING", "does not exist under the package root")
        if not right_kind:
            return None, ("PACKAGE_MEMBER_MISSING", f"is not a {kind}")
        return path, ("", "")

    def json_file(self, member: str, where: str) -> Optional[Dict[str, Any]]:
        """The JSON object at a resolved path of ``member``, or None with a finding."""
        path = self.files.get(where)
        if path is None:
            return None
        set_name = _HOME_SET[member]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.add("PACKAGE_MEMBER_UNREADABLE", set_name, where, str(exc))
            return None
        if not isinstance(data, dict):
            self.add("PACKAGE_MEMBER_UNREADABLE", set_name, where, "not a JSON object")
            return None
        return data

    # Routing descriptors: read once, judged by sets A and C -------------------

    def read_variants(self) -> Dict[str, _Variant]:
        if "routing" not in self.manifest:
            return {}
        variants: Dict[str, _Variant] = {}
        for variant, spec in sorted(self.manifest["routing"]["variants"].items()):
            where = f"routing.variants.{variant}.path"
            path = self.files.get(where)
            if path is None:
                variants[variant] = _Unreadable("its file cannot be used")
                continue
            try:
                actual = digest_file(path)
            except OSError as exc:
                self.add("PACKAGE_MEMBER_UNREADABLE", "A", where, str(exc))
                variants[variant] = _Unreadable("it cannot be read")
                continue
            if actual != spec["sha256"]:
                self.add("PACKAGE_DIGEST_MISMATCH", "A", where,
                         f"{spec['path']} hashes to {actual}, the manifest "
                         f"records {spec['sha256']}")
                variants[variant] = _Unreadable("its bytes are not the ones the manifest binds")
                continue
            data = self.json_file("routing", where)
            if data is None:
                variants[variant] = _Unreadable("it does not parse")
            elif not self.accepted(variant, data):
                variants[variant] = _Unreadable("its schema_version is not one this package accepts")
            else:
                variants[variant] = data
        return variants

    def accepted(self, variant: str, descriptor: Dict[str, Any]) -> bool:
        """Whether the descriptor's schema_version is in the accepted range,
        with a finding when it is not: a shape this reader does not know is
        one it does not vouch for, in any set."""
        accepted = self.manifest["routing"]["accepted"]
        major, min_minor = accepted["major"], accepted["min_minor"]
        version = _parse_contract_version(descriptor.get("schema_version"))
        if version is not None and version[0] == major and version[1] >= min_minor:
            return True
        self.add("PACKAGE_DESCRIPTOR_VERSION", "A", f"routing.variants.{variant}",
                 f"schema_version {descriptor.get('schema_version')!r} is not "
                 f"{major}.{min_minor} or a later minor of major {major}")
        return False

    def check_routing(self, variants: Dict[str, _Variant], names: Dict[str, List[str]]) -> None:
        routing = self.manifest.get("routing")
        if routing is None:
            return
        if routing["default_variant"] not in routing["variants"]:
            self.add("PACKAGE_DEFAULT_VARIANT", "A", "routing.default_variant",
                     f"{routing['default_variant']!r} is not one of the variants "
                     f"({', '.join(sorted(routing['variants']))})")
        for name, holders in sorted(names.items()):
            if len(holders) > 1:
                self.add("PACKAGE_DESCRIPTOR_NAME", "A", "routing.variants",
                         f"variants {', '.join(holders)} all carry identity.name {name!r}; "
                         "a measurement that names its descriptor could be either")
        major = routing["accepted"]["major"]
        for variant, descriptor in sorted(variants.items()):
            if isinstance(descriptor, _Unreadable):
                continue
            where = f"routing.variants.{variant}"
            if "requires_reader" in descriptor:
                reader = _parse_contract_version(descriptor["requires_reader"])
                if reader is None or reader[0] != major:
                    self.add("PACKAGE_DESCRIPTOR_READER", "A", where,
                             f"requires_reader {descriptor['requires_reader']!r} is not a "
                             f"version of major {major}")
            identity = descriptor.get("identity")
            identity = identity if isinstance(identity, dict) else {}
            for key in ("dbu_um", "manufacturing_grid_um"):
                if key in self.manifest and identity.get(key) != self.manifest[key]:
                    self.add("PACKAGE_DESCRIPTOR_DISAGREES", "A", where,
                             f"identity.{key} is {identity.get(key)!r}, the "
                             f"manifest states {self.manifest[key]!r}")

    def check_resistances(self, variants: Dict[str, _Variant]) -> None:
        for variant, descriptor in sorted(variants.items()):
            where = f"routing.variants.{variant}"
            if isinstance(descriptor, _Unreadable):
                self.add("PACKAGE_RESISTANCE_MISSING", "C", where,
                         f"its resistances cannot be checked: {descriptor.reason} (set A)")
                continue
            for group, slot in (("routing_layers", "resistance_ohm_per_sq"),
                                ("vias", "resistance_ohm_per_cut")):
                entries = descriptor.get(group)
                if not isinstance(entries, list) or not entries:
                    self.add("PACKAGE_RESISTANCE_MISSING", "C", where, f"has no {group}")
                    continue
                for k, entry in enumerate(entries):
                    entry = entry if isinstance(entry, dict) else {}
                    if not _positive(entry.get(slot)):
                        name = entry.get("emitter_name", f"{group}[{k}]")
                        self.add("PACKAGE_RESISTANCE_MISSING", "C", where,
                                 f"{name} has no positive {slot}")

    # Escape capacity (set A, optional) ----------------------------------------

    def check_escape(self, variants: Dict[str, _Variant], names: Dict[str, List[str]]) -> None:
        seen = set()
        for k, entry in enumerate(self.manifest.get("escape_capacity", ())):
            where = f"escape_capacity[{k}]"
            method, axis, supply = entry["method"], entry["crossing_axis"], entry["supply"]
            if (method, axis, supply) in seen:
                self.add("PACKAGE_ESCAPE_KEY", "A", where,
                         f"another entry already has method {method!r}, crossing_axis "
                         f"{axis!r} and supply {supply!r}")
            seen.add((method, axis, supply))
            if method not in self.manifest.get("attach", {}).get("methods", ()):
                self.add("PACKAGE_ESCAPE_METHOD", "A", where,
                         f"method {method!r} is not one of attach.methods")
            for problem in _measure_problems(entry):
                self.add("PACKAGE_ESCAPE_MEASURE", "A", where, problem)
            problem = self.escape_descriptor_problem(entry, variants, names)
            if problem:
                self.add("PACKAGE_ESCAPE_AXIS", "A", where, problem)
            if self.pitches is None:
                self.note("PACKAGE_PITCH_NOT_CHECKED", "A", where,
                          "no interconnect method registry was given")
            elif self.pitches.get(method) is None:
                self.add("PACKAGE_ESCAPE_PITCH", "A", where,
                         f"the registry gives no pitch for method {method!r}")
            elif _finite(entry["pitch_um"]) and not _same(entry["pitch_um"], self.pitches[method]):
                self.add("PACKAGE_ESCAPE_PITCH", "A", where,
                         f"pitch_um {entry['pitch_um']!r} is not method {method!r}'s pitch "
                         f"{self.pitches[method]!r}")

    @staticmethod
    def escape_descriptor_problem(entry: Dict[str, Any], variants: Dict[str, _Variant],
                                  names: Dict[str, List[str]]) -> Optional[str]:
        """Why the entry's descriptor cannot carry it, or None.

        The entry names the descriptor its coupon was routed with; that must be
        exactly one readable variant, routing with the multi-layer router (more
        than one route layer, as interposer-pnr selects it) and with a route
        layer on the entry's axis."""
        name, axis = entry["router"]["setting"]["descriptor"], entry["crossing_axis"]
        holders = names.get(name, [])
        if not holders:
            return (f"router.setting.descriptor {name!r} is not a readable routing "
                    "variant of this package")
        if len(holders) > 1:
            return f"router.setting.descriptor {name!r} is variants {', '.join(holders)}"
        descriptor = variants[holders[0]]
        if len(_route_layers(descriptor)) < 2:
            return (f"{name} routes with the single-layer backend (fewer than two "
                    "routability.route_layers); the measurement is of the multi-layer router")
        if not _axis_layers(descriptor, axis):
            return (f"{name} has no routability.route_layers entry with preferred "
                    f"direction {axis}")
        return None

    # Layer sheet, stack and pairs (set A) -------------------------------------

    def check_layer_sheet(self) -> None:
        lyp = self.files.get("layer_properties.lyp")
        if lyp is None or "external" not in self.manifest:
            return
        try:
            tree = ET.parse(lyp)
        except (OSError, ET.ParseError) as exc:
            self.add("PACKAGE_MEMBER_UNREADABLE", "A", "layer_properties.lyp", str(exc))
            return
        # A layer's name is the part before its purpose: "Metal.drawing" is "Metal".
        layers = {name.strip().split(".", 1)[0]
                  for name in (p.findtext("name") for p in tree.getroot().iter("properties"))
                  if name}
        for method, spec in sorted(self.manifest["external"].items()):
            for role, name in sorted(spec.items()):
                if name not in layers:
                    self.add("PACKAGE_LAYER_UNKNOWN", "A", f"external.{method}.{role}",
                             f"{name!r} is not a layer of the package's layer sheet")

    def check_stack(self) -> None:
        stack = self.json_file("stack", "stack.path")
        if stack is None:
            return
        for keys in _STACK_KEYS:
            if not _positive(_dig(stack, keys)):
                self.add("PACKAGE_STACK_KEY", "A", "stack.path",
                         f"{self.manifest['stack']['path']} has no positive number "
                         f"at {'.'.join(keys)}")

    def check_pairs(self) -> None:
        pairs = self.manifest.get("occupied_pairs")
        if pairs is not None and (pairs["state"] != "measured" or not pairs.get("measured_by")):
            self.add("PACKAGE_PAIRS_DECLARED", "A", "occupied_pairs",
                     "set A needs the pairs measured, with measured_by naming the run or test")

    # EM limits (set C) ---------------------------------------------------------

    def check_em_limits(self) -> None:
        self.json_file("em_limits", "em_limits.path")


def _by_identity(variants: Dict[str, _Variant]) -> Dict[str, List[str]]:
    """The readable variants under each ``identity.name`` they carry."""
    names: Dict[str, List[str]] = {}
    for variant, descriptor in sorted(variants.items()):
        if not isinstance(descriptor, _Unreadable):
            name = _dig(descriptor, ("identity", "name"))
            if isinstance(name, str):
                names.setdefault(name, []).append(variant)
    return names


# --- entry points -------------------------------------------------------------

def check_package(root, *, methods=None,
                  on_warn: Optional[Callable[[str], None]] = None) -> PackageReport:
    """Validate the package at ``root`` and report every set, without raising
    for a set that fails. A refusal (see the module docstring) still raises.
    ``methods`` is the path of an interconnect method registry, read for the
    pitches ``escape_capacity`` is held to."""
    root = Path(root)
    manifest = load_manifest(root, on_warn=on_warn)
    pitches = load_method_pitches(methods, on_warn=on_warn) if methods is not None else None
    checker = _Checker(root, manifest, pitches)
    checker.run()
    return PackageReport(manifest["id"], str(root), checker.findings, checker.notes)


def validate_package(root, *, require: Sequence[str] = ("A", "B"), methods=None,
                     on_warn: Optional[Callable[[str], None]] = None) -> PackageReport:
    """Like :func:`check_package`, but refuse unless every set in ``require``
    passes, naming every finding of those sets."""
    _known_sets(require)
    report = check_package(root, methods=methods, on_warn=on_warn)
    failing = report.failing(require)
    if failing:
        raise TechnologyPackageError(
            "PACKAGE_SET_INCOMPLETE",
            f"package {report.id!r} at {root} does not pass set {', '.join(failing)}:\n  "
            + "\n  ".join(f.line() for name in failing for f in report.findings[name]))
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m chiplet_format_io.technology_package ROOT``.

    Exit 0 when every required set passes, 1 when one does not, 2 when the
    package is refused before any set can be judged, 3 when the validator
    itself fails. Notes, findings and the set verdicts go to stdout, warnings
    and a refusal to stderr; ``--report`` writes the same as JSON in every
    case, with ``refused`` set when nothing could be judged.
    """
    parser = argparse.ArgumentParser(
        prog="python -m chiplet_format_io.technology_package",
        description="Validate a technology package and report its capability sets.")
    parser.add_argument("root", help=f"package root, the tree holding {MANIFEST_PATH}")
    parser.add_argument("--require", default="A,B",
                        help="sets that must pass, comma separated (default A,B)")
    parser.add_argument("--methods", help="interconnect method registry (interconnect_methods.json), "
                                          "to check escape capacity pitches against")
    parser.add_argument("--report", help="write the report as JSON to this file")
    args = parser.parse_args(argv)
    require = [s for s in args.require.split(",") if s]
    try:
        _known_sets(require)
    except ValueError as exc:
        parser.error(str(exc))

    def write(data: Dict[str, Any]) -> None:
        if args.report:
            Path(args.report).write_text(json.dumps(data, indent=2) + "\n")

    try:
        with warnings.catch_warnings():
            # on_warn prints each warning already; the channel would print it twice.
            warnings.simplefilter("ignore", ContractVersionWarning)
            report = check_package(
                args.root, methods=args.methods,
                on_warn=lambda text: print(f"PACKAGE_WARNING: {text}", file=sys.stderr))
    except TechnologyPackageError as exc:
        print(exc, file=sys.stderr)
        write(_refused_report(args.root, exc.message_id, exc.text))
        return 2
    except Exception as exc:  # a defect here must not read as a failing set
        traceback.print_exc()
        text = f"{type(exc).__name__}: {exc}"
        print(f"PACKAGE_VALIDATOR_FAILED: {text}", file=sys.stderr)
        write(_refused_report(args.root, "PACKAGE_VALIDATOR_FAILED", text))
        return 3
    for note in report.notes:
        print(note.line())
    for name in SET_MEMBERS:
        for finding in report.findings[name]:
            print(finding.line())
        print(report.verdict(name))
    write(report.as_dict())
    return 1 if report.failing(require) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
