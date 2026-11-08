# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Format 1.5, a rail's routing requirement: ``width_um``, or ``amps`` with
``current_density_ma_per_um``, on a power or ground rail. Tier 0 holds the
pairing the vendored reader holds (its ``_validate_rail_requirement``, private
to it), in the words of the rail's line; the width a current needs is the
router's to compute."""
from __future__ import annotations

from typing import Collection, NamedTuple, Optional

#: The keys, in the order ``ITEM_KEYS`` writes them on a rail.
RAIL_REQUIREMENT_KEYS = ("width_um", "amps", "current_density_ma_per_um")


class RailRequirement(NamedTuple):
    """What a rail states; a key it does not state is None."""

    width_um: Optional[float] = None
    amps: Optional[float] = None
    current_density_ma_per_um: Optional[float] = None


class RequiredRail(NamedTuple):
    """A rail that states a requirement, as the elaborator hands it over."""

    rail: str
    width_um: Optional[float]
    amps: Optional[float]
    current_density_ma_per_um: Optional[float]
    line: int


def pairing_refusal(stated: Collection[str]) -> Optional[str]:
    """Why the keys in ``stated`` break the pairing, or None: a width beside a
    current, or one half of a current without the other."""
    has_width, has_amps, has_density = (key in stated for key in RAIL_REQUIREMENT_KEYS)
    if has_width and (has_amps or has_density):
        return "width_um and a current are both stated; a rail states one or the other"
    if has_amps != has_density:
        stated_key, missing = (("amps", "current_density_ma_per_um") if has_amps
                               else ("current_density_ma_per_um", "amps"))
        return f"{stated_key} without {missing}; a width is computed from both"
    return None
