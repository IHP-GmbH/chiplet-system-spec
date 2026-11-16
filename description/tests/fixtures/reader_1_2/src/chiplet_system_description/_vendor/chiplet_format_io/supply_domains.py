# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The supply domains of a die contract (1.2): which domain each power and
ground terminal names, and the volts the contract declares per domain.

Admission and a raw reader of the contract (the system description's
inventory) read the same rules here, so the two cannot drift apart. The rules
span keys, so the schema cannot hold them: when ``supply_domains`` is present
its keys are exactly the domains the power and ground terminals name, no
domain is named by terminals of both roles, a ground domain is 0 and a power
domain is above 0. Absent, nothing is declared and nothing is checked. Which
contract version may carry the key is the reader's to say, not this module's.

A contract above the reader's minor may carry a terminal role the reader does
not know, naming its own domain, and that domain then appears among the keys:
read element by element, a key no known terminal names is not used and not
refused, since refusing it would refuse the whole document for what refuses
one terminal at most.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set, Tuple

__all__ = [
    "SupplyDomainError",
    "read_supply_domains",
]

#: The terminal roles a supply domain belongs to.
SUPPLY_ROLES = ("power", "ground")


class SupplyDomainError(ValueError):
    """A ``supply_domains`` its terminals contradict, or not of its shape."""


def read_supply_domains(data: Dict[str, Any], where: str, *, above_floor: bool = False
                        ) -> Tuple[Dict[str, str], Optional[Dict[str, float]]]:
    """The domain each named power and ground terminal names, by terminal
    name, and the volts per domain ``supply_domains`` declares (None when the
    contract declares none), or :class:`SupplyDomainError`. Only a terminal
    whose role is power or ground names a domain here; ``terminals`` that is
    not a list names none. ``above_floor`` is a contract read element by
    element, whose keys may include domains no known terminal names."""
    by_terminal: Dict[str, str] = {}
    roles: Dict[str, Set[str]] = {}
    terminals = data.get("terminals")
    for term in terminals if isinstance(terminals, list) else ():
        if (isinstance(term, dict) and term.get("role") in SUPPLY_ROLES
                and isinstance(term.get("supply_domain"), str)):
            roles.setdefault(term["supply_domain"], set()).add(term["role"])
            if isinstance(term.get("name"), str):
                by_terminal[term["name"]] = term["supply_domain"]
    declared = data.get("supply_domains")
    if declared is None:
        return by_terminal, None
    if not (isinstance(declared, dict) and all(
            isinstance(entry, dict) and isinstance(entry.get("volts"), (int, float))
            and not isinstance(entry.get("volts"), bool) for entry in declared.values())):
        raise SupplyDomainError(f"{where}: supply_domains is not {{domain: {{volts: number}}}}")
    named, keys = set(roles), set(declared)
    if named != keys and not (above_floor and named < keys):
        parts = []
        if named - keys:
            parts.append(f"no volts for {', '.join(sorted(named - keys))}")
        if keys - named:
            parts.append(f"{', '.join(sorted(keys - named))} named by no power or ground "
                         "terminal")
        raise SupplyDomainError(f"{where}: supply_domains does not match the terminals' "
                                f"domains: {'; '.join(parts)}")
    for domain in sorted(roles):
        volts = declared[domain]["volts"]
        if roles[domain] == set(SUPPLY_ROLES):
            raise SupplyDomainError(f"{where}: supply domain {domain!r} is named by both a "
                                    "power and a ground terminal")
        if roles[domain] == {"ground"} and volts != 0:
            raise SupplyDomainError(f"{where}: supply domain {domain!r} is a ground domain "
                                    f"at {volts!r} volts; a ground domain is 0")
        if roles[domain] == {"power"} and not volts > 0:
            raise SupplyDomainError(f"{where}: supply domain {domain!r} is a power domain "
                                    f"at {volts!r} volts; a power domain is above 0")
    return by_terminal, {domain: entry["volts"] for domain, entry in declared.items()}
