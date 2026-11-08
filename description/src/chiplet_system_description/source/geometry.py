# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The box of one placed die, and whether two boxes overlap.

``at`` is the die centre, in the same units as ``extent`` (width, height).
:func:`turned` swaps the two at rotation 90 or 270. Nothing here is quantized
except :func:`_grid_extent`: a caller that wants the nanometre grid passes that
extent, and quantizes ``at`` itself.

:func:`overlaps` is the open test. Boxes that only share an edge do not overlap.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence, Tuple

from .scalars import quantized


def turned(extent: Sequence, rotation: int) -> Tuple:
    """``extent`` as drawn, or swapped when ``rotation`` is 90 or 270."""
    w, h = extent[0], extent[1]
    if rotation in (90, 270):
        return (h, w)
    return (w, h)


def die_box(extent: Sequence, at: Sequence, rotation: int) -> Tuple:
    """The axis-aligned box ``(x0, y0, x2, y2)`` of ``extent`` centred on ``at``."""
    w, h = turned(extent, rotation)
    x, y = at[0], at[1]
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def _grid_extent(folder) -> Tuple[Decimal, Decimal]:
    """Width and height on the nanometre grid, before rotation."""
    return quantized(folder.width), quantized(folder.height)


def overlaps(a: Sequence, b: Sequence) -> bool:
    """Whether boxes ``a`` and ``b`` overlap. Sharing an edge does not."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
