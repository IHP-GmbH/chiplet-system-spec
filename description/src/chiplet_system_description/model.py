# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The System object model: frozen value dataclasses.

The model is an immutable value. Every class is ``@dataclass(frozen=True)`` and
holds collections as tuples so instances stay hashable and serializable. This
follows the ``interposer_pnr.ir`` idiom (stdlib dataclasses, explicit checks,
typed exceptions, no framework) with one addition: the system is frozen, so
mutation happens only in :class:`chiplet_system_description.builder.SystemBuilder`,
never in the model.

Every invariant is enforced in ``__post_init__`` and raises a typed error from
:mod:`chiplet_system_description.errors`. The resolution and interface-compatibility checks
live here as module functions so :class:`SystemBuilder` and ``System`` call the
same code (each check is written once).

The coordinate frame is NOT paraphrased here; the single source of truth is
the spec's docs/coord_frame_contract.md section 2.1 (``anchor`` / ``position``
semantics). This model emits ``anchor: bbox_center`` for dies, the anchor whose
reference point is exactly the ``position`` it already writes (the die geometric
centre), so downstream never has to re-centre a die whose numbers say one thing
and whose ``anchor`` says another.
``format_version`` is never a literal in this package; it is emitted
from the vendored loader constant by :mod:`chiplet_system_description.elaborate`.
"""
from __future__ import annotations

import enum
import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .errors import (
    AmbiguousLane,
    DieProcessIncompatible,
    DuplicateId,
    DuplicateLane,
    DuplicateNet,
    InterfaceContractMismatch,
    InterfaceTypeMismatch,
    InterfaceVersionMismatch,
    LaneCountMismatch,
    MissingBumpGeometry,
    PinAlreadyBound,
    PitchClassIncompatible,
    PortAlreadyBound,
    RoleConflict,
    SystemSpecError,
    UnknownInstance,
    UnknownMethod,
    UnknownPin,
    UnknownPort,
    UnsupportedAnchor,
    UnsupportedUnits,
)

_CANONICAL_ORIENTATIONS = ("face_up", "flip_chip")


# ----------------------------------------------------------------------
# Value primitives
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Position:
    """Geometric centre, micrometres, frame per the module docstring."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass(frozen=True)
class Dimensions:
    """Bounding box, micrometres."""

    width: float
    height: float
    thickness: float = 0.0


@dataclass(frozen=True)
class Technology:
    """A named process; the key components reference by id.

    ``layer_properties`` (a KLayout ``.lyp``) and ``stackup`` (a layer-stackup
    YAML) are the format's optional technology paths. They are written exactly
    as given, never resolved or normalised, and only when set. Each must be
    anchored, absolute or starting with ``${VAR}`` (the KiCad export's rule): a
    reader takes a relative path against the ``.chiplet``'s own directory, and
    a System's ``.chiplet`` is emitted into a run directory, never beside the
    file that authored it.
    """

    id: str
    description: str = ""
    dbu: float = 0.001
    layer_properties: Optional[str] = None
    stackup: Optional[str] = None

    def __post_init__(self) -> None:
        # An empty id would resolve to nothing
        # and collide silently; refuse it at construction.
        if not self.id:
            raise SystemSpecError("Technology id must be non-empty")
        for name in ("layer_properties", "stackup"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, str) or not value:
                raise SystemSpecError(
                    f"Technology {self.id}: {name} must be a non-empty path string or "
                    f"None, got {value!r}")
            if not (os.path.isabs(value) or value.startswith("${")):
                raise SystemSpecError(
                    f"Technology {self.id}: {name} {value!r} is a relative path, which "
                    f"cannot be anchored to the emitted .chiplet")


#: The two pinned entries, verbatim to the golden fixture. Data constants, not a
#: registry mechanism.
INTM4TM2 = Technology("intm4tm2", "IHP 130nm IntM4TM2 aluminium BEOL interposer", 0.001)
SG13G2 = Technology("sg13g2", "IHP SG13G2 130nm BiCMOS die", 0.001)


