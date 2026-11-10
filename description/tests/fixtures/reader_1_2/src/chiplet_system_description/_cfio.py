# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The vendored ``chiplet_format_io``, imported under this package's own name.

The copy lives at ``chiplet_system_description/_vendor/chiplet_format_io`` and is reached
by a relative import. It is never put on ``sys.path`` and never answers the
public name ``chiplet_format_io``, which belongs to the installed spec
reference alone. A process that imports this package and the reference gets two
whole readers, not one package assembled from two trees, and nothing here
depends on import order. The copy ships in this package's wheel, so the wheel
installs and reads on its own, without the reference distribution. What is
vendored is byte identical to the reference, which
``tests/test_vendored_identity.py`` keeps true; objects it returns are this
copy's types, not the reference's.

``die_contract.py`` is not vendored on purpose. It finds its schemas inside its
own package directory (the wheel layout) or through the spec's
``reference/python`` layout; the vendored location offers neither, and a schema
file inside the vendored package is a non-Python file the die flow's native
snapshot refuses. This package never reads a die contract; the identity test
pins the exclusion.
"""
from __future__ import annotations

from pathlib import Path

from ._vendor import chiplet_format_io as cfio

VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"

__all__ = ["cfio", "VENDOR_DIR"]
