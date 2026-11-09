# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Read a ``*.pins.json`` pin list into a tuple of :class:`DiePin`.

Rails mirrored from ``interposer_pnr.pads.load_pin_list``: ``dbu_um`` must be
present in the file (raise, never guess the pad scale) and each ``pins[]`` entry
must carry a ``name``. Geometry is not validated further and frames are not
transformed; the eDSL never places bumps (that is ``interposer-pnr bumps``'
job). Coordinates are die-local GDS micrometres, converted here from database
units by the file's own ``dbu_um``.

A malformed pin list is an input-file defect, not a spec defect, so it raises a
plain :class:`ValueError` rather than a member of the
:mod:`chiplet_system_description.errors` hierarchy (which is reserved for System-spec
invariants).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from .errors import LaneSelectionError
from .model import DiePin


def _scaled(value, dbu: float):
    if value is None:
        return None
    return float(value) * dbu


def load_pins_json(path) -> Tuple[DiePin, ...]:
    """Read a real ``*.pins.json`` file into a tuple of :class:`DiePin`."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("pins"), list):
        raise ValueError(f"{path}: not a pin list (no 'pins' array)")

    dbu = data.get("dbu_um")
    try:
        dbu = float(dbu)
    except (TypeError, ValueError):
        raise ValueError(
            f"{path}: no usable 'dbu_um'; refusing to guess the pad scale"
        ) from None
    if dbu <= 0:
        raise ValueError(f"{path}: dbu_um must be > 0, got {dbu!r}")

    pins = []
    for i, pin in enumerate(data["pins"]):
        if not isinstance(pin, dict):
            raise ValueError(f"{path}: pin #{i} is not an object")
        name = pin.get("name")
        if not name:
            raise ValueError(f"{path}: pin #{i} has no 'name'")
        pins.append(
            DiePin(
                name=str(name),
                pad_index=(
                    int(pin["pad_index"]) if pin.get("pad_index") is not None else None
                ),
                type=str(pin.get("type", "")),
                side=str(pin.get("side", "")),
                x_um=_scaled(pin.get("center_x_dbu"), dbu),
                y_um=_scaled(pin.get("center_y_dbu"), dbu),
                width_um=_scaled(pin.get("width_dbu"), dbu),
                height_um=_scaled(pin.get("height_dbu"), dbu),
            )
        )
    return tuple(pins)


def lanes_from_pins(
    pins: Tuple[DiePin, ...], *, prefix: str, count: int
) -> Tuple[str, ...]:
    """Deterministic lane selection.

    Select the pins whose ``name`` starts with ``prefix``, require each selected
    pin to carry a ``pad_index``, order by ``pad_index``, and require exactly
    ``count`` members with no repeated name. Any violation raises
    :class:`LaneSelectionError` (empty or short selection, oversized selection
    with no silent truncation, missing ``pad_index``, repeated name). The result
    feeds :class:`~chiplet_system_description.model.Port` unchanged, so the full check
    chain validates it; the helper is pure and never touches geometry.

    Extending selection beyond prefix + ``pad_index`` order (sides, geometric
    order, regex) has no consumer yet and is left out on purpose.
    """
    selected = [pin for pin in pins if pin.name.startswith(prefix)]
    for pin in selected:
        if pin.pad_index is None:
            raise LaneSelectionError(
                f"lanes_from_pins(prefix={prefix!r}): pin {pin.name!r} has no "
                "pad_index; selection order is undefined without it"
            )
    names = [pin.name for pin in selected]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise LaneSelectionError(
            f"lanes_from_pins(prefix={prefix!r}): repeated pin name(s) {dupes}; "
            "each lane must name a distinct pin"
        )
    ordered = sorted(selected, key=lambda pin: pin.pad_index)
    if len(ordered) != count:
        raise LaneSelectionError(
            f"lanes_from_pins(prefix={prefix!r}): selected {len(ordered)} pin(s) "
            f"but count={count} was requested (no silent truncation or padding)"
        )
    return tuple(pin.name for pin in ordered)