# ----------------------------------------------------------------------
# Die pins
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class DiePin:
    """One record of the pins.json contract, converted to micrometres.

    ``name`` names the pin, the geometry check reads ``x_um``/``y_um`` of a
    bound lane and lane selection reads ``pad_index``; the rest is provenance the
    model carries but never re-emits (the pin list travels beside the .chiplet).
    """

    name: str
    pad_index: "int | None" = None
    type: str = ""
    side: str = ""
    x_um: "float | None" = None
    y_um: "float | None" = None
    width_um: "float | None" = None
    height_um: "float | None" = None


# ----------------------------------------------------------------------
# Interface typing
# ----------------------------------------------------------------------
class Role(enum.Enum):
    """Topwrap vocabulary mapped onto our lane/beachfront roles.

    A link binds exactly one MANAGER to one SUBORDINATE. Per-lane TX/RX signal
    maps are out of scope and must not be added here.
    """

    MANAGER = "manager"
    SUBORDINATE = "subordinate"


@dataclass(frozen=True)
class InterfaceDefinition:
    """Interface-type-as-data: a named, versioned descriptor of the D2D physical
    contract.

    Identity is ``(name, version)`` with exact string equality; a
    version difference is a physical difference. ``max_pitch_um`` is the coarsest
    bump pitch the interface's ballout is specified for; when set it is checked
    at build() against the die's :class:`InterconnectMethod` (a method that
    cannot go fine enough is off-spec). "Width" in the D2D sense is
    ``lane_count``; there is no separate width field, and no ``min_pitch_um``
    (no decidable declaration-level consumer for it). Pinned UCIe/BoW/AIB
    catalog entries and per-lane signal maps stay out.

    The plain form: ``InterfaceDefinition("generic-parallel", lane_count=6)``
    constructs unchanged (``version`` defaults, no envelope, no method required).
    """

    name: str
    lane_count: int
    version: str = "1.0"
    max_pitch_um: "float | None" = None

    def __post_init__(self) -> None:
        if self.lane_count <= 0:
            raise SystemSpecError(
                f"InterfaceDefinition {self.name!r}: lane_count must be > 0, "
                f"got {self.lane_count}"
            )
        if not self.version:
            raise SystemSpecError(
                f"InterfaceDefinition {self.name!r}: version must be non-empty"
            )
        if self.max_pitch_um is not None and self.max_pitch_um <= 0:
            raise SystemSpecError(
                f"InterfaceDefinition {self.name!r}: max_pitch_um must be > 0 "
                f"when set, got {self.max_pitch_um}"
            )


@dataclass(frozen=True)
class Port:
    """A die's typed lane group. ``lanes`` are ordered die pin names."""

    name: str
    definition: InterfaceDefinition
    role: Role
    lanes: Tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.lanes) != self.definition.lane_count:
            raise LaneCountMismatch(
                f"port {self.name!r}: {len(self.lanes)} lane(s) but interface "
                f"{self.definition.name!r} declares lane_count="
                f"{self.definition.lane_count}"
            )
        # A repeated lane would elaborate to two nets on one pad (a short)
        # while another lane goes unconnected. Refuse it here.
        if len(set(self.lanes)) != len(self.lanes):
            dupes = sorted({lane for lane in self.lanes if self.lanes.count(lane) > 1})
            raise DuplicateLane(
                f"port {self.name!r}: lane(s) {dupes} appear more than once; each "
                "lane must name a distinct pin (a repeat elaborates to a short)"
            )


