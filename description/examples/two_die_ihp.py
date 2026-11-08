# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The canonical system: two SG13G2 dies on an IntM4TM2 interposer.

``build_system()`` returns the frozen :class:`System` the tests elaborate and
route; run as a script it takes ``--out`` and emits the ``.chiplet``. The result
is semantically identical to ``tests/fixtures/ihp_two_die_route.chiplet``.

The dies mount on the plane the interposer PDK publishes, its
``beol.attachment_surface_z_um``, read from
``$INTERPOSER_PDK_ROOT/libs.tech/intm4tm2_stack.json``. Without it the system is
not built; there is no fallback value.
"""
from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

from chiplet_system_description import (
    INTM4TM2,
    SG13G2,
    Die,
    Dimensions,
    InterfaceDefinition,
    Interposer,
    InterposerIoPad,
    Port,
    Position,
    Role,
    SystemBuilder,
    emit,
    load_pins_json,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
_PINS_PATH = _FIXTURES / "two_die_route.pins.json"

PLANE_DESCRIPTOR = Path("libs.tech") / "intm4tm2_stack.json"
PLANE_KEY = "beol.attachment_surface_z_um"
#: cupillar_opt1's connection stack (28 um Cu pillar, 16 um SnAg cap): a die sits
#: this far above the plane.
STACK_HEIGHT_UM = Decimal("44")


class AttachPlaneUnavailable(Exception):
    """The interposer PDK's mount plane cannot be read, so the system is not built."""


def attach_plane() -> Decimal:
    """The mount plane, exactly as the interposer PDK writes it."""
    root = os.environ.get("INTERPOSER_PDK_ROOT")
    if not root:
        raise AttachPlaneUnavailable(
            f"set INTERPOSER_PDK_ROOT to the interposer PDK: the mount plane is its "
            f"{PLANE_KEY} in {PLANE_DESCRIPTOR}")
    path = Path(root) / PLANE_DESCRIPTOR
    try:
        stack = json.loads(path.read_text(encoding="utf-8"),
                           parse_float=Decimal, parse_int=Decimal)
        plane = stack["beol"]["attachment_surface_z_um"]
    except (OSError, ValueError, LookupError, TypeError) as exc:
        raise AttachPlaneUnavailable(f"cannot read {PLANE_KEY} from {path}: {exc!r}") from None
    if not (isinstance(plane, Decimal) and plane.is_finite()):
        raise AttachPlaneUnavailable(f"{PLANE_KEY} in {path} is not a number of um: {plane!r}")
    return plane


def build_system():
    """Author and freeze the two-die IHP system (the answer to the golden)."""
    plane = attach_plane()
    # The exact decimal sum, so the die z is written as its shortest text.
    die_z = float(plane + STACK_HEIGHT_UM)
    pins = load_pins_json(_PINS_PATH)
    d2d = InterfaceDefinition("generic-parallel", lane_count=6)

    def die(name, top_cell, port):
        return Die(
            name=name, technology="sg13g2", top_cell=top_cell,
            dimensions=Dimensions(1000.0, 2000.0, 750.0),
            pins=pins, ports=(port,), pins_source=str(_PINS_PATH),
        )

    u1 = die("u1", "DIE_U1", Port("d2d", d2d, Role.MANAGER,
                                  ("A0", "A1", "A2", "A3", "A4", "A5")))
    u2 = die("u2", "DIE_U2", Port("d2d", d2d, Role.SUBORDINATE,
                                  ("B0", "B1", "B2", "B3", "B4", "B5")))

    b = SystemBuilder("ihp_two_die_route", interposer=Interposer(
        technology="intm4tm2", top_cell="INTERPOSER",
        dimensions=Dimensions(6492.312, 5602.001, float(plane)),
        position=Position(3246.156, 2801.0),
        io_pads=(InterposerIoPad("J_VDD", "VDD", Position(300.0, 2801.0), 80.0, 80.0),
                 InterposerIoPad("J_GND", "GND", Position(6192.0, 2801.0), 80.0, 80.0))),
        description="Two SG13G2 dies on an IntM4TM2 interposer; cupillar_opt1; routable netlist")
    b.add_technology(INTM4TM2)
    b.add_technology(SG13G2)
    b.add_die(u1, "U1", Position(1500.0, 2801.0, die_z))
    b.add_die(u2, "U2", Position(5000.0, 2801.0, die_z))
    b.connect(("U1", "d2d"), ("U2", "d2d"), net_prefix="sig")
    b.tie("U1", "VDD", "VDD", net_class="power")
    b.tie("U2", "GND", "GND", net_class="ground")
    return b.build()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("build/two_die_ihp.chiplet"))
    args = ap.parse_args()
    out = emit(build_system(), args.out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
