# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Scalars as a source file writes them, shared by the writer and the check."""
from __future__ import annotations

import json
import math
import re
from decimal import Decimal

#: The carrier's database unit, um: placements are compared at this grain.
DBU_UM = Decimal("0.001")

_LINE_BREAKS = {"\u0085": "\\N", " ": "\\L", " ": "\\P"}


def quote(text: str) -> str:
    """``text`` as a double-quoted scalar every reader reads back exactly."""
    out = json.dumps(text, ensure_ascii=False)
    for raw, escape in _LINE_BREAKS.items():
        out = out.replace(raw, escape)
    return out


#: A string a source may write bare: both YAML reader families (1.1 loaders such
#: as PyYAML, 1.2 core such as yaml-cpp) read every such word as the same string,
#: once the bool and null words are taken out.
BARE = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]*\Z")
#: The words a YAML 1.1 reader takes for a bool or a null, in every case form.
RETYPED_WORDS = frozenset({"y", "n", "yes", "no", "true", "false", "on", "off", "null"})


def bare(text: str) -> bool:
    """Whether ``text`` may be written without quotes in a source."""
    return bool(BARE.match(text)) and text.lower() not in RETYPED_WORDS


def scalar(text: str) -> str:
    """``text`` as a source writes it: bare when :func:`bare` allows, else quoted."""
    return text if bare(text) else quote(text)


def _decimal(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"not a number: {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"not a finite number: {value!r}")
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


def number(value) -> str:
    """The shortest decimal reading back to ``value``: no exponent, no ``.0``."""
    exact = _decimal(value)
    if exact == 0:
        return "0"
    if exact == exact.to_integral_value():
        return format(exact.to_integral_value(), "f")
    return format(exact.normalize(), "f")


def quantized(value) -> Decimal:
    """``value`` on the 1 nm grid."""
    return _decimal(value).quantize(DBU_UM)
