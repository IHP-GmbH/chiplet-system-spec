# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""What an install of this package ships: src alone, never the frozen 1.2 reader.

tests/fixtures/reader_1_2 is a frozen copy of this package under the same
import name, with its own vendored reader, which a test runs to read format
1.2. A build that reached it would ship an old reader under the live one's
name, and a check of vendored copies would see a copy that is meant to differ.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import PACKAGE_ROOT, READER_1_2

# tomllib is 3.11 and later; the suite runs on 3.12, the package itself on 3.10.
tomllib = pytest.importorskip("tomllib", reason="reading pyproject.toml needs Python 3.11")

SRC = PACKAGE_ROOT / "src"


def _setuptools() -> dict:
    return tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["setuptools"]


def test_the_frozen_reader_is_where_these_cases_look():
    assert (READER_1_2 / "src" / "chiplet_system_description" / "__init__.py").is_file()


def test_packages_are_found_in_src_alone():
    # A package-dir map or a packages list beside the find table would be a
    # second way in; so would a setup.py, a setup.cfg or a MANIFEST.in.
    setuptools = _setuptools()
    assert setuptools["packages"] == {"find": {"where": ["src"]}}
    assert "package-dir" not in setuptools
    assert [name for name in ("setup.py", "setup.cfg", "MANIFEST.in")
            if (PACKAGE_ROOT / name).exists()] == []


def test_nothing_in_src_resolves_into_the_frozen_reader():
    # setuptools follows a symlink when it builds, so a link under src would
    # ship whatever it points at.
    assert not READER_1_2.resolve().is_relative_to(SRC.resolve())
    assert [path for path in SRC.rglob("*")
            if path.resolve().is_relative_to(READER_1_2.resolve())] == []