# ----------------------------------------------------------------------
# Interconnect methods
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class InterconnectMethod:
    """A post-fabrication interconnect method (Cu-pillar / solder-bump class).

    ``pitch_um`` is the method's minimum achievable pitch. It pairs with
    :attr:`InterfaceDefinition.max_pitch_um` in the build()-time pitch-class
    check. These are data constants transcribed from the interconnect_pdk
    manifest, not a registry mechanism, exactly like :data:`SG13G2`/:data:`INTM4TM2`;
    they are identity-tested against the manifest strict-live. Only the fields
    a build()-time check consumes are carried; the manifest's other fields
    (spacing, opening, diameter, stack) join this class only with their consumer.

    ``die_processes`` (manifest contract 1.1) names the technology ids this
    method attaches, as the assembly document keys its technologies
    (:attr:`Technology.id`, e.g. ``"sg13g2"``), joined by string equality. Ids,
    not versions. ``None`` is the only spelling of "not stated": a method that
    states nothing accepts no die at build() (:class:`DieProcessIncompatible`),
    and an empty tuple is refused here like an empty id, so absence never reads
    as "any process".
    """

    id: str
    pitch_um: float
    die_processes: Optional[Tuple[str, ...]] = None

    def __post_init__(self) -> None:
        if not self.id:
            raise SystemSpecError("InterconnectMethod id must be non-empty")
        if self.pitch_um <= 0:
            raise SystemSpecError(
                f"InterconnectMethod {self.id!r}: pitch_um must be > 0, "
                f"got {self.pitch_um}"
            )
        if self.die_processes is not None:
            if not self.die_processes:
                raise SystemSpecError(
                    f"InterconnectMethod {self.id!r}: die_processes is empty; an "
                    "empty statement is not a statement, leave it None to state "
                    "no die process"
                )
            if any(not p for p in self.die_processes) or (
                len(set(self.die_processes)) != len(self.die_processes)
            ):
                raise SystemSpecError(
                    f"InterconnectMethod {self.id!r}: die_processes must be "
                    f"non-empty, distinct technology ids, got {self.die_processes!r}"
                )


#: Pinned constants, transcribed verbatim from the interconnect_pdk manifest's
#: ``default_connection_library`` entries (``pitch_um`` = ``pitch_rules.IXN_pitch``,
#: ``die_processes`` = ``die_processes``).
#: The manifest's ``vendorx_microbump`` entry is demo data and is not shipped.
CUPILLAR_OPT1 = InterconnectMethod("cupillar_opt1", pitch_um=75.0, die_processes=("sg13g2",))
CUPILLAR_OPT2 = InterconnectMethod("cupillar_opt2", pitch_um=80.0, die_processes=("sg13g2",))
CUPILLAR_OPT3 = InterconnectMethod("cupillar_opt3", pitch_um=95.0, die_processes=("sg13g2",))
SBUMP_SAC305 = InterconnectMethod("sbump_sac305", pitch_um=130.0, die_processes=("sg13g2",))


