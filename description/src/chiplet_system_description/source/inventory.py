# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""A die folder as the source reads it: the contract's die facts and the ports
its inventory serves.

The folder holds ``die-contract.json`` beside the files it binds. This module
reads only what a source needs to elaborate and to check at tier 1: the top
cell, the box, the technology, per port its direction, role, net and pads in
ordinal order, and the supply domain of each power and ground pad with the
volts the contract declares for it (die contract 1.2). It does not admit the die. Admission (the digests the contract
binds, the strict contract read) belongs to the flow that runs the source, so a
green here says nothing about whether the bytes are the die the contract names.

The technology joins the contract's ``process.pdk`` through
``chiplet_format_io.technology.TECHNOLOGY_OF_PDK``, the one table, or is the
contract's own ``process.technology`` when it states one; a contract that states
both must agree with itself, and one whose technology neither gives is refused,
so the join answers as admission's does.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .._vendor.chiplet_format_io import ContractVersionError, _parse_contract_version, check_contract_version
from .._vendor.chiplet_format_io.die_contract import ACCEPTED_DIE_CONTRACT_VERSIONS
from .._vendor.chiplet_format_io.supply_domains import SupplyDomainError, read_supply_domains
from .._vendor.chiplet_format_io.technology import TECHNOLOGY_OF_PDK
from ..errors import SystemSpecError
from ..model import DiePin
from ..pins import load_pins_json

CONTRACT = "die-contract.json"
#: Inventory roles that make a port a rail port, and the rail class each is.
RAIL_ROLES = {"POWER": "power", "GROUND": "ground"}


class InventoryError(SystemSpecError):
    """A die folder the source cannot read its ports from."""


class UnportedPad(InventoryError):
    """A pad serves no port, or the die states no port."""


@dataclass(frozen=True)
class InventoryPort:
    """One port an inventory's pads serve."""

    name: str
    direction: str  # INPUT, OUTPUT, INOUT, as the inventory states it
    role: str  # SIGNAL, POWER, GROUND
    net: str
    pads: Tuple[str, ...]  # pad names, by ordinal

    @property
    def rail_class(self):
        return RAIL_ROLES.get(self.role)


@dataclass(frozen=True)
class DieFolder:
    """What a source reads from one die folder."""

    folder: Path
    name: str
    top_cell: str
    width: float
    height: float
    technology: str
    inventory: Path
    pins: Tuple[DiePin, ...]
    #: Every port, in the order the inventory first names it.
    ports: Tuple[InventoryPort, ...]
    #: The inventory document as parsed. A contract check binds this document,
    #: not one rebuilt from its pin rows.
    inventory_document: Dict[str, Any]
    #: The supply domain each power and ground terminal names, by pad name.
    pad_domains: Mapping[str, str] = field(default_factory=dict)
    #: Each domain's volts, as ``supply_domains`` states them; None when the
    #: contract declares none.
    supply_volts: Optional[Mapping[str, float]] = None
    #: The lower-left corner of ``die.bbox_um``, in the frame the pins are in.
    origin: Tuple[float, float] = (0.0, 0.0)

    def port(self, name: str) -> InventoryPort:
        for port in self.ports:
            if port.name == name:
                return port
        raise KeyError(name)

    def has_port(self, name: str) -> bool:
        return any(p.name == name for p in self.ports)

    def supply(self, pad: str) -> Tuple[Optional[str], Optional[float]]:
        """The supply domain ``pad``'s terminal names and the volts the
        contract declares for it, each None when the contract states none."""
        domain = self.pad_domains.get(pad)
        if domain is None or self.supply_volts is None:
            return domain, None
        return domain, self.supply_volts[domain]


def _technology(contract: Dict[str, Any], where: Path) -> str:
    process = contract.get("process")
    if not isinstance(process, dict):
        raise InventoryError(f"{where}: the contract states no process block")
    pdk, stated = process.get("pdk"), process.get("technology")
    mapped = TECHNOLOGY_OF_PDK.get(pdk)
    if stated is not None and mapped is not None and stated != mapped:
        raise InventoryError(
            f"{where}: the contract attests process {pdk!r}, which maps to {mapped!r}, "
            f"and states technology {stated!r}; the document contradicts itself")
    technology = stated if stated is not None else mapped
    if not isinstance(technology, str) or not technology:
        raise InventoryError(
            f"{where}: process {pdk!r} maps to no technology "
            f"({', '.join(sorted(TECHNOLOGY_OF_PDK))} do) and the contract states no "
            "process.technology")
    return technology


