# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Footprint names a KiCad sheet is given.

:func:`die_footprint` is ``chiplet_dies:<cell>``. :func:`ext_pad_footprint`
is ``chiplet_carrier:EXT_PAD_<um>um``, the square pad edge in micrometres.
A cell KiCad 9 would rewrite or refuse as a footprint name, an empty name,
and any whitespace are refused. A pad size is a positive whole number of um,
and nothing else.
"""
from __future__ import annotations

from . import ChipletFormatError

__all__ = [
    "DIE_LIBRARY",
    "CARRIER_LIBRARY",
    "FootprintIdError",
    "die_footprint",
    "ext_pad_footprint",
]

#: The footprint library a die symbol names.
DIE_LIBRARY = "chiplet_dies"
#: The footprint library an external pad names.
CARRIER_LIBRARY = "chiplet_carrier"

# KiCad 9.0.6, common/lib_id.cpp: LIB_ID::HasIllegalChars (lines 175-188)
# and LIB_ID::FixIllegalChars (lines 191-205) both ask isLegalChar (lines
# 208-240), which refuses a code point below U+0020 and ':', '\\', '<', '>'
# and '"'. That release has no findInvalidLibIdChar. The same release,
# pcbnew/footprint.cpp: FOOTPRINT::IsLibNameValid (lines 2398-2406) refuses
# StringLibNameInvalidChars (lines 2409-2423), which adds '%', '$' and '/'.
# A die footprint has to be drawable, so both sets are refused here. An empty
# name and any whitespace are refused as well: KiCad would keep a space.
_ILLEGAL = frozenset(":\\<>\"%$/")


class FootprintIdError(ChipletFormatError):
    """A footprint name KiCad 9 would not keep or draw, or a pad size that is
    not a positive whole number of um."""


def die_footprint(cell: str) -> str:
    """``chiplet_dies:<cell>``, or :class:`FootprintIdError`."""
    if not isinstance(cell, str):
        raise FootprintIdError(f"cell name {cell!r} is not a string")
    if cell == "":
        raise FootprintIdError("cell name is empty")
    for ch in cell:
        if ch.isspace() or ord(ch) < 0x20 or ch in _ILLEGAL:
            raise FootprintIdError(
                f"cell name {cell!r} contains {ch!r}, which a KiCad 9 footprint "
                "name cannot hold")
    return f"{DIE_LIBRARY}:{cell}"


def ext_pad_footprint(um: int) -> str:
    """``chiplet_carrier:EXT_PAD_<um>um`` for a positive whole number of um."""
    if isinstance(um, bool) or not isinstance(um, int) or um <= 0:
        raise FootprintIdError(f"pad size {um!r} is not a positive whole number of um")
    return f"{CARRIER_LIBRARY}:EXT_PAD_{um}um"
