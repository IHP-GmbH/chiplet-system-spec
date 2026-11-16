# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Which technology each PDK is: the one join the die side and the assembly side
both read.

Two namespaces name a die's process and nothing else makes them agree: the
system names a TECHNOLOGY on the die (``sg13g2``, the id the assembly document
keys its technologies by) and a build names a PDK (``ihp-sg13g2``, what
LibreLane resolves from the die config and what a die contract attests in its
``process.pdk``). :data:`TECHNOLOGY_OF_PDK` joins the two. A flow reads it
before a build is dispatched, so a die declared in one process is not built in
another and then extracted with the declared process's pad layers (an empty pad
map, not an error); the admission of a ready die reads it as the cross-check on
a contract's own ``process.technology``, and as the join when the contract
states none.

A PDK the table does not map refuses a build, and refuses a ready die whose
contract does not state its technology: an unmappable PDK cannot be confirmed
to agree with the declaration, and choosing on the strength of the declaration
alone is the silent case this table exists to remove.
"""
from __future__ import annotations

from typing import Dict

__all__ = [
    "TECHNOLOGY_OF_PDK",
]

TECHNOLOGY_OF_PDK: Dict[str, str] = {
    "ihp-sg13g2": "sg13g2",
    "ihp-sg13cmos5l": "sg13cmos5l",
    "gf180mcuD": "gf180mcu",
}
