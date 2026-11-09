# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Format 1.4, a die's admission: the file a ``dies`` item may name.

A die the run does not build is placed only on an admission, and from 1.4 its
``dies`` item names the file that states it (``admission``, a ``.json`` path
relative to the source). The file is one JSON object holding ``reason`` and
``scope``, each a non-empty string, and ``not_verified``, a non-empty list of
non-empty strings: the checks the run does not verify for the die. Any other
key is the file's own and is not read. The format has no key for who admits
the die.

Tier 0 holds the key to the path rule (:mod:`.document`); tier 1 reads the file
(:func:`read_admissions`) and the elaborator hands the flow each admitted die's
file. What an admission allows is the flow's to decide; nothing here states a
verdict.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .document import Finding, Source

#: The keys an admission file holds, in the order a finding names them.
ADMISSION_KEYS = ("reason", "scope", "not_verified")


def admission_problem(path: Path, written: str) -> Optional[str]:
    """Why the admission file at ``path`` (``written`` in the source) admits no
    die, or None when it holds the three keys in their shapes."""
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"admission {written}: no such file beside the source"
    except (OSError, ValueError, RecursionError) as exc:
        return f"admission {written} cannot be read as JSON: {exc}"
    if not isinstance(data, dict):
        return f"admission {written} is not a JSON object"
    missing = [key for key in ADMISSION_KEYS if key not in data]
    if missing:
        return f"admission {written}: missing {', '.join(missing)}"
    *texts, listed = ADMISSION_KEYS
    for key in texts:
        if not isinstance(data[key], str) or not data[key].strip():
            return f"admission {written}: {key} is a non-empty string"
    items = data[listed]
    if not isinstance(items, list) or not items or \
            not all(isinstance(item, str) and item.strip() for item in items):
        return (f"admission {written}: {listed} is a non-empty list of non-empty "
                "strings, the checks the run does not verify for the die")
    return None


def read_admissions(source: Source) -> List[Finding]:
    """A finding on each ``dies`` line whose admission file admits no die."""
    findings: List[Finding] = []
    cache: Dict[Path, Optional[str]] = {}
    for die in source.dies:
        if die.admission is None:
            continue
        path = source.folder / die.admission
        if path not in cache:
            cache[path] = admission_problem(path, die.admission)
        if cache[path] is not None:
            findings.append(Finding(die.line, f"dies: {die.id}: {cache[path]}", tier=1))
    return findings
