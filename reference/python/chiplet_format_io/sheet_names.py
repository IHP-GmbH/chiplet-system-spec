# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The names a KiCad sheet carries verbatim: its nets, its pins and its pads.
(Not KiCad's own sheet name, the Sheetname field of a hierarchical sheet.)

The generator writes them into labels, pins and the carrier's pad list, and the
two sheet imports check the net names they read back, so both take the rule
from here. A name is a plain first character, then plain characters and ``[N]``
digit subscripts in any order, as a die's bus pads are named
(``IO_BOND_bidirs[0].bidir_pad``). kicad-cli 9.99 netlists such a name
unchanged on a label off any bus, and ``x[3]`` and ``x[03]`` stay two nets.
The first character is plain because KiCad reads a pin name of ``[0]`` or
``[1-3]`` as stacked pins. A range (``B[0..3]``, ``[3:0]``), an empty or nested
subscript and a non-digit one are refused.

The subscript has no digit limit: nothing reads it as a number, and every
reader compares the whole name as text. The rule is stated without anchors,
and :func:`is_sheet_name` matches the whole name.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["SHEET_NAME_RULE", "SHEET_NAME_WORDS", "is_sheet_name"]

#: A sheet name, unanchored.
SHEET_NAME_RULE = re.compile(r"[A-Za-z0-9_.+-](?:[A-Za-z0-9_.+-]|\[[0-9]+\])*")

#: The rule in words, for the refusal that quotes it.
SHEET_NAME_WORDS = "plain [A-Za-z0-9_.+-] name, with [N] subscripts allowed after its first character"


def is_sheet_name(value: Any) -> bool:
    """Whether ``value`` is a whole sheet name."""
    return isinstance(value, str) and SHEET_NAME_RULE.fullmatch(value) is not None
