# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The pin list's version and the words it shares with the die contract.

A built die's pin list (``pins.json``, ``schemas/pins.schema.json``) is written
beside its die contract and read by whatever binds, wires or simulates the die.
Each pin carries a role and a direction in the die view's authored vocabulary;
the contract spells the same two in its own closed vocabulary. Both sides read
the version and the three tables from here: the DEF ``USE`` the die view
expects for each role, and the role and direction each authored word becomes
in the contract.

From pins.json 1.1 the pin's KiCad electrical type is derived, not stored: the
one table here gives it from role and direction, keyed by the pin list's words
(:data:`KICAD_TYPE`) and by the die contract's (:data:`KICAD_TYPE_OF_TERMINAL`),
and :func:`kicad_type` reads one pin by the format's rule. The stored ``type``
is deprecated: it is used only for a pin with no role, and it never overrides
the role. Writers keep stamping :data:`PINS_VERSION` until every reader accepts
:data:`SUPPORTED_PINS_VERSION`.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from . import ChipletFormatError

__all__ = [
    "PINS_VERSION",
    "SUPPORTED_PINS_VERSION",
    "USE_BY_ROLE",
    "ROLE",
    "DIRECTION",
    "PIN_ROLES",
    "PIN_DIRECTIONS",
    "KICAD_PIN_TYPES",
    "KICAD_TYPE",
    "KICAD_TYPE_OF_TERMINAL",
    "kicad_type",
]

#: The pins.json version writers stamp; a reader checks the major.
PINS_VERSION = "1.0"
#: The highest pins.json version a reader here understands: 1.1 declares role
#: and direction and deprecates type.
SUPPORTED_PINS_VERSION = "1.1"
#: The DEF ``USE`` the die view expects for each role.
USE_BY_ROLE = {"POWER": "POWER", "GROUND": "GROUND", "SIGNAL": "SIGNAL"}
# The view's authored vocabulary on the left, the contract's closed one on the
# right. A value outside this table is a refusal: the contract's role and
# direction are closed sets, and guessing one is exactly the silent step the
# format exists to remove.
ROLE = {"SIGNAL": "signal", "POWER": "power", "GROUND": "ground"}
DIRECTION = {"INPUT": "input", "OUTPUT": "output", "INOUT": "bidir"}

#: The pin list's closed vocabulary, exact capitals: the role and direction
#: words pins.schema.json declares.
PIN_ROLES = ("SIGNAL", "POWER", "GROUND", "NO_CONNECT")
PIN_DIRECTIONS = ("INPUT", "OUTPUT", "INOUT", "ANALOG", "NONE")
#: KiCad's pin electrical types, as its files spell them.
KICAD_PIN_TYPES = ("input", "output", "bidirectional", "tri_state", "passive", "free",
                   "unspecified", "power_in", "power_out", "open_collector", "open_emitter",
                   "no_connect")
# Each pin-list word and the die contract's word for it, over the whole
# vocabulary: ROLE and DIRECTION above, which keep the built-die words only,
# and the three words no built die writes.
_CONTRACT_WORD = {**ROLE, **DIRECTION, "NO_CONNECT": "no_connect", "ANALOG": "analog",
                  "NONE": "none"}
# The one table: role, direction (None: any direction or none), KiCad type.
_ROWS = (
    ("POWER", None, "power_in"),
    ("GROUND", None, "power_in"),
    ("NO_CONNECT", None, "no_connect"),
    ("SIGNAL", "INPUT", "input"),
    ("SIGNAL", "OUTPUT", "output"),
    ("SIGNAL", "INOUT", "bidirectional"),
    ("SIGNAL", "ANALOG", "passive"),
    ("SIGNAL", "NONE", "passive"),
)
#: The KiCad type of each (role, direction) in the pin list's words; the
#: direction is None when the pin has none. A signal pin with no direction is
#: not a key: :func:`kicad_type` reads it as passive and says so.
KICAD_TYPE = {(role, each): word for role, direction, word in _ROWS
              for each in ((None,) + PIN_DIRECTIONS if direction is None else (direction,))}
#: The same table keyed by the die contract's role and direction words. A
#: signal terminal with no direction is not a key here either; it is passive,
#: as :func:`kicad_type` reads the pin.
KICAD_TYPE_OF_TERMINAL = {
    (_CONTRACT_WORD[role], None if direction is None else _CONTRACT_WORD[direction]): word
    for (role, direction), word in KICAD_TYPE.items()}


def _label(pin: Mapping[str, Any]) -> str:
    """The pin as a message names it: its name, and its pad index when it has
    one, so a rail's pads are told apart."""
    if "pad_index" in pin:
        return f"{pin.get('name')} (pad_index {pin['pad_index']})"
    return f"{pin.get('name')}"


def kicad_type(pin: Mapping[str, Any]) -> Tuple[str, Tuple[str, ...]]:
    """The KiCad electrical type of one pins.json pin, and the warnings its
    reading gives, which the caller prints.

    A pin with a role takes the table's word; a signal pin with no direction is
    passive, with a warning. A stored ``type`` beside a role that differs from
    the table's word is named in a warning and loses. A pin with no role takes
    its stored ``type``, which must be a KiCad type, with a warning that the
    field is deprecated; with no type either it is passive, with a warning. A
    direction is not read without a role. A role or direction outside the
    vocabulary, in any spelling but exact capitals, raises
    :class:`ChipletFormatError`.
    """
    label = _label(pin)
    if "role" not in pin:
        unused = " Its direction is not used without a role." if "direction" in pin else ""
        if "type" not in pin:
            return "passive", (f"pin {label}: it has neither a role nor a type, so its KiCad "
                               f"type is passive; write a role and a direction.{unused}",)
        stored = pin["type"]
        if not isinstance(stored, str) or stored not in KICAD_PIN_TYPES:
            raise ChipletFormatError(
                f'pin {label}: it has no role, and its type "{stored}" is not a KiCad pin type. '
                f"Write a role and a direction, or use one of these types: "
                f"{', '.join(KICAD_PIN_TYPES)}.")
        return stored, (f'pin {label}: it has no role, so its KiCad type is the stored type '
                        f'"{stored}". The type field is deprecated from pins.json 1.1; write a '
                        f"role and a direction instead.{unused}",)
    role = pin["role"]
    if not isinstance(role, str) or role not in PIN_ROLES:
        raise ChipletFormatError(
            f'pin {label}: role "{role}" is not a pin-list role; write one of '
            f"{', '.join(PIN_ROLES)}, in capitals.")
    direction: Optional[str] = None
    if "direction" in pin:
        direction = pin["direction"]
        if not isinstance(direction, str) or direction not in PIN_DIRECTIONS:
            raise ChipletFormatError(
                f'pin {label}: direction "{direction}" is not a pin-list direction; write one '
                f"of {', '.join(PIN_DIRECTIONS)}, in capitals.")
    said = []
    if role == "SIGNAL" and direction is None:
        word = "passive"
        said.append(f"pin {label}: role SIGNAL has no direction, so its KiCad type is passive; "
                    f"write a direction ({', '.join(PIN_DIRECTIONS[:-1])} or {PIN_DIRECTIONS[-1]}).")
    else:
        word = KICAD_TYPE[(role, direction)]
    if "type" in pin and pin["type"] != word:
        said.append(f'pin {label}: the stored type "{pin["type"]}" is not "{word}", the KiCad '
                    f'type its role and direction give; "{word}" is used.')
    return word, tuple(said)
