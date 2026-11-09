# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Tier 1 typed golden equivalence: the typed example elaborates to a document
semantically identical to the golden under the SAME projection function as the
untyped one (reused, not reimplemented), with the zero-warning cross-load and
the prove-it-can-fail mutants. Tier 2 typed route parity is strict-live."""
from __future__ import annotations

import dataclasses

import pytest

from chiplet_system_description import Position, elaborate, emit
from chiplet_system_description._cfio import cfio
from tests import conftest
from tests.conftest import (
    GOLDEN_CHIPLET,
    PACKAGE_ROOT,
    require_interposer_pnr,
    route_report,
)

# Reuse the golden answer-key machinery verbatim (do not reimplement).
from tests.test_elaborate_golden import _floats_close, _project
from tests.test_route_parity import _parity_tuple


# --- Tier 1: typed example equals the golden under the golden projection ---
def test_typed_elaboration_equals_golden(built_typed_system):
    golden = cfio.load(str(GOLDEN_CHIPLET))
    mine = elaborate(built_typed_system)
    assert _floats_close(_project(mine), _project(golden))


def test_typed_emit_roundtrips_through_shared_loader(built_typed_system, tmp_path):
    out = emit(built_typed_system, tmp_path / "two_die_typed.chiplet")
    assert out.exists()
    reloaded = cfio.load(str(out))
    golden = cfio.load(str(GOLDEN_CHIPLET))
    assert _floats_close(_project(reloaded), _project(golden))


@pytest.mark.needs_sibling_sources
def test_typed_emitted_loads_zero_warnings_under_ir(built_typed_system, tmp_path):
    require_interposer_pnr()
    from interposer_pnr import ir

    out = emit(built_typed_system, tmp_path / "two_die_typed.chiplet")
    asm = ir.load_assembly(str(out))
    assert asm.warnings == []


# --- prove-it-can-fail: the typed-equivalence oracle must reject two mutants ---
def test_typed_mutant_shifted_position_fails_projection(built_typed_system):
    golden = _project(cfio.load(str(GOLDEN_CHIPLET)))
    inst = built_typed_system.instances[1]  # U2, shifted +100 um
    shifted = dataclasses.replace(
        inst, position=Position(inst.position.x + 100.0, inst.position.y, inst.position.z)
    )
    mutant = dataclasses.replace(
        built_typed_system, instances=(built_typed_system.instances[0], shifted)
    )
    assert not _floats_close(_project(elaborate(mutant)), golden)


def test_typed_mutant_dropped_net_fails_projection(built_typed_system):
    golden = _project(cfio.load(str(GOLDEN_CHIPLET)))
    doc = elaborate(built_typed_system)
    doc["netlist"]["nets"] = [n for n in doc["netlist"]["nets"] if n["name"] != "sig3"]
    assert not _floats_close(_project(doc), golden)


# --- Tier 2 (strict-live): typed route parity against the golden run ---
@pytest.mark.live_openroad
def test_typed_route_parity_matches_golden(built_typed_system, golden_route_report):
    emitted = emit(built_typed_system, PACKAGE_ROOT / "build" / "two_die_typed.chiplet")
    mine = route_report(emitted, conftest.PACKAGE_ROOT / "build" / "parity" / "typed")
    assert _parity_tuple(mine) == _parity_tuple(golden_route_report)