# ----------------------------------------------------------------------
# Dies and instances
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Die:
    """A ready / black-box die blueprint."""

    name: str
    technology: str
    top_cell: str
    dimensions: Dimensions
    pins: Tuple[DiePin, ...]
    ports: Tuple[Port, ...] = ()
    orientation: str = "flip_chip"
    connection: str = "cupillar_opt1"
    anchor: str = "bbox_center"
    pins_source: str = ""

    def __post_init__(self) -> None:
        if self.anchor != "bbox_center":
            raise UnsupportedAnchor(
                f"die {self.name!r}: anchor {self.anchor!r} is unsupported; this "
                "model authors the die geometric centre into 'position', so it "
                "emits 'bbox_center' (the anchor whose reference point IS that "
                "centre; coord_frame_contract.md section 2.1)"
            )
        if self.orientation not in _CANONICAL_ORIENTATIONS:
            raise SystemSpecError(
                f"die {self.name!r}: orientation {self.orientation!r} is not a "
                f"canonical token {_CANONICAL_ORIENTATIONS}"
            )
        # Pin-name multiplicities (AmbiguousLane) and first-occurrence lookup
        # (geometry completeness; a bound lane has multiplicity 1, so the first
        # occurrence is the unique pad).
        counts: Dict[str, int] = {}
        pin_by_name: Dict[str, DiePin] = {}
        for pin in self.pins:
            counts[pin.name] = counts.get(pin.name, 0) + 1
            pin_by_name.setdefault(pin.name, pin)

        seen_ports: set = set()
        claimed_by: Dict[str, str] = {}  # pin name -> port that already bound it
        for port in self.ports:
            if port.name in seen_ports:
                raise DuplicateId(
                    f"die {self.name!r}: duplicate port name {port.name!r}"
                )
            seen_ports.add(port.name)
            for lane in port.lanes:
                n = counts.get(lane, 0)
                if n == 0:
                    raise UnknownPin(
                        f"die {self.name!r} port {port.name!r}: lane {lane!r} "
                        "is not a pin of this die"
                    )
                if n > 1:
                    raise AmbiguousLane(
                        f"die {self.name!r} port {port.name!r}: lane {lane!r} "
                        f"occurs {n} times in the pin list; a port cannot carry "
                        "the ordinal the downstream name+ordinal binding needs"
                    )
                # Cross-port claim: a repeat here is necessarily from a
                # different port (within-port repeats are DuplicateLane at Port).
                prior = claimed_by.get(lane)
                if prior is not None:
                    raise PinAlreadyBound(
                        f"die {self.name!r}: pin {lane!r} is bound by ports "
                        f"{prior!r} and {port.name!r}; one pad, two ports"
                    )
                claimed_by[lane] = port.name
                # Geometry completeness: a bound lane is a bump landing;
                # its pad must carry both centre coordinates (fail-fast mirror of
                # interposer_pnr.pads.load_pin_list). Width/height not required.
                pin = pin_by_name[lane]
                if pin.x_um is None or pin.y_um is None:
                    raise MissingBumpGeometry(
                        f"die {self.name!r} port {port.name!r}: lane {lane!r} "
                        "binds a pin with no x_um/y_um centre; a bump landing "
                        "needs a pad centre"
                    )

    def port(self, name: str) -> Port:
        """The port named ``name``, or raise :class:`UnknownPort`."""
        for p in self.ports:
            if p.name == name:
                return p
        raise UnknownPort(
            f"die {self.name!r} has no port {name!r} (has: "
            f"{[p.name for p in self.ports]})"
        )

    def has_pin(self, name: str) -> bool:
        return any(p.name == name for p in self.pins)


@dataclass(frozen=True)
class DieInstance:
    """Placement intent: explicit interposer-local coordinates only."""

    id: str
    die: Die
    position: Position
    rotation_z: float = 0.0


# ----------------------------------------------------------------------
# Interposer
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class InterposerIoPad:
    """A wire-bond pad on the interposer (the golden's ``io_pads`` records)."""

    id: str
    net: str
    position: Position
    width_um: float
    height_um: float
    io_class: str = "wire_bond"
    layer: str = "TopMetal2"


@dataclass(frozen=True)
class Interposer:
    """The single interposer of a system."""

    technology: str
    top_cell: str
    dimensions: Dimensions
    position: Position
    io_pads: Tuple[InterposerIoPad, ...] = ()
    id: str = "interposer"
    anchor: str = "bbox_center"
    #: The plane dies mount on (``attachment_surface_z``); None writes no plane.
    attachment_surface_z: Optional[float] = None


# ----------------------------------------------------------------------
# Topology
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class PortRef:
    """A string reference to a die instance's port (like ir.NetConnection)."""

    instance: str
    port: str


@dataclass(frozen=True)
class Link:
    """One D2D connection; elaborates to ``lane_count`` two-endpoint nets.

    After validation ``a`` is the MANAGER end and ``b`` the SUBORDINATE end.
    Net names come from :func:`link_net_name`: ``f"{net_prefix}{i}"`` for an
    interface of two or more lanes, and ``net_prefix`` itself for a one-lane
    interface, whose single net has no index to disambiguate. Lane i of ``a``
    pairs with lane i of ``b``, positionally, manager endpoint first.
    """

    a: PortRef
    b: PortRef
    net_prefix: str