def _ports(inventory: Path, rows: Any) -> Tuple[InventoryPort, ...]:
    if not isinstance(rows, list):
        raise InventoryError(f"{inventory}: the inventory states no pins list")
    any_port = any(isinstance(row, dict) and isinstance(row.get("port"), str) and row.get("port")
                   for row in rows)
    lanes: Dict[str, List[Tuple[int, str]]] = {}
    stated: Dict[str, Tuple[str, str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise InventoryError(f"{inventory}: a pad row is not an object")
        name, port, ordinal = row.get("name"), row.get("port"), row.get("ordinal")
        if not isinstance(name, str) or not name:
            raise InventoryError(f"{inventory}: a pad row carries no name")
        if not isinstance(port, str) or not port:
            if any_port:
                raise UnportedPad(
                    f"{inventory}: pad {name!r} serves no port; a source wires the ports "
                    "its dies state")
            raise UnportedPad(
                f"{inventory}: the die states no ports; a source wires the ports "
                "its dies state")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise InventoryError(
                f"{inventory}: pad {name!r} carries no ordinal, so its place in {port!r} "
                "cannot be stated")
        facts = (str(row.get("direction", "")).upper(), str(row.get("role", "")).upper(),
                 str(row.get("net", "")))
        if stated.setdefault(port, facts) != facts:
            raise InventoryError(
                f"{inventory}: port {port!r} is stated as {stated[port]} and as {facts}")
        lanes.setdefault(port, []).append((ordinal, name))
    out = []
    for port, pads in lanes.items():
        ordinals = [o for o, _ in pads]
        if len(set(ordinals)) != len(ordinals):
            raise InventoryError(f"{inventory}: port {port!r} repeats an ordinal")
        direction, role, net = stated[port]
        out.append(InventoryPort(port, direction, role, net,
                                 tuple(name for _, name in sorted(pads))))
    return tuple(out)


def _supply(contract: Dict[str, Any], where: Path
            ) -> Tuple[Dict[str, str], Optional[Dict[str, float]]]:
    """The domain of each power and ground pad, and the volts per domain the
    contract declares (None when it declares none), read by admission's own
    rules (:mod:`supply_domains`). A contract stamped below 1.2 carrying them
    is refused, as admission refuses it."""
    stamped = _parse_contract_version(contract.get("version"))
    if contract.get("supply_domains") is not None and stamped is not None and stamped < (1, 2):
        raise InventoryError(f"{where}: supply_domains is a die contract 1.2 key, and this "
                             f"contract is {contract.get('version')!r}")
    try:
        return read_supply_domains(contract, str(where),
                                   above_floor=stamped is not None and stamped > (1, 2))
    except SupplyDomainError as exc:
        raise InventoryError(str(exc)) from exc


def read_die_folder(folder: Path) -> DieFolder:
    """The die facts and ports of ``folder``, or :class:`InventoryError`."""
    folder = Path(folder)
    where = folder / CONTRACT
    try:
        contract = json.loads(where.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InventoryError(f"{where}: cannot be read: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise InventoryError(f"{where}: not valid JSON ({exc})") from exc
    if not isinstance(contract, dict) or not isinstance(contract.get("die"), dict):
        raise InventoryError(f"{where}: not a die contract (no die block)")
    try:
        check_contract_version(contract.get("version"), ACCEPTED_DIE_CONTRACT_VERSIONS,
                               name=CONTRACT)
    except ContractVersionError as exc:
        raise InventoryError(f"{folder}: {exc}") from exc
    die = contract["die"]
    top_cell = die.get("top_cell")
    if not isinstance(top_cell, str) or not top_cell:
        raise InventoryError(f"{where}: the contract states no die.top_cell")
    box = die.get("bbox_um")
    if not (isinstance(box, list) and len(box) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)
            and box[2] > box[0] and box[3] > box[1]):
        raise InventoryError(f"{where}: die.bbox_um is not [x0, y0, x1, y1] with extent")
    technology = _technology(contract, where)
    binding = contract.get("inventory")
    rel = binding.get("path") if isinstance(binding, dict) else None
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise InventoryError(f"{where}: inventory.path is not a path inside the folder")
    inventory = folder / rel
    try:
        document = json.loads(inventory.read_text(encoding="utf-8"))
        rows = document.get("pins")
        pins = load_pins_json(inventory)
    except (OSError, ValueError, AttributeError) as exc:
        raise InventoryError(f"{inventory}: the inventory the contract binds cannot be "
                             f"read: {exc}") from exc
    name = die.get("name") if isinstance(die.get("name"), str) and die.get("name") else top_cell
    pad_domains, supply_volts = _supply(contract, where)
    return DieFolder(folder=folder, name=name, top_cell=top_cell,
                     width=float(box[2] - box[0]), height=float(box[3] - box[1]),
                     technology=technology, inventory=inventory, pins=pins,
                     ports=_ports(inventory, rows), inventory_document=document,
                     pad_domains=pad_domains, supply_volts=supply_volts,
                     origin=(float(box[0]), float(box[1])))
