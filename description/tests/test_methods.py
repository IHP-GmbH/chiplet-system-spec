# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Method-constants identity against the interconnect_pdk manifest. Answer key:
interconnect_pdk/manifest/interconnect_methods.json (pitch_rules.IXN_pitch). Skip if the sibling is absent; strict-live enforces."""
from __future__ import annotations

import json

import pytest

from chiplet_system_description import (
    CUPILLAR_OPT1,
    CUPILLAR_OPT2,
    CUPILLAR_OPT3,
    INTM4TM2,
    SG13G2,
    InterconnectMethod,
    SBUMP_SAC305,
    SystemSpecError,
)
from tests.conftest import require_sibling

SHIPPED = (CUPILLAR_OPT1, CUPILLAR_OPT2, CUPILLAR_OPT3, SBUMP_SAC305)


def _manifest_doc() -> dict:
    manifest = require_sibling("interconnect_pdk", "manifest/interconnect_methods.json")
    return json.loads(manifest.read_text(encoding="utf-8"))


@pytest.mark.needs_sibling_sources
def test_each_shipped_constant_matches_manifest():
    methods = _manifest_doc()["methods"]
    for m in SHIPPED:
        assert m.id in methods, f"{m.id!r} is not a manifest method"
        assert m.pitch_um == float(methods[m.id]["pitch_rules"]["IXN_pitch"])
        # Contract 1.1: the processes a method attaches, transcribed like the pitch.
        assert m.die_processes == tuple(methods[m.id]["die_processes"]), m.id


def test_shipped_die_processes_are_shipped_technology_ids():
    """The join is by Technology.id. A process a shipped method states that no
    shipped Technology carries would refuse every die at build() with a message
    that reads like a design error, so the two id spaces are held together here."""
    shipped_ids = {SG13G2.id, INTM4TM2.id}
    for m in SHIPPED:
        assert m.die_processes is not None, f"{m.id!r} states no die process"
        assert set(m.die_processes) <= shipped_ids, (m.id, m.die_processes)


@pytest.mark.needs_sibling_sources
def test_shipped_ids_are_exactly_the_default_connection_library_minus_demo():
    """The four shipped constants are the manifest's default_connection_library;
    the demo vendorx_microbump entry is deliberately not shipped."""
    doc = _manifest_doc()
    assert {m.id for m in SHIPPED} == set(doc["default_connection_library"])
    assert all(m.id != "vendorx_microbump" for m in SHIPPED)


def test_constants_reject_bad_values():
    with pytest.raises(SystemSpecError):
        InterconnectMethod("", pitch_um=75.0)
    with pytest.raises(SystemSpecError):
        InterconnectMethod("x", pitch_um=0.0)
    # None is the only spelling of "not stated": an empty statement, a repeat
    # and a blank id are refused at construction, like the empty method id.
    for bad in ((), ("sg13g2", "sg13g2"), ("",)):
        with pytest.raises(SystemSpecError):
            InterconnectMethod("x", pitch_um=75.0, die_processes=bad)


# --- prove-it-can-fail: a perturbed constant must fail the manifest comparison ---
@pytest.mark.needs_sibling_sources
def test_seeded_mutant_perturbed_constant_fails_identity():
    methods = _manifest_doc()["methods"]
    perturbed = InterconnectMethod("cupillar_opt1", pitch_um=CUPILLAR_OPT1.pitch_um + 1.0)
    assert perturbed.pitch_um != float(methods["cupillar_opt1"]["pitch_rules"]["IXN_pitch"])
    wrong_process = InterconnectMethod("cupillar_opt1", pitch_um=CUPILLAR_OPT1.pitch_um,
                                       die_processes=(INTM4TM2.id,))
    assert wrong_process.die_processes != tuple(methods["cupillar_opt1"]["die_processes"])
