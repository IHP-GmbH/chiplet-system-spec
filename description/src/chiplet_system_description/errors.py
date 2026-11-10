# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Typed exception hierarchy for the System object model.

Every concrete error carries enough context to name both sides in the message.
Tests assert the *class*, never the message text; message wording is free to
change. Elaboration failures are not translated here: the vendored
``chiplet_format_io.ChipletFormatError`` is re-raised untouched, because the
shared loader's verdict on a ``.chiplet`` document is the authoritative verdict.

The check that a referenced ``technology`` id is declared has no dedicated
concrete class; it raises the base :class:`SystemSpecError`, so a caller that
catches the concrete classes by name never meets one it does not know.
"""
from __future__ import annotations


class SystemSpecError(ValueError):
    """Base class for every static defect the System model refuses."""


class UnknownInstance(SystemSpecError):
    """A reference names a die instance that was never added."""


class UnknownPort(SystemSpecError):
    """A reference names a port that its die does not declare."""


class UnknownPin(SystemSpecError):
    """A port lane or a tie names a die pin that does not exist."""


class AmbiguousLane(SystemSpecError):
    """A port lane names a pin that occurs more than once in the pin list.

    Elaboration binds endpoints to pads by name + ordinal in netlist order, an ordinal a
    port cannot express, so a repeated lane name is refused rather than bound to
    an arbitrary pad. Ties may still reference repeated names (each contributes
    one endpoint at its declaration ordinal).
    """


class DuplicateLane(SystemSpecError):
    """A single port lists the same lane (pin name) more than once.

    Each lane elaborates to its own net endpoint, so a repeated lane silently
    drives one pad from two nets while another lane goes unconnected, a short.
    Refused at ``Port`` construction. Distinct from
    :class:`AmbiguousLane`, which is about a pin name repeated in the pin list.
    """


class DuplicateId(SystemSpecError):
    """Two die instances share an id, an id is empty, or one equals the interposer id."""


class DuplicateNet(SystemSpecError):
    """Two nets (across generated link nets and ties) share a name."""


class PortAlreadyBound(SystemSpecError):
    """A port is used by more than one link."""


class InterfaceTypeMismatch(SystemSpecError):
    """A link binds two ports whose interface definitions differ by name."""


class LaneCountMismatch(SystemSpecError):
    """A port's lane tuple length disagrees with its definition, or two linked
    ports disagree on lane count."""


class RoleConflict(SystemSpecError):
    """A link's two ends are not exactly one MANAGER and one SUBORDINATE."""


class UnsupportedAnchor(SystemSpecError):
    """A die declares an anchor other than ``bbox_center``.

    This model writes the die geometric centre into ``position``, so it emits the
    ``bbox_center`` anchor whose reference point is exactly that centre
    (coord_frame_contract.md section 2.1). Any other anchor would mean the numbers
    and the anchor disagree, the frame bug this refuses to author."""


class UnsupportedUnits(SystemSpecError):
    """A system declares units other than ``um``."""


# ----------------------------------------------------------------------
# Method, pitch and binding errors. All SystemSpecError subclasses; tests
# assert the class, never the message text.
# ----------------------------------------------------------------------
class InterfaceVersionMismatch(SystemSpecError):
    """A link binds two same-named interface definitions of different versions.

    Interface identity is ``(name, version)`` with exact string equality: a D2D
    standard changes its ballout between versions, so a version difference is a
    physical difference (checked at connect()).
    """


class InterfaceContractMismatch(SystemSpecError):
    """Two linked definitions agree on name, version and lane count but are not
    structurally equal (after those three checks, only ``max_pitch_um`` can
    differ). Refuses a forged or drifted same-name descriptor (check 4 of
    ``check_interface_compatible``).
    """


class PinAlreadyBound(SystemSpecError):
    """A pad landing is claimed more times than it exists, or an ambiguously
    shared repeated-name group is claimed by more than one net.

    Fail-fast mirror of ``interposer_pnr.pads.bump_map`` strict mode: a die pin
    claimed across two ports of one die (Die ctor), a ``(instance, pin)`` group
    whose total port+tie claims exceed its multiplicity, or an ``m > 1`` group
    claimed by more than one distinct tie net (build()).
    """


class MissingBumpGeometry(SystemSpecError):
    """A pin bound by a port lane has no ``x_um``/``y_um`` centre.

    A bound lane is a bump landing; ``interposer_pnr.pads.load_pin_list`` refuses
    centre-less pins at the bumps stage, and this is the fail-fast mirror of that
    refusal, moved up to Die construction. Unbound pins may stay geometry-less.
    """


class UnknownMethod(SystemSpecError):
    """A die whose interface declares a pitch envelope references a
    ``connection`` method id that no declared :class:`InterconnectMethod`
    provides. An envelope with no method to check it against is refused, never
    silently skipped (build()).
    """


class PitchClassIncompatible(SystemSpecError):
    """A die's interconnect method cannot reach the interface's pitch class
    (``method.pitch_um > definition.max_pitch_um``). Blind-spot check: no
    downstream stage knows interface pitch classes (build()).
    """


class DieProcessIncompatible(SystemSpecError):
    """A die is mounted with a declared method that does not attach its
    process: the die's ``technology`` id is not in the method's
    ``die_processes``, or the method states none (``None``), which accepts no
    die. Interconnect manifest contract 1.1. The join is by technology id,
    string equality, as the assembly document keys its technologies; a
    property of the die's mount, so it is checked with or without a pitch
    envelope (build()).
    """


class LaneSelectionError(SystemSpecError):
    """``lanes_from_pins`` cannot form a clean lane tuple: an empty or short
    selection, an oversized selection (no silent truncation), a selected pin
    without a ``pad_index``, or a repeated pin name.
    """


__all__ = [
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
    # method, pitch and binding errors
    "InterfaceVersionMismatch",
    "InterfaceContractMismatch",
    "PinAlreadyBound",
    "MissingBumpGeometry",
    "UnknownMethod",
    "PitchClassIncompatible",
    "LaneSelectionError",
]
