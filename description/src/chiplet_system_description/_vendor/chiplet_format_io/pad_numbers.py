# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Symbol pin numbers from a die contract, and from nothing else.

The handout is drawn before any sheet exists, so the number a pad gets on its
symbol is decided here, from the contract's ``terminals``. A port's pads share
their pad name as one number, repeated names included (a ground of thirteen
pads is one pin). A repeated pad name that is no port is ``<name>_<k>``, k
from 1 in ``pad_index`` order, and is no-connect. A unique non-port pad keeps
its name and is no-connect.

What cannot be numbered is :class:`DieContractError`, not a guessed number:
no port identity on any terminal (:func:`~.die_contract.no_port_identity`,
the sentence :func:`~.die_contract.port_terminals` uses), a port identity this
reader cannot read, one pad name that is a port and not a port, one pad name
on two ports, or a generated ``<name>_<k>`` that is another terminal's number.
Terminals are read through die_contract's own readers.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple, Union

from .die_contract import (
    UNREADABLE_PORT,
    BoundContract,
    DieContractError,
    _port_identity,
    _terminal_identity,
    _unreadable_port,
    no_port_identity,
)

__all__ = ["PadNumber", "pad_numbers"]


class PadNumber(NamedTuple):
    """One pad's symbol pin.

    ``pad_index`` is the terminal's own, so a caller pairs a number with its
    pad without walking the terminals again or sorting to line the two up.
    ``port`` and ``ordinal`` are that terminal's port identity, both or
    neither, which is the pair an inventory pin is held to. ``is_port`` and
    ``no_connect`` follow ``port``.
    """

    number: str
    name: str
    pad_index: int
    port: Optional[str]
    ordinal: Optional[int]

    @property
    def is_port(self) -> bool:
        return self.port is not None

    @property
    def no_connect(self) -> bool:
        return self.port is None


class _Row(NamedTuple):
    index: int
    name: str
    pad_index: int
    port: Optional[str]
    ordinal: Optional[int]


def _document(contract: Union[BoundContract, Mapping[str, Any]]):
    """The terminals a reader of this minor knows, and the declared version."""
    if isinstance(contract, BoundContract):
        return list(contract.terminals), contract.contract.get("version")
    if isinstance(contract, Mapping):
        return contract.get("terminals"), contract.get("version")
    return None, None


def _rows(contract: Union[BoundContract, Mapping[str, Any]]) -> Tuple[List[_Row], Any]:
    terminals, version = _document(contract)
    if not isinstance(terminals, list):
        raise DieContractError("pad numbers are read from the contract's terminals")
    parsed: List[Tuple[int, Dict[str, Any], str, int]] = []
    for i, term in enumerate(terminals):
        if not isinstance(term, dict):
            raise DieContractError(f"terminals[{i}] has no usable name")
        name, pad_index = _terminal_identity(term, f"terminals[{i}]")
        parsed.append((i, term, name, pad_index))
    unread = [(i, term, name) for i, term, name, _ in parsed
              if _port_identity(term) is UNREADABLE_PORT]
    if unread:
        i, term, name = unread[0]
        raise DieContractError(
            f"terminal {name!r} has a port identity this reader cannot read "
            f"({_unreadable_port(i, term).pointer})")
    rows = []
    for i, term, name, pad_index in parsed:
        identity = _port_identity(term)
        port, ordinal = (None, None) if identity is None else identity
        rows.append(_Row(i, name, pad_index, port, ordinal))
    return rows, version


def pad_numbers(contract: Union[BoundContract, Mapping[str, Any]]) -> Tuple[PadNumber, ...]:
    """The symbol pin of each terminal, in ``pad_index`` order.

    ``contract`` is a die-contract document or a :class:`BoundContract`. No
    inventory, path or schema is read.
    """
    rows, version = _rows(contract)
    if not any(row.port is not None for row in rows):
        raise DieContractError(no_port_identity(version))
    groups: Dict[str, List[_Row]] = {}
    for row in rows:
        groups.setdefault(row.name, []).append(row)
    assigned: Dict[int, PadNumber] = {}
    generated = []
    for name, group in groups.items():
        ordered = sorted(group, key=lambda row: (row.pad_index, row.index))
        ports = {row.port for row in ordered}
        if None in ports and len(ports) > 1:
            raise DieContractError(
                f"pad {name!r} carries a port terminal and a terminal with no port")
        if None not in ports and len(ports) > 1:
            named = ", ".join(sorted(port for port in ports if port is not None))
            raise DieContractError(
                f"pad {name!r} is on more than one port ({named})")
        if None not in ports:
            for row in ordered:
                assigned[row.index] = PadNumber(
                    name, row.name, row.pad_index, row.port, row.ordinal)
            continue
        if len(ordered) > 1:
            for k, row in enumerate(ordered, start=1):
                number = f"{name}_{k}"
                assigned[row.index] = PadNumber(
                    number, row.name, row.pad_index, None, None)
                generated.append(row.index)
            continue
        row = ordered[0]
        assigned[row.index] = PadNumber(name, row.name, row.pad_index, None, None)
    by_number: Dict[str, List[int]] = {}
    for index, pin in assigned.items():
        by_number.setdefault(pin.number, []).append(index)
    for index in generated:
        number = assigned[index].number
        if len(by_number[number]) > 1:
            raise DieContractError(
                f"terminal {assigned[index].name!r} would be numbered {number}, "
                "which is another terminal's number")
    return tuple(assigned[row.index] for row in sorted(rows, key=lambda r: (r.pad_index, r.index)))
