# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Reader and admission checks for the die output contract sidecar.

The sidecar (``schemas/die_contract.schema.json``) is what a die knows about
itself: which process produced it, which pad inventory is authoritative for it,
what each terminal is for, and what intrinsic verification has been recorded
over its bytes. This module reads one, binds it to the inventory it names, and
answers the questions a strict admission profile has to ask.

Three rules are load-bearing here rather than in the schema, because a schema
can only say what a document looks like:

* **The inventory is bound by digest, not by path.** The pad geometry and its
  resolution already existed before this contract; what did not exist was any
  statement of WHICH inventory is authoritative for a component, so a stale or
  regenerated pin list resolved cleanly against whatever the caller passed.
  Every function here refuses on a digest mismatch rather than reading on.

* **Identity is ``(name, pad_index)``, and a repeated name is refused rather
  than selected.** ``interposer_pnr.pads.bump_map`` hands out same-name pads by
  OCCURRENCE ORDER (``group[k]``), so a selector carried in the assembly would be
  ignored by the consumer that places the bump and a different physical pad would
  be chosen in silence. That makes an index selector a MAJOR change under
  docs/VERSION_POLICY.md, not an optional addition, so this profile takes the
  narrow route instead: unique physical names, canonically bound at index 0, and
  an outright refusal wherever a name repeats until an index-aware consumer
  exists. A black-box padmap carries no index at all, so a repeated name there is
  refused for the same reason and never ordered by file position.

* **An attestation entry is a record, not an authorization.** A resolvable
  evidence digest is not enough: the entry must bind the same layout this
  contract describes, name the deck applied, and record that the execution
  completed. Anything less is ``unresolved``, whatever the entry claims. That is
  a third outcome on purpose: a two-value verdict has nowhere to put an
  observation that is neither, so an ambiguous one gets resolved toward whatever
  the rest of the record already suggests.

* **Every path is relative to the sidecar.** An absolute path, or one climbing
  out of the sidecar's directory, is refused, so the contract and everything it
  binds move together instead of resolving differently on another machine.

* **A newer minor is read element by element.** A document at the minor this
  reader was written for is held to the complete shipped schema, and anything
  it does not know refuses the document: at that version an unknown value is
  out of contract, since only a higher minor can carry an addition (the
  ``solder_bump`` row of docs/VERSION_POLICY.md: a value no producer may emit
  before the minor that adds it). A document
  declaring a HIGHER minor of the same major is held to the same schema with its
  closed keys and vocabularies opened and nothing else. An unknown key is then
  ignored and reported; an unknown value refuses the element that carries it
  wherever that element is read, and is reported; and a die whose admission
  reads a refused element is incomplete, never admitted and never refused on
  that account. That is what makes a new optional
  key or vocabulary member a MINOR (docs/VERSION_POLICY.md): a reader that
  refused the document instead would turn every such addition into a MAJOR.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple

from . import ChipletFormatError, _parse_contract_version, check_contract_version
from .supply_domains import SupplyDomainError, read_supply_domains

__all__ = [
    "STRICT_ADMISSION_API_VERSION",
    "DIE_CONTRACT_SCHEMA",
    "SUPPORTED_DIE_CONTRACT_VERSION",
    "ACCEPTED_DIE_CONTRACT_VERSIONS",
    "TERMINAL_ROLES",
    "TERMINAL_DIRECTIONS",
    "die_contract_schema",
    "validate_die_contract",
    "DieContractError",
    "DieContractIncomplete",
    "Unrecognised",
    "BoundContract",
    "digest_file",
    "load_die_contract",
    "bind",
    "inventory_identities",
    "check_terminals",
    "attestation_state",
    "admit_component",
    "resolve_endpoint",
    "resolve_port",
    "port_terminals",
    "no_port_identity",
]

#: Library capability, NOT a document version or physical-qualification claim.
#: Revision 1 identifies the reviewed full-schema/DBU/artwork binding boundary
#: with shipped schema resources and unresolved attestations absent an evaluator.
#: Revision 2 keeps all of it for documents at the accepted minor and reads a
#: higher minor element by element (docs/STRICT_ADMISSION_API.md). A consumer
#: names the revisions it accepts; revision 2 admits documents revision 1
#: refuses, so neither is a safe stand-in for the other. Consumers must not infer
#: it from callable names or the package's version string alone.
#: Revision 3 moves the accepted minor to 1.1 and reads the port identity. It
#: MOVES A VERDICT, and the move is larger than the addition: a document stamped
#: 1.1 is now at a minor this reader fully implements, so the element-level
#: reading stops applying to it and ANY difference from the shipped 1.1 schema
#: refuses the document. Three classes: an unknown key revision 2 ignored and
#: warned about, an attestation entry revision 2 resolved to ``unresolved``
#: (both of which revision 2 ADMITTED), and an unknown role, direction, opening
#: purpose or shape, inventory kind or die frame, which revision 2 reported as
#: :class:`DieContractIncomplete`. A 1.2 document is still read element by
#: element, and a 1.0 document carrying the 1.1 port identity is now refused.
#: Revision 4 moves the accepted minor to 1.2 and reads ``supply_domains``, the
#: same move one minor on: a 1.2 document is held to the shipped 1.2 schema, so
#: what revision 3 ignored, resolved to unresolved or reported incomplete on it
#: now refuses it, and a 1.3 document is read element by element. A 1.1
#: document carrying ``supply_domains`` is refused; 1.0 and 1.1 documents keep
#: every verdict.
STRICT_ADMISSION_API_VERSION = 4

#: Discriminator every die contract carries (the boundary-manifest idiom).
DIE_CONTRACT_SCHEMA = "die-output-contract"

#: The version this reference implementation writes.
SUPPORTED_DIE_CONTRACT_VERSION = "1.2"

#: The MAJOR.MINOR floors this reader accepts, one per major.
ACCEPTED_DIE_CONTRACT_VERSIONS = ("1.2",)

#: Where each schema node was introduced, for every node added after 1.0.
#: A document is validated against the schema AS OF the minor it declares, so a
#: key a later minor added is out of contract below it (the ``solder_bump`` rule
#: of docs/VERSION_POLICY.md) and is refused with a detector rather than by
#: convention. A minor that edits the schema and adds no row here is caught by
#: the conformance baseline, which compares the reconstructed 1.0 schema against
#: the bytes 1.0 shipped.
INTRODUCED_AT: Dict[Tuple[str, ...], str] = {
    ("properties", "terminals", "items", "properties", "port"): "1.1",
    ("properties", "terminals", "items", "properties", "ordinal"): "1.1",
    ("properties", "terminals", "items", "dependentRequired"): "1.1",
    ("properties", "supply_domains"): "1.2",
}

#: Closed role vocabulary. Deliberately separate from ``direction`` and
#: ``supply_domain``: an analog and a bidirectional signal share a role, and two
#: power terminals in different domains are not interchangeable.
TERMINAL_ROLES = ("signal", "power", "ground", "no_connect")
TERMINAL_DIRECTIONS = ("input", "output", "bidir", "analog", "none")
OPENING_PURPOSES = ("conductor", "aperture")
OPENING_SHAPES = ("rectangle", "octagon", "circle")
INVENTORY_KINDS = ("pins_json", "blackbox_padmap")
DIE_FRAME = "die_local_gds_origin"
_SCHEMA_CACHE: Optional[Dict[str, Any]] = None
_READER_SCHEMA_CACHE: Optional[Dict[str, Any]] = None
_SCHEMA_RESOURCE = "die_contract.schema.json"

# What a newer minor may do at each closed node of the shipped schema, keyed by
# the node's path and the keyword that closes it. Every closed node has exactly
# one entry, and a conformance test walks the shipped schema to hold that, so a
# node added to the schema without a decision here is caught before it ships and
# read as incomplete, never admitted, if it ships anyway.
IGNORED = "ignored"
TERMINAL_REFUSED = "terminal_refused"
#: Not a closed node's disposition: the one finding this reader raises on its
#: own, for a terminal that reads while what its port identity means does not.
#: Distinct from TERMINAL_REFUSED because the terminal is not refused; a
#: consumer that resolves by pad name is entitled to carry on using it.
PORT_UNREADABLE = "port_unreadable"
OPENING_REFUSED = "opening_refused"
ENTRY_UNRESOLVED = "entry_unresolved"
BINDING_INCOMPLETE = "binding_incomplete"
DOCUMENT_REFUSED = "document_refused"