@dataclass(frozen=True)
class Tie:
    """One single-die-endpoint net (power / ground / probe export).

    The interposer side of such a net, when present, lives on
    ``InterposerIoPad.net``; the correspondence is deliberately unchecked
    (no engine consumes it yet).
    """

    net: str
    instance: str
    pin: str
    net_class: str = "signal"
    layer: str = ""


@dataclass(frozen=True)
class PinRef:
    """A string reference to one die instance's pad, by pad name."""

    instance: str
    pin: str


#: The classes a :class:`Supply` may carry. A shared net of any other class is
#: not a supply, and a signal shared across more than two pads has no builder
#: surface on purpose.
SUPPLY_NET_CLASSES = ("power", "ground")


@dataclass(frozen=True)
class Supply:
    """One supply net over several pads, on one die or across dies.

    The explicit form of a shared rail: ``endpoints`` lists every pad of the
    net, each keeping its own pad identity, in the order the connections are
    written. A one-pad net is a :class:`Tie`, and two ties still may not share a
    net name; that refusal keeps catching an accidentally repeated name.
    """

    net: str
    endpoints: Tuple[PinRef, ...]
    net_class: str = "power"
    layer: str = ""

    def __post_init__(self) -> None:
        refs = []
        for ref in self.endpoints:
            if not isinstance(ref, PinRef):
                if (not isinstance(ref, (tuple, list)) or len(ref) != 2
                        or not all(isinstance(part, str) for part in ref)):
                    raise SystemSpecError(
                        f"supply {self.net!r}: endpoint {ref!r} is not a PinRef "
                        "or an (instance, pin) pair"
                    )
                ref = PinRef(instance=ref[0], pin=ref[1])
            refs.append(ref)
        object.__setattr__(self, "endpoints", tuple(refs))


def link_net_name(link: "Link", lane_count: int, lane_i: int) -> str:
    """The net name of lane ``lane_i`` of ``link`` (see :class:`Link`)."""
    if lane_count == 1:
        return link.net_prefix
    return f"{link.net_prefix}{lane_i}"


# ----------------------------------------------------------------------
# Shared resolution + compatibility checks (each written once)
# ----------------------------------------------------------------------
def resolve_port(
    instances_by_id: Dict[str, DieInstance], ref: PortRef
) -> Tuple[DieInstance, Port]:
    """Resolve a :class:`PortRef` to its instance and port, or raise."""
    inst = instances_by_id.get(ref.instance)
    if inst is None:
        raise UnknownInstance(
            f"port ref {ref.instance!r}.{ref.port!r}: no die instance "
            f"{ref.instance!r} (have: {sorted(instances_by_id)})"
        )
    return inst, inst.die.port(ref.port)


