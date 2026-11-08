# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The examples mount their dies on the plane the interposer PDK publishes.

Both read ``beol.attachment_surface_z_um`` from
``$INTERPOSER_PDK_ROOT/libs.tech/intm4tm2_stack.json`` and refuse to build
without it; there is no fallback value. The interposer carries the plane and each
die sits on the plane plus its connection stack, summed in decimal. The golden
pins the plane of the vendored descriptor; these tests pin that the value comes
from whatever descriptor the variable names.
"""
from __future__ import annotations

import importlib
import sys

import pytest

from chiplet_system_description import elaborate
from tests.conftest import INTERPOSER_PDK_FIXTURE, PACKAGE_ROOT

EXAMPLES = ["two_die_ihp", "two_die_typed"]
DESCRIPTOR = INTERPOSER_PDK_FIXTURE / "libs.tech" / "intm4tm2_stack.json"
#: Binary float addition gives 10.7103 + 44 as 54.710300000000004; the decimal sum is 54.7103.
OTHER_PLANE = "10.7103"


def example(name):
    sys.path.insert(0, str(PACKAGE_ROOT))
    return importlib.import_module(f"examples.{name}")


def pdk_root(tmp_path, plane):
    """A PDK root whose descriptor is the vendored one with ``plane`` as the key's value."""
    text = DESCRIPTOR.read_text(encoding="utf-8")
    needle = '"attachment_surface_z_um": 10.8103,'
    assert text.count(needle) == 1
    (tmp_path / "libs.tech").mkdir()
    (tmp_path / "libs.tech" / "intm4tm2_stack.json").write_text(
        text.replace(needle, f'"attachment_surface_z_um": {plane},'), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("name", EXAMPLES)
def test_an_example_refuses_without_the_interposer_pdk(name, monkeypatch):
    monkeypatch.delenv("INTERPOSER_PDK_ROOT", raising=False)
    module = example(name)
    with pytest.raises(module.AttachPlaneUnavailable, match="INTERPOSER_PDK_ROOT"):
        module.build_system()


@pytest.mark.parametrize("name", EXAMPLES)
def test_an_example_refuses_a_root_without_the_key(name, tmp_path, monkeypatch):
    monkeypatch.setenv("INTERPOSER_PDK_ROOT", str(tmp_path))
    module = example(name)
    with pytest.raises(module.AttachPlaneUnavailable, match="attachment_surface_z_um"):
        module.build_system()


@pytest.mark.parametrize("name", EXAMPLES)
def test_the_plane_is_the_descriptors(name, tmp_path, monkeypatch):
    monkeypatch.setenv("INTERPOSER_PDK_ROOT", str(pdk_root(tmp_path, OTHER_PLANE)))
    doc = elaborate(example(name).build_system())
    by_id = {c["id"]: c for c in doc["components"]}
    assert by_id["interposer"]["dimensions"]["thickness"] == 10.7103
    assert [by_id[u]["position"]["z"] for u in ("U1", "U2")] == [54.7103, 54.7103]
