# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""chiplet_system_description -- a typed Python eDSL that elaborates a chiplet-on-interposer
system description to the ``.chiplet`` the flow already consumes.

Author a :class:`SystemBuilder`, add dies and links, and :func:`emit` the frozen
:class:`System` to a ``.chiplet`` through the shared reference writer. The one
interface check (lane count, direction, pin existence) fires eagerly at
``connect()``, before any physical step. The names below are the public API.
"""
from __future__ import annotations

from .builder import SystemBuilder
from .elaborate import elaborate, emit
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
    LaneSelectionError,
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
from .model import (
    CUPILLAR_OPT1,
    CUPILLAR_OPT2,
    CUPILLAR_OPT3,
    INTM4TM2,
    SBUMP_SAC305,
    SG13G2,
    Die,
    DieInstance,
    DiePin,
    Dimensions,
    InterconnectMethod,
    InterfaceDefinition,
    Interposer,
    InterposerIoPad,
    Link,
    PinRef,
    Port,
    PortRef,
    Position,
    Role,
    Supply,
    System,
    Technology,
    Tie,
)
from .pins import lanes_from_pins, load_pins_json

__all__ = [
    # authoring + model
    "System",
    "SystemBuilder",
    "Die",
    "DieInstance",
    "DiePin",
    "Port",
    "PortRef",
    "Role",
    "InterfaceDefinition",
    "Interposer",
    "InterposerIoPad",
    "Link",
    "Tie",
    "Supply",
    "PinRef",
    "Position",
    "Dimensions",
    "Technology",
    "SG13G2",
    "INTM4TM2",
    "load_pins_json",
    "elaborate",
    "emit",
    # interconnect methods and lane selection
    "InterconnectMethod",
    "CUPILLAR_OPT1",
    "CUPILLAR_OPT2",
    "CUPILLAR_OPT3",
    "SBUMP_SAC305",
    "lanes_from_pins",
    # exceptions
    "SystemSpecError",
    "UnknownInstance",
    "UnknownPort",
    "UnknownPin",
    "AmbiguousLane",
    "DuplicateLane",
    "DuplicateId",
    "DuplicateNet",
    "PortAlreadyBound",
    "InterfaceTypeMismatch",
    "LaneCountMismatch",
    "RoleConflict",
    "UnsupportedAnchor",
    "UnsupportedUnits",
    # method, pitch and binding exceptions
    "InterfaceVersionMismatch",
    "InterfaceContractMismatch",
    "PinAlreadyBound",
    "MissingBumpGeometry",
    "UnknownMethod",
    "PitchClassIncompatible",
    "DieProcessIncompatible",
    "LaneSelectionError",
]