def check_interface_compatible(
    a_ref: PortRef, a_port: Port, b_ref: PortRef, b_port: Port
) -> None:
    """The checks a link must pass, in order: (1) interface name, (2) version,
    (3) lane count, (4) full structural equality, (5) one MANAGER and one
    SUBORDINATE. Check 4 subsumes 1 to 3, which stay for error specificity.
    Binding (``PortAlreadyBound``) is caller state, checked at the call site.
    """
    a_def, b_def = a_port.definition, b_port.definition
    # check 1: interface identity name
    if a_def.name != b_def.name:
        raise InterfaceTypeMismatch(
            f"{a_ref.instance}.{a_ref.port} is {a_def.name!r} but "
            f"{b_ref.instance}.{b_ref.port} is {b_def.name!r}"
        )
    # check 2: interface identity version (exact string equality, no semver)
    if a_def.version != b_def.version:
        raise InterfaceVersionMismatch(
            f"{a_ref.instance}.{a_ref.port} is {a_def.name!r} v{a_def.version} "
            f"but {b_ref.instance}.{b_ref.port} is v{b_def.version}; a version "
            "difference is a physical difference"
        )
    # check 3: lane count
    if a_def.lane_count != b_def.lane_count:
        raise LaneCountMismatch(
            f"{a_ref.instance}.{a_ref.port} has {a_def.lane_count} lanes but "
            f"{b_ref.instance}.{b_ref.port} has {b_def.lane_count}"
        )
    # check 4: full structural equality (after 1-3, only max_pitch_um can differ)
    if a_def != b_def:
        raise InterfaceContractMismatch(
            f"{a_ref.instance}.{a_ref.port} and {b_ref.instance}.{b_ref.port} "
            f"share interface {a_def.name!r} v{a_def.version} but disagree on the "
            f"physical contract (max_pitch_um {a_def.max_pitch_um} vs "
            f"{b_def.max_pitch_um})"
        )
    # check 5: exactly one MANAGER and one SUBORDINATE
    if {a_port.role, b_port.role} != {Role.MANAGER, Role.SUBORDINATE}:
        raise RoleConflict(
            f"a link needs one MANAGER and one SUBORDINATE, got "
            f"{a_ref.instance}.{a_ref.port}={a_port.role.name} and "
            f"{b_ref.instance}.{b_ref.port}={b_port.role.name}"
        )


# ----------------------------------------------------------------------
# System (the frozen root)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class System:
    """The frozen root spec. Normally produced by ``SystemBuilder.build()``;
    direct construction upholds the same invariants via ``__post_init__``."""

    name: str
    technologies: Tuple[Technology, ...]
    interposer: Interposer
    instances: Tuple[DieInstance, ...]
    links: Tuple[Link, ...] = ()
    ties: Tuple[Tie, ...] = ()
    description: str = ""
    units: str = "um"
    routing_layer: str = "TopMetal2"
    methods: Tuple[InterconnectMethod, ...] = ()  # build()-time pitch check
    supplies: Tuple[Supply, ...] = ()  # shared supply nets

    def __post_init__(self) -> None:
        _validate_system(self)

    # convenience accessors (used by elaboration and tests)
    def instances_by_id(self) -> Dict[str, DieInstance]:
        return {inst.id: inst for inst in self.instances}

    def technology_ids(self) -> set:
        return {t.id for t in self.technologies}


