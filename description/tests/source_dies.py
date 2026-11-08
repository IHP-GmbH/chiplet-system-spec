# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Die folders and a source for the source tests, written into a temp folder.

A die folder is what a source names: ``die-contract.json`` binding a
``pins.json`` inventory. Only the fields the source reads are written; the
digests are placeholders, since admission is not this package's.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

#: (port, direction, role, net, pad count)
PortSpec = Tuple[str, str, str, str, int]

DRIVER: Sequence[PortSpec] = (
    ("d2d_f0", "OUTPUT", "SIGNAL", "d2d_f0", 1),
    ("d2d_f1", "OUTPUT", "SIGNAL", "d2d_f1", 1),
    ("d2d_f2", "OUTPUT", "SIGNAL", "d2d_f2", 1),
    ("d2d_r0", "INPUT", "SIGNAL", "d2d_r0", 1),
    ("d2d_r1", "INPUT", "SIGNAL", "d2d_r1", 1),
    ("CLK", "INPUT", "SIGNAL", "CLK", 1),
    ("VDD", "INOUT", "POWER", "VDD", 2),
    ("VSS", "INOUT", "GROUND", "VSS", 2),
)
ECHO: Sequence[PortSpec] = (
    ("STATUS", "OUTPUT", "SIGNAL", "STATUS", 1),
    ("d2d_r1", "OUTPUT", "SIGNAL", "d2d_r1", 1),
    ("d2d_f0", "INPUT", "SIGNAL", "d2d_f0", 1),
    ("d2d_f1", "INPUT", "SIGNAL", "d2d_f1", 1),
    ("d2d_f2", "INPUT", "SIGNAL", "d2d_f2", 1),
    ("d2d_r0", "OUTPUT", "SIGNAL", "d2d_r0", 1),
    ("VDD", "INOUT", "POWER", "VDD", 1),
    ("VSS", "INOUT", "GROUND", "VSS", 1),
)
#: The volts each fixture die's contract declares per supply domain, the
#: domain being the port's net.
SUPPLY = {"VDD": 1.2, "VSS": 0}


def write_die(folder: Path, name: str, ports: Iterable[PortSpec], *,
              box: Tuple[float, float] = (1000.0, 1000.0), pdk: str = "ihp-sg13g2",
              supply: Optional[Mapping[str, float]] = None,
              places: Optional[Mapping[str, Tuple[float, float]]] = None) -> Path:
    """A die folder at ``folder`` whose inventory serves ``ports``.

    Pads are named ``PAD_<port>_<ordinal>`` and written in reverse ordinal
    order, so a reader that does not sort by ordinal is caught. Every pad is
    a terminal with its port, ordinal and ``pad_index``, the same physical
    identity as the inventory. With ``supply`` None the contract is the 1.1
    one a die emitted before V5 has, naming no supply domain; otherwise (1.2)
    each power and ground terminal names the domain its net names, and
    ``supply`` the volts declared per domain. ``places`` puts the named pads
    at (x, y) um in the die frame; the others sit in one row along the south
    side.
    """
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    index = 0
    for port, direction, role, net, count in ports:
        for ordinal in reversed(range(count)):
            rows.append({
                "name": f"PAD_{port}_{ordinal}", "pad_index": index,
                "center_x_dbu": 100000.0 + 80000.0 * index, "center_y_dbu": 100000.0,
                "width_dbu": 60000.0, "height_dbu": 60000.0,
                "port": port, "ordinal": ordinal, "net": net,
                "direction": direction, "role": role,
            })
            if places and rows[-1]["name"] in places:
                x, y = places[rows[-1]["name"]]
                rows[-1].update(center_x_dbu=x * 1000.0, center_y_dbu=y * 1000.0)
            index += 1
    (folder / "pins.json").write_text(json.dumps({"dbu_um": 0.001, "pins": rows}, indent=1),
                                      encoding="utf-8")
    contract = {
        "schema": "die-output-contract", "version": "1.1",
        "die": {"name": name, "layout": "layout.gds", "top_cell": name, "dbu_um": 0.001,
                "bbox_um": [0.0, 0.0, box[0], box[1]], "frame": "die_local_gds_origin"},
        "process": {"pdk": pdk, "pdk_version": "0" * 40},
        "inventory": {"kind": "pins_json", "path": "pins.json", "sha256": "0" * 64},
    }
    _DIRECTION = {"INPUT": "input", "OUTPUT": "output", "INOUT": "bidir"}
    contract["terminals"] = []
    for row in rows:
        term = {"name": row["name"], "pad_index": row["pad_index"],
                "role": row["role"].lower(), "port": row["port"], "ordinal": row["ordinal"]}
        direction = _DIRECTION.get(row["direction"])
        if direction is not None:
            term["direction"] = direction
        if supply is not None and row["role"] in ("POWER", "GROUND"):
            term["supply_domain"] = row["net"]
        contract["terminals"].append(term)
    if supply is not None:
        contract["version"] = "1.2"
        contract["supply_domains"] = {domain: {"volts": volts} for domain, volts in supply.items()}
    (folder / "die-contract.json").write_text(json.dumps(contract, indent=1), encoding="utf-8")
    return folder


SOURCE = '''\
# A two-die source the tests mutate line by line.
format_version: "1.1"
_metadata:
  finalize_required: true
  finalizer: "packaging-flow run"
assembly:
  name: two_die
  description: "Driver and echo on a carrier"
  units: um
interposer:
  adapter: intm4tm2
system:
  carrier: {technology: intm4tm2, top_cell: CF_CARRIER, size: [2000, 3200]}
  dies:
    - {id: U1, die: dies/DRIVER, at: [1000, 2200], rotation: 0, method: cupillar_opt1, thickness: 750}
    - {id: U2, die: dies/ECHO, at: [1000, 1000], rotation: 180, method: cupillar_opt1, thickness: 750}
  buses:
    - {ends: [U1, U2], ports: ["d2d_f[0..2]", "d2d_r[0..1]"], net_suffix: _n}
  rails:
    - {name: VDD, class: power, ports: [U1.VDD, U2.VDD]}
    - {name: VSS, class: ground, ports: [U1.VSS, U2.VSS]}
  alone:
    - U2.STATUS
    - U1.CLK
flow:
  packaging_flow:
    checks: {template: workshop}
'''


#: :data:`SOURCE` with a voltage on its power rail (format 1.2), for dies
#: whose contracts declare :data:`SUPPLY`.
DECLARED = SOURCE.replace('format_version: "1.1"', 'format_version: "1.2"').replace(
    "{name: VDD, class: power, ports:", "{name: VDD, class: power, volts: 1.2, ports:")


#: The start of rule 4's note, on each rail of a source that states no volts or
#: reaches a die whose contract states none.
UNCHECKED = "rails: voltage not checked for "


def checked(items: Iterable[Any]) -> Tuple[Any, ...]:
    """``items`` (findings, or rendered notes) less rule 4's notes, for a test
    of something else on :data:`SOURCE`."""
    return tuple(i for i in items if UNCHECKED not in (i if isinstance(i, str) else i.message))


def write_source(root: Path, text: str = SOURCE, *,
                 supply: Optional[Mapping[str, float]] = None) -> Path:
    """The source and its two die folders under ``root``, each die's
    contract declaring ``supply`` (:func:`write_die`); the source's path."""
    write_die(root / "dies" / "DRIVER", "DRIVER", DRIVER, supply=supply)
    write_die(root / "dies" / "ECHO", "ECHO", ECHO, supply=supply)
    path = root / "system.chiplet"
    path.write_text(text, encoding="utf-8")
    return path
