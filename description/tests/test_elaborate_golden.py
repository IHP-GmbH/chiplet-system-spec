# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Tier 1 golden equivalence, the zero-warning cross-load, the
write-path lint, and the prove-it-can-fail mutants."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from chiplet_system_description import Position, elaborate, emit
from chiplet_system_description._cfio import cfio
from tests.conftest import (
    GOLDEN_CHIPLET,
    PACKAGE_ROOT,
    require_interposer_pnr,
)

FLOAT_TOL = 1e-6


def _project(doc: dict) -> dict:
    """The canonical projection: exactly the fields the route path consumes."""
    comps = {}
    for c in doc["components"]:
        pos = c.get("position") or {}
        dim = c.get("dimensions") or {}
        rot = c.get("rotation") or {}
        comps[c["id"]] = {
            "type": c.get("type"),
            "technology": c.get("technology"),
            "anchor": c.get("anchor"),
            "orientation": c.get("orientation"),
            "connection": c.get("connection"),
            "top_cell": c.get("top_cell"),
            "attachment_surface_z": c.get("attachment_surface_z"),
            "position": tuple(float(pos.get(k, 0.0)) for k in ("x", "y", "z")),
            "dimensions": tuple(
                float(dim.get(k, 0.0)) for k in ("width", "height", "thickness")
            ),
            "rotation_z": float(rot.get("z", 0.0)),
            "io_pads": [
                (
                    p["id"], p["net"], p.get("io_class"), p.get("layer"),
                    float(p["position"]["x"]), float(p["position"]["y"]),
                    float(p["size"]["x"]), float(p["size"]["y"]),
                )
                for p in c.get("io_pads", [])
            ],
        }
    nets = {}
    for n in doc["netlist"]["nets"]:
        nets[n["name"]] = (
            n.get("class", "signal"),
            tuple((x["component"], x["pin"], x["layer"]) for x in n["connections"]),
        )
    return {
        "units": doc["assembly"]["units"],
        "dbu": {k: float(v["dbu"]) for k, v in doc["technologies"].items()},
        "components": comps,
        "nets": nets,
    }


def _floats_close(a, b) -> bool:
    """Structural equality with float tolerance FLOAT_TOL."""
    if isinstance(a, float) or isinstance(b, float):
        return abs(float(a) - float(b)) <= FLOAT_TOL
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_floats_close(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_floats_close(x, y) for x, y in zip(a, b))
    return a == b


def test_elaboration_equals_golden(built_system):
    golden = cfio.load(str(GOLDEN_CHIPLET))
    mine = elaborate(built_system)
    assert _floats_close(_project(mine), _project(golden))


def test_emit_roundtrips_through_shared_loader(built_system, tmp_path):
    out = emit(built_system, tmp_path / "two_die.chiplet")
    assert out.exists()
    reloaded = cfio.load(str(out))
    golden = cfio.load(str(GOLDEN_CHIPLET))
    assert _floats_close(_project(reloaded), _project(golden))


@pytest.mark.needs_sibling_sources
def test_emitted_file_loads_zero_warnings_under_ir(built_system, tmp_path):
    """Beside the golden equivalence: the parity-bound reader accepts the
    emitted file with zero warnings (skip if the sibling is absent)."""
    require_interposer_pnr()
    from interposer_pnr import ir

    out = emit(built_system, tmp_path / "two_die.chiplet")
    asm = ir.load_assembly(str(out))
    assert asm.warnings == []


@pytest.mark.needs_sibling_sources
def test_seeded_mutant_stripped_anchor_warns_under_ir(built_system, tmp_path):
    """Prove-it-can-fail for the zero-warning oracle (RAIL-4): a malformed but
    still-loadable emission (a component with its anchor removed) must produce a
    non-empty ir warning set, so the cross-load actually catches a bad emit."""
    require_interposer_pnr()
    from interposer_pnr import ir

    doc = elaborate(built_system)
    for comp in doc["components"]:
        if comp["id"] == "U1":
            del comp["anchor"]  # ir defaults to bbox_center AND warns
    out = tmp_path / "mutant_no_anchor.chiplet"
    cfio.dump(doc, str(out))
    asm = ir.load_assembly(str(out))
    assert asm.warnings != []


#: The one tree the lint exempts: the byte-identity gate covers it, nothing else under _vendor.
VENDORED_CFIO = (PACKAGE_ROOT / "src" / "chiplet_system_description" / "_vendor"
                 / "chiplet_format_io")


def test_write_path_lint_no_raw_yaml_dump_outside_vendor():
    """The write-side of the raw-load lint: no yaml.safe_dump / yaml.dump
    anywhere in src/ or examples/ outside the vendored copy (the only writer is
    cfio.dump)."""
    offenders = []
    for base in ("src", "examples"):
        for path in (PACKAGE_ROOT / base).rglob("*.py"):
            if path.is_relative_to(VENDORED_CFIO):
                continue
            text = path.read_text(encoding="utf-8")
            if "yaml.safe_dump" in text or "yaml.dump" in text:
                offenders.append(str(path))
    assert not offenders, f"raw yaml dump outside _vendor/: {offenders}"


# --- prove-it-can-fail: the equivalence oracle must reject two mutants ---
def test_mutant_shifted_position_fails_projection(built_system):
    golden = _project(cfio.load(str(GOLDEN_CHIPLET)))
    inst = built_system.instances[1]  # U2
    shifted = dataclasses.replace(
        inst, position=Position(inst.position.x + 100.0, inst.position.y, inst.position.z)
    )
    mutant = dataclasses.replace(
        built_system, instances=(built_system.instances[0], shifted)
    )
    assert not _floats_close(_project(elaborate(mutant)), golden)


def test_mutant_dropped_net_fails_projection(built_system):
    golden = _project(cfio.load(str(GOLDEN_CHIPLET)))
    doc = elaborate(built_system)
    doc["netlist"]["nets"] = [
        n for n in doc["netlist"]["nets"] if n["name"] != "sig3"
    ]
    assert not _floats_close(_project(doc), golden)