_TERMINAL = ("properties", "terminals", "items")
_OPENING = _TERMINAL + ("properties", "opening")
_ATTESTATION = ("properties", "attestation", "items")
DISPOSITIONS: Dict[Tuple[Tuple[str, ...], str], str] = {
    ((), "additionalProperties"): IGNORED,
    (("properties", "schema"), "const"): DOCUMENT_REFUSED,
    (("properties", "die"), "additionalProperties"): IGNORED,
    (("properties", "die", "properties", "frame"), "const"): BINDING_INCOMPLETE,
    (("properties", "process"), "additionalProperties"): IGNORED,
    (("properties", "inventory"), "additionalProperties"): IGNORED,
    (("properties", "inventory", "properties", "kind"), "enum"): BINDING_INCOMPLETE,
    (("properties", "build_record"), "additionalProperties"): IGNORED,
    (_TERMINAL, "additionalProperties"): IGNORED,
    (_TERMINAL + ("properties", "role"), "enum"): TERMINAL_REFUSED,
    (_TERMINAL + ("properties", "direction"), "enum"): TERMINAL_REFUSED,
    (_OPENING, "additionalProperties"): IGNORED,
    (_OPENING + ("properties", "purpose"), "enum"): OPENING_REFUSED,
    (_OPENING + ("properties", "shape"), "enum"): OPENING_REFUSED,
    # An attestation entry is a claim, and a claim is read whole or not at all:
    # evaluating it with a key skipped evaluates a claim nobody recorded. Not
    # knowing is the incomplete outcome for an entry, so it resolves to that.
    (_ATTESTATION, "additionalProperties"): ENTRY_UNRESOLVED,
    (_ATTESTATION + ("properties", "check"), "enum"): ENTRY_UNRESOLVED,
    (_ATTESTATION + ("properties", "result"), "enum"): ENTRY_UNRESOLVED,
    (_ATTESTATION + ("properties", "execution"), "enum"): ENTRY_UNRESOLVED,
    # A newer minor may say more about a domain than its volts; the volts still read.
    (("properties", "supply_domains", "additionalProperties"), "additionalProperties"): IGNORED,
}

#: Pairings this minor requires that a later one may relax, enforced at the
#: floor by the shipped schema and nowhere above it.
#:
#: ``dependentRequired`` is not a closed node, so the opening walk never sees it
#: and it would otherwise survive into the newer-minor path and refuse a
#: higher-minor DOCUMENT for a combination only this minor forbids. That is the
#: refusal docs/VERSION_POLICY.md reserves for bytes with no conforming reading,
#: and half a port identity has one: the terminal reads, and only what its port
#: identity means is unread. :func:`_port_identity` carries that from here.
RELAXABLE: Tuple[Tuple[Tuple[str, ...], str], ...] = ((_TERMINAL, "dependentRequired"),)


class DieContractError(ChipletFormatError):
    """A die contract is malformed, unbindable, or refused by the profile.

    A subclass of :class:`ChipletFormatError` so a consumer that already catches
    that keeps working; catch this one to tell a die-side refusal apart from a
    malformed assembly document.
    """


class Unrecognised(NamedTuple):
    """One key or value a newer minor carries that this reader does not know."""

    pointer: str
    kind: str  # "key" or "value"
    value: Any
    disposition: str


class DieContractIncomplete(DieContractError):
    """The contract refuses nothing this reader can read, and it cannot read all of it.

    Raised instead of admitting when an element of a newer minor carries a value
    this reader does not know. It is "cannot tell", not "the contract said no":
    a consumer that separates the two maps this to its undecidable outcome, and
    one that only catches :class:`DieContractError` still fails closed.
    """

    def __init__(self, message: str, unrecognised: Iterable[Unrecognised] = ()) -> None:
        super().__init__(message)
        self.unrecognised = tuple(unrecognised)


class BoundContract:
    """A die contract together with the inventory it actually binds.

    Construction is the check: an instance exists only if the sidecar parsed,
    its version is accepted, its inventory resolved, and the inventory's bytes
    hashed to what the contract recorded.
    """

    def __init__(self, contract: Dict[str, Any], path: Path,
                 inventory: Optional[Dict[str, Any]], inventory_path: Path) -> None:
        self.contract = contract
        self.path = path
        self.inventory = inventory
        self.inventory_path = inventory_path
        self.kind = contract["inventory"]["kind"]
        #: What a newer minor carried that this reader does not know, as bound.
        self.unrecognised: Tuple[Unrecognised, ...] = ()
        # A kind or frame this reader does not know, left for admission to raise
        # once every refusal has had its chance. Never set outside this module.
        self._pending: Tuple[Unrecognised, ...] = ()
        self._pending_reason = ""

    @property
    def terminals(self) -> List[Dict[str, Any]]:
        """The terminals, carrying only keys this reader knows.

        At the accepted minor that is every key. For a newer minor an unknown
        key is left out here, so no reader path can act on a key the reader has
        just reported it does not know; ``contract`` keeps the document as
        written, for a consumer that repackages it.
        """
        terms = list(self.contract.get("terminals") or [])
        if not reads_element_level(self.contract):
            return terms
        return [_known_terminal_keys(t) if isinstance(t, dict) else t for t in terms]

    def identities(self) -> Dict[Tuple[str, int], Dict[str, Any]]:
        """Physical identities the bound inventory actually offers."""
        return inventory_identities(self.inventory, self.kind)