def _validate_system(system: System) -> None:
    """Full system invariants. Runs at ``build()`` and at direct
    ``System(...)`` construction, so both paths get identical guarantees."""
    # technology ids: unique. Defense-in-depth mirror of the builder's
    # add_technology refusal (builder.py): a direct System(...) that bypasses the
    # builder must not silently last-wins on a duplicate id (technology_ids() is a
    # set, so a duplicate would otherwise vanish without complaint).
    seen_tech_ids: set = set()
    for tech in system.technologies:
        if tech.id in seen_tech_ids:
            raise DuplicateId(f"duplicate technology id {tech.id!r}")
        seen_tech_ids.add(tech.id)
    tech_ids = system.technology_ids()

    # instance ids: unique, non-empty, distinct from the interposer id
    by_id: Dict[str, DieInstance] = {}
    for inst in system.instances:
        if not inst.id:
            raise DuplicateId("a die instance has an empty id")
        if inst.id == system.interposer.id:
            raise DuplicateId(
                f"die instance id {inst.id!r} collides with the interposer id"
            )
        if inst.id in by_id:
            raise DuplicateId(f"duplicate die instance id {inst.id!r}")
        by_id[inst.id] = inst

    # every referenced technology is declared
    if system.interposer.technology not in tech_ids:
        raise SystemSpecError(
            f"interposer references undeclared technology "
            f"{system.interposer.technology!r} (declared: {sorted(tech_ids)})"
        )
    for inst in system.instances:
        if inst.die.technology not in tech_ids:
            raise SystemSpecError(
                f"die instance {inst.id!r} references undeclared technology "
                f"{inst.die.technology!r} (declared: {sorted(tech_ids)})"
            )

    # units
    if system.units != "um":
        raise UnsupportedUnits(
            f"units must be 'um', got {system.units!r}"
        )

    # links: resolve, compatibility, single-binding
    bound_ports: set = set()
    net_names: Dict[str, str] = {}  # net name -> origin, for DuplicateNet
    for i, link in enumerate(system.links):
        a_inst, a_port = resolve_port(by_id, link.a)
        b_inst, b_port = resolve_port(by_id, link.b)
        check_interface_compatible(link.a, a_port, link.b, b_port)
        for ref in (link.a, link.b):
            key = (ref.instance, ref.port)
            if key in bound_ports:
                raise PortAlreadyBound(
                    f"port {ref.instance}.{ref.port} is bound by more than one link"
                )
            bound_ports.add(key)
        lane_count = a_port.definition.lane_count
        for lane_i in range(lane_count):
            net = link_net_name(link, lane_count, lane_i)
            if not net:
                raise SystemSpecError(f"link #{i} generates an empty net name")
            if net in net_names:
                raise DuplicateNet(
                    f"net {net!r} generated by link #{i} collides with "
                    f"{net_names[net]}"
                )
            net_names[net] = f"link #{i}"

    # ties: resolve instance + pin, unique net names
    for j, tie in enumerate(system.ties):
        if not tie.net:
            raise SystemSpecError(f"tie #{j} has an empty net name")
        inst = by_id.get(tie.instance)
        if inst is None:
            raise UnknownInstance(
                f"tie {tie.net!r}: no die instance {tie.instance!r}"
            )
        if not inst.die.has_pin(tie.pin):
            raise UnknownPin(
                f"tie {tie.net!r}: die instance {tie.instance!r} has no pin "
                f"{tie.pin!r}"
            )
        if tie.net in net_names:
            raise DuplicateNet(
                f"net {tie.net!r} from tie #{j} collides with {net_names[tie.net]}"
            )
        net_names[tie.net] = f"tie #{j}"

    # supplies: class, endpoint count, resolve instance + pin, unique net names
    for k, supply in enumerate(system.supplies):
        if not supply.net:
            raise SystemSpecError(f"supply #{k} has an empty net name")
        if supply.net_class not in SUPPLY_NET_CLASSES:
            raise SystemSpecError(
                f"supply {supply.net!r}: net class {supply.net_class!r} is not one "
                f"of {SUPPLY_NET_CLASSES}"
            )
        if len(supply.endpoints) < 2:
            raise SystemSpecError(
                f"supply {supply.net!r}: {len(supply.endpoints)} endpoint(s); a "
                "supply net spans at least two pads (one pad is a tie)"
            )
        for ep in supply.endpoints:
            inst = by_id.get(ep.instance)
            if inst is None:
                raise UnknownInstance(
                    f"supply {supply.net!r}: no die instance {ep.instance!r}"
                )
            if not inst.die.has_pin(ep.pin):
                raise UnknownPin(
                    f"supply {supply.net!r}: die instance {ep.instance!r} has no "
                    f"pin {ep.pin!r}"
                )
        if supply.net in net_names:
            raise DuplicateNet(
                f"net {supply.net!r} from supply #{k} collides with "
                f"{net_names[supply.net]}"
            )
        net_names[supply.net] = f"supply #{k}"

    # single-pad claims (ties and supply endpoints) grouped by instance, for the
    # claim accounting below: each is one (net, pin) claim on one pad
    pad_claims_by_instance: Dict[str, list] = {}
    for tie in system.ties:
        pad_claims_by_instance.setdefault(tie.instance, []).append((tie.net, tie.pin))
    for supply in system.supplies:
        for ep in supply.endpoints:
            pad_claims_by_instance.setdefault(ep.instance, []).append(
                (supply.net, ep.pin)
            )

    # --- claim accounting: mirror pads.bump_map strict mode ---
    # Each declared port lane and each tie endpoint claims one pad of its name
    # group; a declaration reserves whether or not the port is linked.
    for inst in system.instances:
        mult: Dict[str, int] = {}
        for pin in inst.die.pins:
            mult[pin.name] = mult.get(pin.name, 0) + 1
        claims: Dict[str, int] = {}
        claim_nets: Dict[str, set] = {}
        for port in inst.die.ports:
            for lane in port.lanes:  # ports only ever touch m == 1 groups
                claims[lane] = claims.get(lane, 0) + 1
        for net, pin in pad_claims_by_instance.get(inst.id, ()):
            claims[pin] = claims.get(pin, 0) + 1
            claim_nets.setdefault(pin, set()).add(net)
        for name, count in claims.items():
            m = mult.get(name, 0)
            if count > m:  # (a) more claims than pads
                raise PinAlreadyBound(
                    f"die instance {inst.id!r}: pin {name!r} occurs {m} time(s) "
                    f"but is claimed {count} time(s) by ports/ties/supplies"
                )
            if m > 1 and len(claim_nets.get(name, ())) > 1:  # (b) shared group
                raise PinAlreadyBound(
                    f"die instance {inst.id!r}: repeated pin {name!r} (x{m}) is "
                    f"claimed by more than one tie or supply net "
                    f"({sorted(claim_nets[name])}); which pad belongs to which net "
                    "is undecidable without a pad index"
                )

    # --- method resolution + pitch class ---
    # interconnect method ids: unique. Defense-in-depth mirror of the builder's
    # add_method refusal (builder.py): the dict comprehension below would silently
    # last-wins on a duplicate id, so refuse it here first.
    seen_method_ids: set = set()
    for method in system.methods:
        if method.id in seen_method_ids:
            raise DuplicateId(f"duplicate interconnect method id {method.id!r}")
        seen_method_ids.add(method.id)
    methods_by_id = {method.id: method for method in system.methods}
    for inst in system.instances:
        for port in inst.die.ports:
            envelope = port.definition.max_pitch_um
            if envelope is None:
                continue  # envelope-free port needs no method
            method = methods_by_id.get(inst.die.connection)
            if method is None:  # method resolution
                raise UnknownMethod(
                    f"die instance {inst.id!r} port {port.name!r}: interface "
                    f"{port.definition.name!r} declares max_pitch_um={envelope} "
                    f"but connection {inst.die.connection!r} matches no declared "
                    "InterconnectMethod"
                )
            if method.pitch_um > envelope:  # pitch class
                raise PitchClassIncompatible(
                    f"die instance {inst.id!r} port {port.name!r}: method "
                    f"{method.id!r} pitch {method.pitch_um} um is coarser than "
                    f"interface {port.definition.name!r} max_pitch_um={envelope}"
                )

    # --- die process (interconnect manifest contract 1.1) ---
    # A property of the die's mount, not of a port: every instance whose
    # connection resolves to a declared method is held to that method's
    # die_processes, envelope or not. A connection that matches no declared
    # method resolves to nothing and stays as above (refused only through an
    # envelope), so documents that declare no methods are untouched. The
    # join is by technology id, string equality, as the assembly document keys
    # its technologies; the message says so because a mismatch in either id
    # space reads like a design error otherwise.
    for inst in system.instances:
        method = methods_by_id.get(inst.die.connection)
        if method is None:
            continue
        if method.die_processes is None:
            raise DieProcessIncompatible(
                f"die instance {inst.id!r} (technology {inst.die.technology!r}) "
                f"mounts with method {method.id!r}, which states no die process; "
                "a method that states nothing accepts no die, absence never "
                "means any process"
            )
        if inst.die.technology not in method.die_processes:
            raise DieProcessIncompatible(
                f"die instance {inst.id!r}: technology {inst.die.technology!r} is "
                f"not one the method {method.id!r} attaches "
                f"({', '.join(method.die_processes)}); the join is by technology "
                "id, as the assembly document keys its technologies"
            )
