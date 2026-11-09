# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The pin list's version and the words it shares with the die contract.

A built die's pin list (``pins.json``, ``schemas/pins.schema.json``) is written
beside its die contract and read by whatever binds, wires or simulates the die.
Each pin carries a role and a direction in the die view's authored vocabulary;
the contract spells the same two in its own closed vocabulary. Both sides read
the version and the three tables from here: the DEF ``USE`` the die view
expects for each role, and the role and direction each authored word becomes
in the contract.
"""
from __future__ import annotations

__all__ = [
    "PINS_VERSION",
    "USE_BY_ROLE",
    "ROLE",
    "DIRECTION",
]

#: The pins.json format; a reader checks the major.
PINS_VERSION = "1.0"
#: The DEF ``USE`` the die view expects for each role.
USE_BY_ROLE = {"POWER": "POWER", "GROUND": "GROUND", "SIGNAL": "SIGNAL"}
# The view's authored vocabulary on the left, the contract's closed one on the
# right. A value outside this table is a refusal: the contract's role and
# direction are closed sets, and guessing one is exactly the silent step the
# format exists to remove.
ROLE = {"SIGNAL": "signal", "POWER": "power", "GROUND": "ground"}
DIRECTION = {"INPUT": "input", "OUTPUT": "output", "INOUT": "bidir"}