def digest_file(path) -> str:
    """sha256 of a file, streamed. The one way bytes are named in this module."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _require_mapping(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise DieContractError(f"{what} must be a mapping")
    return value


def _portable(root: Path, raw: Any, what: str) -> Path:
    """Resolve a contract-relative path, refusing anything that does not travel.

    An absolute path names a machine rather than a bundle, and a path climbing
    out of the sidecar's directory names something the bundle does not carry.
    Both resolve fine on the machine that wrote them, which is exactly why they
    have to be refused here rather than discovered somewhere else.
    """
    if not isinstance(raw, str) or not raw:
        raise DieContractError(f"{what}: path is missing")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise DieContractError(
            f"{what}: {raw!r} is absolute; paths are relative to the sidecar so the "
            "contract and what it binds move together"
        )
    resolved = (root / candidate).resolve()
    if root.resolve() not in resolved.parents and resolved != root.resolve():
        raise DieContractError(
            f"{what}: {raw!r} resolves outside the sidecar's directory"
        )
    return resolved


def _check_digest(path: Path, expected: Any, what: str) -> None:
    if not isinstance(expected, str) or len(expected) != 64:
        raise DieContractError(f"{what}: sha256 must be a 64-character digest")
    if not path.is_file():
        raise DieContractError(f"{what}: {path} does not exist, so its digest cannot be checked")
    actual = digest_file(path)
    if actual != expected:
        raise DieContractError(
            f"{what}: {path} hashes to {actual}, the contract records {expected}; "
            "the bound artifact is not the one this contract describes"
        )


def die_contract_schema() -> Dict[str, Any]:
    """The shipped schema document, read from disk rather than restated here.

    A copy of a schema inside the validator is a second source of truth that
    drifts silently, so this locates the one that ships. If it cannot be found
    the strict path REFUSES; it does not fall back to whatever checks happen to
    be written in Python, because a validator that quietly becomes weaker is
    worse than one that stops.
    """
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        text = _read_packaged_schema(_SCHEMA_RESOURCE)
        if text is None:
            raise DieContractError(
                f"{_SCHEMA_RESOURCE} is not present in this installation of "
                "chiplet_format_io, so strict admission cannot enforce the shipped "
                "schema and refuses rather than admitting on whatever checks happen "
                "to be written here"
            )
        _SCHEMA_CACHE = json.loads(text)
    return _SCHEMA_CACHE


def _canonical_schema_dir(module_dir: Path) -> Optional[Path]:
    """The repository's canonical ``schemas`` directory, or None.

    Only an UNBUILT CHECKOUT gets this. Walking three parents up from an
    installed module lands in the environment's ``lib`` directory, and
    ``<venv>/lib/schemas`` is an ambient path that can exist and hold documents
    belonging to something else entirely; reading a foreign schema and calling
    it ours is worse than refusing. So the directory above the module has to be
    the layout this repository actually has, and the build metadata at that
    level has to name this project, before the canonical directory is reached.
    """
    if module_dir.parent.name != "python" or module_dir.parents[1].name != "reference":
        return None
    pyproject = module_dir.parent / "pyproject.toml"
    try:
        if 'name = "chiplet-format-io"' not in pyproject.read_text():
            return None
    except OSError:
        return None
    canonical = module_dir.parents[2] / "schemas"
    return canonical if canonical.is_dir() else None


def _read_packaged_schema(name: str) -> Optional[str]:
    """Read a schema that SHIPS WITH the package, checkout and wheel alike.

    The wheel carries the real files, copied in at build time from the one
    canonical directory at the repository root. A checkout that was never built
    reads that same directory, so there is one source either way and no second
    copy to keep in step.
    """
    try:
        from importlib.resources import files
    except ImportError:  # Python 3.8
        files = None
    if files is not None:
        try:
            return (files(__package__) / "schemas" / name).read_text()
        except (FileNotFoundError, NotADirectoryError, ModuleNotFoundError, OSError):
            pass
    module_dir = Path(__file__).resolve().parent
    candidates = [module_dir / "schemas" / name]
    canonical = _canonical_schema_dir(module_dir)
    if canonical is not None:
        candidates.append(canonical / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text()
    return None


def _jsonschema():
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - exercised by monkeypatch
        raise DieContractError(
            "jsonschema is required for strict die-contract admission and is not "
            "installed; strict admission refuses rather than degrading to a partial "
            "check that would look like a pass"
        ) from exc
    return jsonschema


_AS_OF_CACHE: Dict[str, Dict[str, Any]] = {}
_CLASSIFY_CACHE: Optional[Dict[str, Any]] = None
_DERIVED_BASE: Optional[Dict[str, Any]] = None

#: How many reconstructed minors to keep. The version is whatever the document
#: declares, so an unbounded dict keyed on it grows with the documents read;
#: two minors exist and a reader that thrashes this is already reading nonsense.
_AS_OF_LIMIT = 8


def _fresh(shipped: Dict[str, Any]) -> None:
    """Drop every schema derived from a schema object that is no longer the one.

    Identity, not a flag a caller has to remember: a test that replaces the
    loaded schema would otherwise keep derivatives of the schema it replaced,
    and a stale derivative is a wrong verdict nobody would attribute to a cache.
    Every derivative means every one, the reader schema included; a function that
    drops two of the three is the same trap with a smaller blast radius.
    """
    global _DERIVED_BASE, _CLASSIFY_CACHE, _READER_SCHEMA_CACHE
    if _DERIVED_BASE is not shipped:
        _DERIVED_BASE, _CLASSIFY_CACHE, _READER_SCHEMA_CACHE = shipped, None, None
        _AS_OF_CACHE.clear()


def _relaxed(shipped: Dict[str, Any]) -> Dict[str, Any]:
    """``shipped`` without the pairings a later minor may relax."""
    derived = copy.deepcopy(shipped)
    for path, keyword in RELAXABLE:
        _node(derived, path).pop(keyword, None)
    return derived


def _classify_schema() -> Dict[str, Any]:
    """The schema a document above the floor is measured against, difference by difference."""
    global _CLASSIFY_CACHE
    shipped = die_contract_schema()
    _fresh(shipped)
    if _CLASSIFY_CACHE is None:
        _CLASSIFY_CACHE = _relaxed(shipped)
    return _CLASSIFY_CACHE


def _schema_as_of(value: Any) -> Dict[str, Any]:
    """The shipped schema as it stood at the version ``value`` declares.

    Reconstructed from the one schema that ships, never kept as a second file
    per minor: a per-minor copy is a second source of truth, and this one is
    checked against the bytes 1.0 shipped by a conformance baseline.

    Why a document is held to its OWN minor rather than to the newest one: a key
    a later minor added may not be written before that minor (the ``solder_bump``
    rule), and a reader that accepted it anyway would read a document its own
    floor-1.0 peers refuse. A missing or malformed version gets the complete
    schema, which refuses the document on the version key itself.
    """
    parsed = _parse_contract_version(value)
    shipped = die_contract_schema()
    _fresh(shipped)
    if parsed is None:
        return shipped
    later = sorted(path for path, added in INTRODUCED_AT.items()
                   if _parse_contract_version(added) > parsed)
    if not later:
        return shipped
    key = f"{parsed[0]}.{parsed[1]}"
    if len(_AS_OF_CACHE) >= _AS_OF_LIMIT:
        _AS_OF_CACHE.clear()      # the key comes from the document; it is not a budget
    if key not in _AS_OF_CACHE:
        derived = copy.deepcopy(shipped)
        for path in later:
            _node(derived, path[:-1]).pop(path[-1], None)
        _AS_OF_CACHE[key] = derived
    return _AS_OF_CACHE[key]


def validate_die_contract(data: Dict[str, Any], *, where: str = "die contract") -> None:
    """Validate a parsed contract against the shipped schema AS OF its version."""
    _validate(data, _schema_as_of(data.get("version") if isinstance(data, dict) else None),
              where)


def _validate(data: Dict[str, Any], schema: Dict[str, Any], where: str) -> None:
    jsonschema = _jsonschema()
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        location = "/".join(str(x) for x in exc.absolute_path) or "(document)"
        raise DieContractError(
            f"{where}: {location} violates the shipped schema: {exc.message}"
        ) from exc


def closed_nodes(schema: Dict[str, Any]) -> List[Tuple[Tuple[str, ...], str]]:
    """Every node of ``schema`` that closes a key set or a value.

    That is ``additionalProperties: false``, ``unevaluatedProperties: false``,
    an enum or a const, wherever it sits: under a combinator's list as well as
    under a property, and under a property whose NAME happens to be a keyword.
    A node this walk cannot see is one the reader schema never opens, which
    would refuse a newer minor as a document.
    """
    out: List[Tuple[Tuple[str, ...], str]] = []
    names = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
    values = ("enum", "const", "description", "examples", "default")

    def walk(node: Any, path: Tuple[str, ...], is_map: bool = False) -> None:
        if isinstance(node, list):
            for i, child in enumerate(node):
                walk(child, path + (str(i),))
            return
        if not isinstance(node, dict):
            return
        if is_map:  # keys are names, every value is a schema
            for key, child in node.items():
                walk(child, path + (key,))
            return
        for keyword in ("additionalProperties", "unevaluatedProperties"):
            if node.get(keyword) is False:
                out.append((path, keyword))
        for keyword in ("enum", "const"):
            if keyword in node:
                out.append((path, keyword))
        for key, child in node.items():
            if key not in values:
                walk(child, path + (key,), is_map=key in names)

    walk(schema, ())
    return out


def _node(schema: Any, path: Tuple[str, ...]) -> Any:
    for key in path:
        schema = schema[int(key)] if isinstance(schema, list) else schema[key]
    return schema


def _reader_schema() -> Dict[str, Any]:
    """The shipped schema with its closed nodes opened, and nothing else.

    Derived in memory from the one schema that ships, never kept as a second
    file. Every type, required key, pattern, minimum and length survives, so
    bytes with no conforming reading at all are still refused as a document.
    A vocabulary opens to a non-empty string only, so an empty role is still
    malformed rather than a member this reader has not heard of. The
    discriminator stays closed: a different artifact is not a newer die contract.

    The pairings in ``RELAXABLE`` come out here as well, which is not an opening
    but a removal: they say what this minor requires together, and a later minor
    that stops requiring it writes bytes with a conforming reading.
    """
    global _READER_SCHEMA_CACHE
    shipped = die_contract_schema()
    _fresh(shipped)
    if _READER_SCHEMA_CACHE is None:
        derived = _relaxed(shipped)
        for path, keyword in closed_nodes(shipped):
            if DISPOSITIONS.get((path, keyword)) == DOCUMENT_REFUSED:
                continue
            node = _node(derived, path)
            if keyword in ("additionalProperties", "unevaluatedProperties"):
                del node[keyword]
            else:
                del node[keyword]
                node["type"] = "string"
                node["minLength"] = 1
        _READER_SCHEMA_CACHE = derived
    return _READER_SCHEMA_CACHE


def _classify(data: Dict[str, Any], where: str) -> List[Unrecognised]:
    """Name what a newer minor carries beyond the shipped schema, node by node.

    Runs after the reader schema has passed, so the only differences left are at
    closed nodes. One with no disposition means the schema grew a closed node no
    decision was made for; that is read as incomplete, never guessed at.
    """
    jsonschema = _jsonschema()
    shipped = _classify_schema()
    validator = jsonschema.validators.validator_for(shipped)(shipped)
    found: List[Unrecognised] = []
    for err in validator.iter_errors(data):
        schema_path = tuple(str(x) for x in err.schema_path)
        node, keyword = schema_path[:-1], schema_path[-1] if schema_path else ""
        disposition = DISPOSITIONS.get((node, keyword))
        pointer = "/".join(str(x) for x in err.absolute_path)
        if disposition in (None, DOCUMENT_REFUSED) or err.validator != keyword:
            raise DieContractIncomplete(
                f"{where}: {pointer or '(document)'} differs from the shipped schema at a "
                f"node this reader has no disposition for ({err.validator}); it stops "
                "rather than admit what it cannot classify", found)
        if keyword == "additionalProperties":
            known = set(_node(shipped, node).get("properties") or {})
            for key in sorted(k for k in err.instance if k not in known):
                found.append(Unrecognised(f"{pointer}/{key}" if pointer else key,
                                          "key", key, disposition))
        else:
            found.append(Unrecognised(pointer, "value", err.instance, disposition))
    return found


def reads_element_level(contract: Dict[str, Any]) -> bool:
    """True when ``contract`` declares a minor above the one this reader accepts."""
    parsed = _parse_contract_version(contract.get("version"))
    if parsed is None:
        return False
    for floor in ACCEPTED_DIE_CONTRACT_VERSIONS:
        major, minor = _parse_contract_version(floor)
        if parsed[0] == major:
            return parsed[1] > minor
    return False


def _word(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _known_terminal_keys(term: Dict[str, Any]) -> Dict[str, Any]:
    item = _node(die_contract_schema(), _TERMINAL)
    known = {k: v for k, v in term.items() if k in item["properties"]}
    opening = known.get("opening")
    if isinstance(opening, dict):
        allowed = item["properties"]["opening"]["properties"]
        known["opening"] = {k: v for k, v in opening.items() if k in allowed}
    return known


def _describe(finding: Unrecognised) -> str:
    what = {
        IGNORED: "ignored",
        TERMINAL_REFUSED: "the terminal is refused",
        OPENING_REFUSED: "the opening is refused wherever it is read",
        ENTRY_UNRESOLVED: "the attestation entry is unresolved",
        BINDING_INCOMPLETE: "the binding is incomplete",
        PORT_UNREADABLE: "the terminal reads and its port identity does not",
    }.get(finding.disposition, finding.disposition)
    return (f"{finding.pointer} carries the {finding.kind} {finding.value!r}, which "
            f"this reader does not know; {what}")


def _read(p: Path, on_warn) -> Tuple[Dict[str, Any], List[Unrecognised]]:
    if not p.is_file():
        raise DieContractError(f"die contract {p} does not exist")
    try:
        data = json.loads(p.read_text())
    except ValueError as exc:
        raise DieContractError(f"die contract {p} is not valid JSON: {exc}") from exc
    data = _require_mapping(data, f"die contract {p}")
    if data.get("schema") != DIE_CONTRACT_SCHEMA:
        raise DieContractError(
            f"die contract {p}: schema is {data.get('schema')!r}, expected "
            f"{DIE_CONTRACT_SCHEMA!r}; this is a different artifact"
        )
    check_contract_version(data.get("version"), ACCEPTED_DIE_CONTRACT_VERSIONS,
                           name="die contract", on_warn=on_warn)
    found: List[Unrecognised] = []
    if reads_element_level(data):
        _validate(data, _reader_schema(), str(p))
        found = _classify(data, str(p)) + _unreadable_ports(data)
        if on_warn is not None:
            for finding in found:
                on_warn(f"die contract {p}: {_describe(finding)}")
    else:
        # The shipped schema, invoked. Hand-checking the fields someone thought
        # of is how a contract with no die.name, no die.dbu_um and a direction of
        # "teleport" walked through a validator that looked thorough: every one
        # of those is refused by the schema and by none of the checks beside it.
        validate_die_contract(data, where=str(p))
    _check_supply_domains(data, str(p), above_floor=reads_element_level(data))
    process = _require_mapping(data.get("process"), "process")
    for key in ("pdk", "pdk_version"):
        if not isinstance(process.get(key), str) or not process[key]:
            raise DieContractError(
                f"die contract {p}: process.{key} must be a non-empty string; a die "
                "whose process is unstated cannot be checked against any deck"
            )
    return data, found


def _check_supply_domains(data: Dict[str, Any], where: str, *, above_floor: bool) -> None:
    """Refuse a ``supply_domains`` the terminals contradict
    (:mod:`.supply_domains`, which states the rules). Only a terminal whose
    role this reader knows names a domain there; one a newer minor's role
    refuses is not read, and the domain it names is not refused above the
    floor."""
    try:
        read_supply_domains(data, where, above_floor=above_floor)
    except SupplyDomainError as exc:
        raise DieContractError(str(exc)) from exc


def load_die_contract(path, *, on_warn=None) -> Dict[str, Any]:
    """Parse and version-check one die contract sidecar.

    Structure beyond the discriminator and the version is the schema's job; what
    this refuses is a document that is not a die contract at all, or one whose
    major this reader cannot read. A newer minor is read element by element, and
    each key or value this reader does not know goes to ``on_warn``.
    """
    return _read(Path(path), on_warn)[0]


def bind(contract_path, *, base_dir=None, check_layout: bool = True,
         on_warn=None) -> BoundContract:
    """Load a die contract and bind the inventory (and layout) it names.

    Paths inside the contract are verbatim, as everywhere else in this format;
    ``base_dir`` is where a relative one is resolved from, defaulting to the
    directory holding the contract.

    For a newer minor whose inventory kind or frame this reader does not know,
    everything that can still refuse is checked first, and then this raises
    :class:`DieContractIncomplete`: a caller that only binds never receives
    terminals it cannot read or coordinates in a frame it does not know.
    """
    bound = _bind(Path(contract_path), base_dir, check_layout, on_warn)
    if bound._pending:
        raise DieContractIncomplete(bound._pending_reason, bound.unrecognised)
    return bound


def _bind(p: Path, base_dir, check_layout: bool, on_warn) -> BoundContract:
    """Bind, leaving an unknown kind or frame pending rather than raised.

    Admission needs this: a refusal the unknown element does not affect (other
    artwork, a duplicate terminal, the wrong units) has to win over it, and those
    checks run after binding.
    """
    contract, found = _read(p, on_warn)
    root = Path(base_dir) if base_dir is not None else p.parent
    newer = reads_element_level(contract)
    pending: List[Unrecognised] = []
    reasons: List[str] = []

    build = _require_mapping(contract.get("build_record"), "build_record")
    _check_digest(_portable(root, build.get("path"), "build_record"),
                  build.get("sha256"), "build_record")

    inv = _require_mapping(contract.get("inventory"), "inventory")
    kind = inv.get("kind")
    unknown_kind = kind not in INVENTORY_KINDS
    if unknown_kind and not (newer and _word(kind)):
        raise DieContractError(f"inventory.kind {kind!r} is not a known inventory contract")
    inv_path = _portable(root, inv.get("path"), "inventory")
    _check_digest(inv_path, inv.get("sha256"), "inventory")
    die = _require_mapping(contract.get("die"), "die")
    inventory: Optional[Dict[str, Any]] = None
    if unknown_kind:
        # The inventory is only readable through its kind, so it is not read;
        # its digest is still checked above, so a stale one is still refused.
        pending.append(Unrecognised("inventory/kind", "value", kind, BINDING_INCOMPLETE))
        reasons.append(f"inventory.kind {kind!r} is not an inventory contract this reader "
                       "knows, so no terminal can be read through it")
    else:
        try:
            inventory = json.loads(inv_path.read_text())
        except ValueError as exc:
            raise DieContractError(f"inventory {inv_path} is not valid JSON: {exc}") from exc
        inventory = _require_mapping(inventory, f"inventory {inv_path}")

    if check_layout:
        _check_digest(_portable(root, die.get("layout"), "die.layout"),
                      die.get("layout_sha256"), "die.layout")
    frame_known = die.get("frame") == DIE_FRAME
    if not frame_known and newer and _word(die.get("frame")):
        pending.append(Unrecognised("die/frame", "value", die["frame"], BINDING_INCOMPLETE))
        reasons.append(f"die.frame is {die['frame']!r}, a frame this reader does not know, "
                       "so the die box cannot be compared in it")
    else:
        frame_known = True  # a non-word frame is refused inside the box check
    _check_die_box(die, inventory, kind, frame_known=frame_known)

    bound = BoundContract(contract, p, inventory, inv_path)
    bound.unrecognised = tuple(found)
    bound._pending = tuple(pending)
    bound._pending_reason = "; ".join(reasons)
    return bound


_BOX_KEYS = ("x_min", "y_min", "x_max", "y_max")


def _check_die_box(die: Dict[str, Any], inventory: Optional[Dict[str, Any]], kind: str,
                   *, frame_known: bool = True) -> None:
    """Bind the contract's die box to the inventory's own, in one frame.

    The terminals and the outline have to be known to describe ONE die. Without
    this they can drift apart with nothing to notice, since each is separately
    plausible. With no inventory (a kind this reader does not know) only the box
    itself is checked; in a frame it does not know, everything but the final
    comparison is, because the units and the inventory's box do not depend on it.
    """
    if frame_known and die.get("frame") != "die_local_gds_origin":
        raise DieContractError(
            f"die.frame is {die.get('frame')!r}; the only frame this profile binds is "
            "die_local_gds_origin, and an unstated frame is the one that mixes silently"
        )
    box = die.get("bbox_um")
    if not isinstance(box, list) or len(box) != 4 or not all(_finite(v) for v in box):
        raise DieContractError("die.bbox_um must be four finite numbers")
    if box[0] >= box[2] or box[1] >= box[3]:
        raise DieContractError(f"die.bbox_um {box} has no positive extent")
    if inventory is None:
        return

    if kind == "pins_json":
        raw = inventory.get("die_bbox_dbu")
        if raw is None:
            raise DieContractError(
                "the bound pins.json declares no die_bbox_dbu, so the terminals cannot be "
                "tied to the outline this contract states; pins.json leaves the key optional "
                "and this profile does not, because a contract that cannot bind its own "
                "outline is stating two separately plausible things about one die"
            )
        if not isinstance(raw, dict):
            raise DieContractError(
                f"die_bbox_dbu is a {type(raw).__name__}; pins.json declares it an object "
                "with x_min, y_min, x_max and y_max, and a positional form is a different "
                "format that happens to have four numbers in it"
            )
        missing = [k for k in _BOX_KEYS if not _finite(raw.get(k))]
        if missing:
            raise DieContractError(
                "die_bbox_dbu carries no finite " + ", ".join(missing)
            )
        dbu = inventory.get("dbu_um")
        if not _finite(dbu) or dbu <= 0:
            raise DieContractError("the bound pins.json has no usable dbu_um")
        # Semantic, so the schema cannot state it: both documents carry a dbu_um
        # and nothing compared them. A contract at 1.0 bound to a pin list at
        # 0.001 describes terminals a thousand times out of place, and the box
        # comparison below cannot see it because it scales by the inventory's
        # own unit, which is self-consistent whatever the die claims.
        if die.get("dbu_um") != dbu:
            raise DieContractError(
                f"die.dbu_um is {die.get('dbu_um')!r} and the bound inventory works in "
                f"{dbu!r}; the terminals and the outline are then in different units and "
                "every coordinate agreement between them is a coincidence"
            )
        other = [raw[k] * dbu for k in _BOX_KEYS]
    else:
        other = (inventory.get("die") or {}).get("bbox_um")
        if not isinstance(other, list) or len(other) != 4:
            raise DieContractError("the bound padmap declares no usable die.bbox_um")

    if not frame_known:
        return
    if any(abs(a - b) > 1e-6 for a, b in zip(box, other)):
        raise DieContractError(
            f"die.bbox_um {box} does not match the bound inventory's die box {list(other)}; "
            "the terminals and the outline describe different dies"
        )


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and value == value and value not in (float("inf"), float("-inf"))


def inventory_identities(inventory: Dict[str, Any],
                         kind: str) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """Map ``(name, pad_index)`` to the pad record the inventory holds.

    ``pins_json`` carries the index; a black-box padmap does not, so a repeated
    name there is undecidable and refused rather than ordered by file position.
    That refusal is the point: file order is not a fact about the design.
    """
    out: Dict[Tuple[str, int], Dict[str, Any]] = {}
    if kind == "pins_json":
        for i, pad in enumerate(inventory.get("pins") or []):
            if not isinstance(pad, dict):
                raise DieContractError(f"inventory pins[{i}] must be a mapping")
            name, index = pad.get("name"), pad.get("pad_index")
            if not isinstance(name, str) or not name:
                raise DieContractError(f"inventory pins[{i}] has no usable name")
            if not isinstance(index, int) or isinstance(index, bool):
                raise DieContractError(
                    f"inventory pin {name!r}[{i}] has no integer pad_index; "
                    "pins.json requires it and identity depends on it"
                )
            if (name, index) in out:
                raise DieContractError(
                    f"inventory declares pad {name!r} index {index} twice; "
                    "a physical identity cannot name two pads"
                )
            out[(name, index)] = pad
        return out

    seen: Dict[str, int] = {}
    for i, pad in enumerate(inventory.get("pads") or []):
        if not isinstance(pad, dict):
            raise DieContractError(f"inventory pads[{i}] must be a mapping")
        name = pad.get("name")
        if not isinstance(name, str) or not name:
            raise DieContractError(f"inventory pads[{i}] has no usable name")
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            raise DieContractError(
                f"black-box padmap declares pad {name!r} more than once and carries no "
                "pad index, so which physical pad a terminal means is undecidable; "
                "bind a pins.json inventory or give the pads distinct names"
            )
        out[(name, 0)] = pad
    return out


def _terminal_identity(term: Dict[str, Any], where: str) -> Tuple[str, int]:
    name, index = term.get("name"), term.get("pad_index")
    if not isinstance(name, str) or not name:
        raise DieContractError(f"{where} has no usable name")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise DieContractError(f"terminal {name!r}: pad_index must be a non-negative integer")
    return (name, index)


def check_terminals(bound: "BoundContract", *, require_size: bool = False,
                    require_opening: bool = False, require_equality: bool = False,
                    require_unique_names: bool = False,
                    require_port_identity: bool = False) -> None:
    """Refuse a terminal set the bound inventory does not support.

    The keyword switches are the strict-profile knobs, off by default so a
    consumer that only wants the identity check gets exactly that. Size is
    checked in the INVENTORY, never in the contract: pins.json already carries
    ``width_dbu`` and ``height_dbu``, and a second copy of the same number here
    would be a divergence with no detector.

    ``require_port_identity`` is the knob for a consumer that resolves by port:
    it refuses a terminal with no ``(port, ordinal)``, a pair that repeats, a
    port whose ordinals are not ``0..n-1``, and a signal port that covers more
    than one terminal. The port identity stays OPTIONAL in the document, so a
    contract that carries none is refused here rather than at load time; a
    consumer that resolves by pad name never turns this on.

    For a newer minor, a terminal whose role or direction this reader does not
    know is refused as an element but keeps its identity: it still counts for
    duplicates, inventory membership, repeated names and set equality, because
    dropping it would change what each of those checks means. Every refusal is
    raised first; only a terminal set with none left over and some element
    unread raises :class:`DieContractIncomplete`. The same holds for a binding
    admission left pending: with an inventory kind this reader does not know,
    the checks that need no inventory (identity shape, duplicates, repeated
    names, the opening) still run and still refuse.
    """
    if bound.inventory is None and not bound._pending:
        raise DieContractError("the bound contract carries no inventory to check against")
    identities = bound.identities() if bound.inventory is not None else None
    if identities is not None:
        _check_geometry(identities, bound.kind)
    newer = reads_element_level(bound.contract)
    seen: Dict[Tuple[str, int], Dict[str, Any]] = {}
    names: Dict[str, int] = {}
    unread: List[Unrecognised] = []
    for i, term in enumerate(bound.terminals):
        if not isinstance(term, dict):
            raise DieContractError(f"terminals[{i}] must be a mapping")
        ident = _terminal_identity(term, f"terminals[{i}]")
        role_known = term.get("role") in TERMINAL_ROLES
        if not role_known:
            if not (newer and _word(term.get("role"))):
                raise DieContractError(
                    f"terminal {ident[0]!r} index {ident[1]}: role {term.get('role')!r} is "
                    f"not one of {', '.join(TERMINAL_ROLES)}"
                )
            unread.append(Unrecognised(f"terminals/{i}/role", "value", term["role"],
                                       TERMINAL_REFUSED))
        direction = term.get("direction")
        if newer and direction is not None and direction not in TERMINAL_DIRECTIONS:
            if not _word(direction):
                raise DieContractError(
                    f"terminal {ident[0]!r} index {ident[1]}: direction {direction!r} is not "
                    f"one of {', '.join(TERMINAL_DIRECTIONS)}"
                )
            unread.append(Unrecognised(f"terminals/{i}/direction", "value", direction,
                                       TERMINAL_REFUSED))
        if ident in seen:
            raise DieContractError(
                f"terminal {ident[0]!r} index {ident[1]} is declared twice"
            )
        seen[ident] = (i, term)
        names[ident[0]] = names.get(ident[0], 0) + 1
        pad = identities.get(ident) if identities is not None else None
        if identities is not None and pad is None:
            raise DieContractError(
                f"terminal {ident[0]!r} index {ident[1]} is not in the bound inventory "
                f"({bound.inventory_path}); the contract describes a terminal the die "
                "does not have"
            )
        if require_size and pad is not None:
            _check_size(pad, ident, bound.kind)
        # The opening rule does not depend on role, so a terminal whose role is
        # unread is held to it all the same.
        if require_opening:
            _check_opening(term.get("opening"), ident,
                           unread=unread if newer else None, pointer=f"terminals/{i}/opening")

    if require_port_identity:
        # Its refusals are raised here, with the other refusals; what it could
        # not read joins the incompleteness below, behind every one of them.
        unread.extend(_check_port_identity(seen))

    if require_unique_names:
        repeated = sorted(n for n, count in names.items() if count > 1)
        if repeated:
            raise DieContractError(
                f"terminals repeat the names {', '.join(repeated)}, and the consumer that "
                "places bumps selects same-name pads by occurrence order rather than by any "
                "index the assembly carries, so a repeated physical name cannot be resolved "
                "without choosing a pad in silence; give the pads distinct names for this "
                "profile"
            )

    if require_equality and identities is not None:
        undeclared = sorted(set(identities) - set(seen))
        if undeclared:
            shown = ", ".join(f"{n!r}[{i}]" for n, i in undeclared[:5])
            raise DieContractError(
                f"the bound inventory has {len(undeclared)} pad(s) no terminal declares "
                f"({shown}); a pad whose purpose nobody states is not a die this profile "
                "can admit, and leaving it out is how a supply becomes invisible"
            )

    if unread or bound._pending:
        reasons = [bound._pending_reason] if bound._pending else []
        if unread:
            shown = ", ".join(f"{u.pointer}={u.value!r}" for u in unread[:5])
            reasons.append(f"{len(unread)} terminal element(s) carry values this reader "
                           f"does not know ({shown})")
        raise DieContractIncomplete(
            "; ".join(reasons) + "; nothing here is refused, and a die is not admitted on "
            "the part of it that could be read", tuple(bound._pending) + tuple(unread))


def _check_port_identity(
        seen: Dict[Tuple[str, int], Tuple[int, Dict[str, Any]]]) -> List[Unrecognised]:
    """Refuse a port identity a consumer could not resolve by, over the whole set.

    :func:`resolve_port` asks these questions one lane at a time, where a caller
    meets them one refusal at a time; this profile asks them of the die before
    anything is resolved, which is where a producer wants to hear it.

    Returns what it could not read rather than raising it, so the caller keeps
    incompleteness behind every refusal it has left to make.
    """
    lanes: Dict[str, Dict[int, Dict[str, Any]]] = {}
    unreadable: List[Tuple[int, Dict[str, Any]]] = []
    for ident, (index, term) in seen.items():
        identity = _port_identity(term)
        if identity is UNREADABLE_PORT:
            unreadable.append((index, term))
            continue
        if identity is None:
            raise DieContractError(
                f"terminal {ident[0]!r} index {ident[1]} carries no port identity, and this "
                "profile resolves by port: intent names a port, and pad_index renumbers "
                "whenever a pad moves")
        port, ordinal = identity
        if ordinal in lanes.setdefault(port, {}):
            raise DieContractError(
                f"the port {port!r} declares the ordinal {ordinal} twice; a lane that names "
                "two pads is resolved by array order or not at all")
        lanes[port][ordinal] = term
    for port, rows in sorted(lanes.items()):
        ordinals = sorted(rows)
        # A terminal whose identity this reader cannot read could be a lane of
        # this port, and one that does not even name a port could be a lane of
        # any of them. Each such terminal can fill at most one hole, so the shape
        # refusal is certain only while the holes outnumber them; below that the
        # refusal would rest on the element that was not read.
        maybe = sum(1 for _, t in unreadable
                    if not _word(t.get("port")) or t.get("port") == port)
        holes = [o for o in range(ordinals[-1] + 1) if o not in rows]
        if len(holes) > maybe:
            raise DieContractError(
                f"the port {port!r} declares the ordinals {ordinals}, which are not 0..n-1")
        # This one is certain whatever the unread elements turn out to be, so it
        # is not deferred for any of them: both facts it rests on are readable,
        # how many lanes the port already declares and that one of them IS the
        # role signal, and a lane this reader could not read can only add to the
        # count. A lane with an unread ROLE is still a lane of this port, and it
        # is carried into the incompleteness check_terminals raises after every
        # refusal.
        if len(ordinals) > 1 and "signal" in {rows[o].get("role") for o in ordinals}:
            raise DieContractError(
                f"the port {port!r} is a signal port on {len(ordinals)} terminals; a signal "
                "that is several physical pads is a bus, and this profile asks for one port "
                "per lane rather than one name that means several nets")
    return [_unreadable_port(index, t) for index, t in unreadable]


def _check_geometry(identities: Dict[Tuple[str, int], Dict[str, Any]], kind: str) -> None:
    """Refuse coordinates that are not numbers and sizes that are not extents."""
    coords = ("center_x_dbu", "center_y_dbu") if kind == "pins_json" else ("x_um", "y_um")
    sizes = ("width_dbu", "height_dbu") if kind == "pins_json" else ("w_um", "h_um")
    for (name, index), pad in identities.items():
        for key in coords:
            if not _finite(pad.get(key)):
                raise DieContractError(
                    f"pad {name!r} index {index}: {key} is {pad.get(key)!r}, not a finite number"
                )
        for key in sizes:
            value = pad.get(key)
            if value is None:
                continue
            if not _finite(value) or value <= 0:
                raise DieContractError(
                    f"pad {name!r} index {index}: {key} is {value!r}; a terminal with a "
                    "non-positive extent is not a terminal"
                )


def _check_size(pad: Dict[str, Any], ident: Tuple[str, int], kind: str) -> None:
    keys = ("width_dbu", "height_dbu") if kind == "pins_json" else ("w_um", "h_um")
    if any(pad.get(k) is None for k in keys):
        raise DieContractError(
            f"terminal {ident[0]!r} index {ident[1]}: the bound inventory records a center "
            "but no size, and the selected attachment needs the opening dimensions; fix the "
            "inventory rather than restating the size here"
        )


def _check_opening(opening: Any, ident: Tuple[str, int], *,
                   unread: Optional[List[Unrecognised]] = None, pointer: str = "") -> None:
    """Refuse an opening strict admission cannot draw.

    ``unread`` is given only for a newer minor. A purpose or shape this reader
    does not know then refuses the opening as an element and is recorded there,
    while everything else about the opening is still checked and still refuses.
    """
    where = f"terminal {ident[0]!r} index {ident[1]}"
    if not isinstance(opening, dict):
        raise DieContractError(
            f"{where}: strict admission needs the opening layer and shape for the "
            "selected attachment"
        )
    purpose = opening.get("purpose")
    if purpose not in OPENING_PURPOSES and unread is not None and _word(purpose):
        unread.append(Unrecognised(f"{pointer}/purpose", "value", purpose, OPENING_REFUSED))
    elif purpose not in OPENING_PURPOSES:
        raise DieContractError(
            f"{where}: opening.purpose must be conductor or aperture; the same rectangle "
            "means opposite things under the two and the file has to say which"
        )
    for key in ("gds_layer", "gds_datatype"):
        value = opening.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DieContractError(
                f"{where}: opening.{key} must be a non-negative integer; a layer NAME "
                "resolves only against a mapping the consumer happens to hold"
            )
    shape = opening.get("shape")
    if shape not in OPENING_SHAPES and unread is not None and _word(shape):
        unread.append(Unrecognised(f"{pointer}/shape", "value", shape, OPENING_REFUSED))
    elif shape not in OPENING_SHAPES:
        # Truthiness was the test here, so "hexagon", 1 and {} all passed. The
        # schema names three shapes and the consumer can only draw those three,
        # so a fourth is not a shape this profile knows, it is a shape nobody
        # will draw.
        raise DieContractError(
            f"{where}: opening.shape is {opening.get('shape')!r}; this profile knows "
            + ", ".join(OPENING_SHAPES)
        )


def attestation_state(entry: Dict[str, Any], *, base_dir,
                      layout_sha256: Optional[str] = None,
                      evaluator: Optional[Callable[[Path, Dict[str, Any]], Any]] = None,
                      version: Optional[str] = None) -> str:
    """Resolve one attestation entry to ``pass``, ``fail`` or ``unresolved``.

    The recorded ``result`` is never returned on trust. An entry earns its
    recorded value only if it names a completed execution, binds the layout it
    ran over to the one under discussion, names the deck applied, and its
    evidence resolves and hashes to what it records, AND a qualified evaluator
    for that tool reads the evidence and says the same thing.

    Without an ``evaluator`` the answer is ``unresolved`` however well bound the
    entry is, because everything checked up to that point is the entry talking
    about itself. There is deliberately no generic report parser here: one that
    guessed at every tool's output would be a fourth thing to trust, and the
    honest default is to say we cannot tell rather than to invent a reader.

    ``unresolved`` stays distinct from ``fail`` throughout, because "the check
    said no" and "we cannot tell what the check said" are different facts that a
    two-value answer collapses into whatever the record already claimed.

    ``version`` is the version of the contract the entry came from. An entry
    carrying a key or a check this reader does not know is ``unresolved`` at
    any version, before an evaluator sees it; a ``result`` this reader does not
    know is ``unresolved`` only when ``version`` declares a newer minor, and
    refused otherwise.
    """
    if not isinstance(entry, dict):
        raise DieContractError("attestation entry must be a mapping")
    recorded = entry.get("result")
    if recorded not in ("pass", "fail", "unresolved"):
        # A result a newer minor added (a not_applicable, say) is neither a pass
        # nor a fail. Only the contract's version can say it is one; at the floor
        # the same word is a malformed record, as it always was.
        newer = version is not None and reads_element_level({"version": version})
        if not (newer and _word(recorded)):
            raise DieContractError(
                f"attestation result {recorded!r} is not one of pass, fail, unresolved"
            )
        return "unresolved"
    if recorded == "unresolved":
        return "unresolved"
    if entry.get("execution") != "completed":
        return "unresolved"
    if not entry.get("deck") or not entry.get("deck_sha256"):
        return "unresolved"
    ran_over = entry.get("layout_sha256")
    if not isinstance(ran_over, str) or len(ran_over) != 64:
        return "unresolved"
    if layout_sha256 is not None and ran_over != layout_sha256:
        return "unresolved"
    evidence = entry.get("evidence")
    expected = entry.get("evidence_sha256")
    if not isinstance(evidence, str) or not evidence:
        return "unresolved"
    # Deliberately not downgraded to unresolved: an absolute or escaping evidence
    # path is a malformed contract, not an unavailable one, and the two should not
    # look alike.
    path = _portable(Path(base_dir), evidence, "attestation evidence")
    item = _node(die_contract_schema(), _ATTESTATION)
    if (any(key not in item["properties"] for key in entry)
            or entry.get("check") not in item["properties"]["check"]["enum"]):
        # An entry is a claim, read whole or not at all, so a key or check this
        # reader does not know leaves it unresolved before any evaluator sees it.
        # After the evidence path, so a malformed path is still refused first.
        return "unresolved"
    if not path.is_file():
        return "unresolved"
    if not isinstance(expected, str) or digest_file(path) != expected:
        return "unresolved"
    if evaluator is None:
        return "unresolved"
    verdict = evaluator(path, dict(entry))
    if verdict not in ("pass", "fail"):
        return "unresolved"
    if verdict != recorded:
        # The record and the evidence disagree. That is neither of their claims,
        # and picking one silently is how a producer defect becomes a green gate,
        # so it resolves to the outcome that names not knowing.
        return "unresolved"
    return recorded


def admit_component(component: Dict[str, Any], *, base_dir,
                    require_size: bool = True, require_opening: bool = True,
                    require_equality: bool = True, require_unique_names: bool = True,
                    on_warn=None) -> "BoundContract":
    """Strict chiplet-full / demo admission for one assembly component.

    Refuses a component with no ``die_contract`` reference, a reference whose
    digest does not match the sidecar on disk, and everything
    :func:`check_terminals` refuses under the strict switches. Format-level
    reading is untouched: a reader that never calls this still loads a legacy
    ``.chiplet`` exactly as before, which is the whole reason the reference is
    additive and carries no selector.
    """
    ref = component.get("die_contract")
    if not isinstance(ref, dict):
        raise DieContractError(
            f"component {component.get('id')!r}: strict admission requires a die_contract "
            "reference, and this component declares none"
        )
    root = Path(base_dir)
    path = _portable(root, ref.get("path"),
                     f"component {component.get('id')!r} die_contract")
    _check_digest(path, ref.get("sha256"),
                  f"component {component.get('id')!r} die_contract")
    # The reference is resolved against the assembly, because that is where it is
    # written. Everything the contract itself names is resolved against the
    # contract, because that is where THOSE are written. Passing the assembly
    # root down here made a sidecar in a nested bundle read its own layout and
    # inventory from the wrong directory, and a bundle that moved as a unit would
    # have failed or, worse, bound a same-named neighbour.
    # Bound with an unknown kind or frame left pending, so the artwork and every
    # terminal refusal still get to refuse; check_terminals raises it last.
    bound = _bind(path, None, True, on_warn)
    _bind_component_artwork(component, bound, root)
    check_terminals(bound, require_size=require_size, require_opening=require_opening,
                    require_equality=require_equality,
                    require_unique_names=require_unique_names)
    return bound


def _bind_component_artwork(component: Dict[str, Any], bound: "BoundContract",
                            root: Path) -> None:
    """Tie the contract to the artwork the ASSEMBLY places, not just to its own.

    Everything else here checks the contract against itself and against the
    files it names. That leaves the one gap that matters at assembly time: a
    valid contract for a different die, placed beside this component, satisfies
    every one of those checks. The digest and the top cell are what make the
    contract be about THIS component's layout, so strict admission requires both
    and compares them rather than reading them.
    """
    ident = f"component {component.get('id')!r}"
    die = bound.contract.get("die") or {}

    layout = component.get("layout")
    if not isinstance(layout, str) or not layout:
        raise DieContractError(
            f"{ident}: strict admission requires the component's own layout, since a "
            "contract with nothing to compare against is a statement about some other die"
        )
    actual = digest_file(_portable(root, layout, f"{ident} layout"))
    if actual != die.get("layout_sha256"):
        raise DieContractError(
            f"{ident}: the placed layout hashes to {actual} and the die contract binds "
            f"{die.get('layout_sha256')}; this contract describes different artwork"
        )

    top = component.get("top_cell")
    if not isinstance(top, str) or not top:
        raise DieContractError(
            f"{ident}: strict admission requires top_cell, because one GDS can hold many "
            "cells and the contract speaks for exactly one of them"
        )
    if top != die.get("top_cell"):
        raise DieContractError(
            f"{ident}: places top cell {top!r} and the die contract binds "
            f"{die.get('top_cell')!r}"
        )


def resolve_endpoint(bound: "BoundContract", pin: Any) -> Dict[str, Any]:
    """Resolve one assembly netlist endpoint against a bound die contract.

    The assembly carries a pad NAME and no selector, and it stays that way: the
    consumer that places the bump hands out same-name pads by occurrence order,
    so a selector the assembly carried would be ignored and a different physical
    pad chosen in silence. A unique name therefore binds canonically, and a
    repeated one is refused rather than resolved. That refusal is the honest
    answer until an index-aware consumer exists, and it is why this profile asks
    the producer for distinct physical names instead.
    """
    if not isinstance(pin, str) or not pin:
        raise DieContractError("netlist endpoint has no pin name")
    matches = [(i, t) for i, t in enumerate(bound.terminals)
               if isinstance(t, dict) and t.get("name") == pin]
    if not matches:
        raise DieContractError(
            f"netlist claims pin {pin!r}, which the die contract does not declare"
        )
    if len(matches) > 1:
        raise DieContractError(
            f"netlist claims pin {pin!r} and the die has {len(matches)} pads with that "
            "name; the assembly carries no selector and the bump consumer would pick by "
            "occurrence order, so which physical pad is meant is undecidable"
        )
    index, term = matches[0]
    if reads_element_level(bound.contract):
        unread = _unread_terminal(term, f"terminals/{index}")
        if unread:
            raise DieContractIncomplete(
                f"netlist claims pin {pin!r}, whose terminal carries "
                f"{unread[0].pointer[len(f'terminals/{index}/'):]} {unread[0].value!r}, which "
                "this reader "
                "does not know; what the pin is cannot be read", unread)
    return term


def _unread_terminal(term: Dict[str, Any], prefix: str) -> List[Unrecognised]:
    """Every value on one terminal this reader cannot read, with its disposition.

    Shared by both resolvers rather than written twice: the three-outcome answer
    (resolved, refused, cannot tell) has to be the same on every surface that
    hands a terminal to a consumer, and a copy of this rule would drift.
    """
    unread = [Unrecognised(f"{prefix}/{key}", "value", term.get(key), TERMINAL_REFUSED)
              for key, known in (("role", TERMINAL_ROLES), ("direction", TERMINAL_DIRECTIONS))
              if key in term and term.get(key) not in known]
    opening = term.get("opening")
    if isinstance(opening, dict):
        unread += [Unrecognised(f"{prefix}/opening/{key}", "value", opening.get(key),
                                OPENING_REFUSED)
                   for key, known in (("purpose", OPENING_PURPOSES), ("shape", OPENING_SHAPES))
                   if key in opening and opening.get(key) not in known]
    return unread


#: A terminal carries half a port identity, or one this reader cannot read.
#: Deliberately not the same answer as carrying none: this minor pairs the two
#: keys in the schema, so a terminal like this can only come from a minor that
#: relaxed the pairing, and what its identity means is exactly what is unread.
#: Dropping it silently would turn a two-lane bus into a one-lane bus.
UNREADABLE_PORT = object()


def _port_identity(term: Any):
    """The (port, ordinal) pair of one terminal, None, or :data:`UNREADABLE_PORT`."""
    if not isinstance(term, dict):
        return None
    port, ordinal = term.get("port"), term.get("ordinal")
    if port is None and ordinal is None:
        return None
    if not _word(port) or type(ordinal) is not int or ordinal < 0:
        return UNREADABLE_PORT
    return (port, ordinal)


def _unreadable_port(index: int, term: Dict[str, Any]) -> Unrecognised:
    """The finding for the half of a port identity this reader cannot read.

    Located by INDEX like every other finding: a pointer says where in the
    document the element is, and ``terminals`` is a list, so the name is not a
    locator at all on a die that repeats one. Two pointer spaces in one channel
    would be worse than either, since a consumer cannot tell them apart.
    """
    key = "port" if not _word(term.get("port")) else "ordinal"
    return Unrecognised(f"terminals/{index}/{key}", "value", term.get(key),
                        PORT_UNREADABLE)


def _unreadable_ports(data: Dict[str, Any]) -> List[Unrecognised]:
    """Every half port identity in a document, reported like any other finding.

    The schema no longer names these above the floor (``RELAXABLE``), so nothing
    would reach ``on_warn`` or ``BoundContract.unrecognised`` for them, and a
    consumer that never asks about ports would be told nothing at all about a
    document it cannot fully read.
    """
    terminals = data.get("terminals")
    return [_unreadable_port(index, term)
            for index, term in enumerate(terminals if isinstance(terminals, list) else [])
            if _port_identity(term) is UNREADABLE_PORT]


def no_port_identity(version: Any) -> str:
    """The sentence for a contract no port can be resolved through.

    :func:`port_terminals` and pad numbering share it, so the two cannot drift.
    """
    return (f"this die contract declares {version!r} and carries no port identity, so a "
            "port cannot be resolved through it; resolve by pad name instead")


def port_terminals(bound: "BoundContract", port: str) -> Tuple[Dict[str, Any], ...]:
    """Every terminal of one port, in ordinal order, with the bus shape checked.

    A bus is one port with contiguous ordinals from 0, and that is checked here,
    once, where the bus is read: ordinals that repeat, skip a value or start
    above zero describe a bus with a hole in it, and a consumer asking for lane
    k of such a port has no defensible answer.
    """
    if not _word(port):
        raise DieContractError("a port name is required to resolve a terminal")
    declared: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
    unreadable: List[Tuple[int, Dict[str, Any]]] = []
    for index, term in enumerate(bound.terminals):
        identity = _port_identity(term)
        if identity is UNREADABLE_PORT:
            unreadable.append((index, term))
        elif identity is not None:
            declared.setdefault(identity[0], []).append((identity[1], term))
    # A terminal whose identity this reader cannot read may be a lane of the
    # port being asked about, and if it does not even name a port it may be a
    # lane of any of them. Each can fill at most one hole in the bus, which is
    # what decides below whether a shape refusal is certain or undecided.
    blind = [(i, t) for i, t in unreadable
             if not _word(t.get("port")) or t.get("port") == port]
    if not declared and not unreadable:
        raise DieContractError(no_port_identity(bound.contract.get("version")))
    rows = sorted(declared.get(port, []), key=lambda pair: pair[0])
    ordinals = [ordinal for ordinal, _ in rows]
    holes = [o for o in range(ordinals[-1] + 1) if o not in set(ordinals)] if rows else []
    if rows and (len(set(ordinals)) != len(ordinals) or len(holes) > len(blind)):
        raise DieContractError(
            f"the port {port!r} declares the ordinals {ordinals}, which are not 0..n-1; a "
            "port whose lanes repeat, skip a value or do not start at zero cannot be "
            "resolved by lane")
    if blind:
        raise DieContractIncomplete(
            f"the port {port!r} has a terminal whose port identity this reader cannot read "
            f"({_unreadable_port(*blind[0]).pointer}); how many lanes the port has cannot be "
            "read, so no lane of it can be resolved",
            [_unreadable_port(i, t) for i, t in blind])
    if port not in declared:
        readable = ", ".join(sorted(declared)) or "no port this reader can read"
        unread_note = (f", and {len(unreadable)} terminal(s) carry a port identity it could "
                       "not read" if unreadable else "")
        raise DieContractError(
            f"the die contract does not declare the port {port!r}; it declares "
            + readable + unread_note)
    return tuple(term for _, term in rows)


def resolve_port(bound: "BoundContract", port: str, ordinal: int, *,
                 require_role: Optional[str] = None) -> Dict[str, Any]:
    """Resolve one lane of one authored port against a bound die contract.

    The external identity is (port, ordinal) and the physical identity is
    (name, pad_index); this is the join between them, and it is the reason the
    contract carries the port at all. ``pad_index`` is not a substitute for the
    ordinal: moving a single pad renumbers every later one, so intent that named
    a pad number would silently name a different pad after any pad-ring edit.

    ``ordinal`` is required, and asking for the whole bus is
    :func:`port_terminals`, a different question with its own name. An optional
    ordinal would fold "the only lane of this port" and "lane k" into one call
    whose meaning depends on the document it is asked about.

    ``require_role`` is the strict-profile knob, off by default: the policy
    belongs to the call site, the refusal text belongs here once.
    """
    if type(ordinal) is not int or ordinal < 0:
        raise DieContractError(f"ordinal {ordinal!r} must be a non-negative integer")
    if require_role is not None and require_role not in TERMINAL_ROLES:
        raise DieContractError(
            f"require_role {require_role!r} is not one of {', '.join(TERMINAL_ROLES)}")
    lanes = port_terminals(bound, port)
    if ordinal >= len(lanes):
        raise DieContractError(
            f"the port {port!r} has {len(lanes)} lane(s), ordinals 0..{len(lanes) - 1}, and "
            f"the caller asked for {ordinal}")
    term = lanes[ordinal]
    name = term.get("name")
    repeated = sum(1 for t in bound.terminals
                   if isinstance(t, dict) and t.get("name") == name)
    if repeated > 1:  # readable on its face, so it is raised before anything unread
        # What leaves here is a bare pad name the assembly carries with no
        # selector, and the bump consumer hands out same-name pads by occurrence
        # order. Resolving would name a pad and choose a different one.
        raise DieContractError(
            f"the port {port!r} lane {ordinal} is the pad {name!r} and the die has "
            f"{repeated} pads with that name; the assembly carries no selector and the bump "
            "consumer would pick by occurrence order, so which physical pad is meant is "
            "undecidable")
    # The document index, not the pad name: a pointer locates the element, and
    # the shape check above leaves exactly one terminal per (port, ordinal).
    index = next(i for i, t in enumerate(bound.terminals)
                 if _port_identity(t) == (port, ordinal))
    unread = (_unread_terminal(term, f"terminals/{index}")
              if reads_element_level(bound.contract) else [])
    # The role requirement is tested against the ROLE, so it waits only for that
    # one value. Answering "this lane is not a signal" about a role whose meaning
    # is unknown would turn "cannot tell" into "the contract said no", and a
    # later minor's role may well be the one the caller wanted; waiting behind an
    # unread DIRECTION would be the same mistake pointing the other way, hiding a
    # refusal that every reading of that direction leaves standing.
    role_unread = any(finding.pointer.endswith("/role") for finding in unread)
    if require_role is not None and not role_unread and term.get("role") != require_role:
        raise DieContractError(
            f"the port {port!r} lane {ordinal} is role {term.get('role')!r}, and this caller "
            f"requires {require_role!r}")
    if unread:
        raise DieContractIncomplete(
            f"the port {port!r} lane {ordinal} carries "
            f"{unread[0].pointer[len(f'terminals/{index}/'):]} {unread[0].value!r}, which "
            "this reader does not know; what the lane is cannot be read", unread)
    return term
