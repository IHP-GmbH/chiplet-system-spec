# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The installed package set: what pyproject.toml ships, and what it never reaches.

tests/fixtures/reader_1_2 holds a frozen copy of this package, with its own
vendored reader, that a test imports to read format 1.2. The ADK image checks
every vendored reader it stages against the reference and exempts that one by
its path, on the understanding that no install of this package carries it.
These cases make that understanding a check rather than a fact of the layout.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import List

DESCRIPTION = Path(__file__).resolve().parents[1]
PYPROJECT = DESCRIPTION / "pyproject.toml"
FROZEN_READER = DESCRIPTION / "tests" / "fixtures" / "reader_1_2"


def _setuptools() -> dict:
    return tomllib.loads(PYPROJECT.read_text())["tool"]["setuptools"]


def _package_dirs(root: Path) -> List[Path]:
    """Every directory setuptools' find_packages() returns below ``root``:
    those with an ``__init__.py``, reached through directories that have one."""
    found = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "__init__.py").is_file():
            found += [child, *_package_dirs(child)]
    return found


def test_packages_are_found_only_below_src():
    # A packages list, a package-dir map or include patterns would each be a
    # second way in that the find table below does not show.
    assert sorted(_setuptools()) == ["packages"]
    assert _setuptools()["packages"] == {"find": {"where": ["src"]}}


def test_no_found_package_lies_in_the_frozen_reader():
    roots = [DESCRIPTION / where for where in _setuptools()["packages"]["find"]["where"]]
    assert all(not FROZEN_READER.is_relative_to(root) for root in roots)
    found = [path for root in roots for path in _package_dirs(root)]
    assert found and all(not path.is_relative_to(FROZEN_READER) for path in found)
    # The frozen copy is the same package by name, so a mistake would ship it
    # under the live one's import path.
    assert (FROZEN_READER / "src" / "chiplet_system_description" / "__init__.py").is_file()
