# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Routed geometry structure and shared version policy."""
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference/python"))
import chiplet_format_io as cfio
from chiplet_format_io.routed_geometry import (
    RoutedGeometryError, load_routed_geometry, routed_geometry_schema)


@pytest.fixture
def document():
    file = {"path": "input.json", "sha256": "a" * 64}
    return {"schema": "routed-geometry", "version": "1.0",
            "frame": "interposer_local_y_up_um", "dbu_um": 0.001,
            "stack": [{"metal": "M1", "cut_above": None}],
            "outline": [[0, 0], [10, 0], [10, 10], [0, 10]],
            "interposer": {"body_thickness_um": 100},
            "devices": [{"ref": "U1", "layout": file, "origin": [1, 2],
                         "rotation_deg": 90, "mirrored": True, "pad_domains": {}}],
            "nets": [{"name": "signal", "board_name": "signal", "segments": [],
                      "vias": [], "drawn": []}],
            "inputs": {"traces": file, "chiplet": file, "bumps": file,
                       "pins": {"U1": file}, "routing_tech": {"sha256": "b" * 64},
                       "tech_package": {"sha256": "c" * 64},
                       "physical_stack": {"sha256": "d" * 64}},
            "producer": {"tool": "router", "version": "1.0"}}


def test_schema_and_read(document, tmp_path):
    schema = routed_geometry_schema()
    Draft202012Validator.check_schema(schema)
    assert schema == json.loads((ROOT / "schemas/routed_geometry.schema.json").read_text())
    path = tmp_path / "geometry.json"
    path.write_text(json.dumps(document))
    assert load_routed_geometry(path) == document
    assert "order" in schema["description"]


@pytest.mark.parametrize("mutation", ["drawn", "devices", "size", "pitch", "frame"])
def test_missing_or_invalid_geometry_refused(document, mutation, tmp_path):
    if mutation == "drawn":
        del document["nets"][0]["drawn"]
    elif mutation == "devices":
        document["devices"] = []
    elif mutation == "size":
        document["nets"][0]["vias"] = [{"via": "V1", "x": 1, "y": 1,
                                       "bottom_layer": "M1", "top_layer": "M2"}]
    elif mutation == "pitch":
        document["nets"][0]["drawn"] = [{"via": "V1", "bottom_layer": "M1",
            "top_layer": "M2", "x": 1, "y": 1, "pad": [1, 1], "k": 1,
            "cut": 0.5, "pitch": 1, "enclosure": 0.25}]
    else:
        document["frame"] = "board_absolute_ynegated_um"
    path = tmp_path / "geometry.json"
    path.write_text(json.dumps(document))
    with pytest.raises(RoutedGeometryError):
        load_routed_geometry(path)


def test_newer_minor_warns_once(document, tmp_path):
    cfio._reset_version_warnings()
    document["version"] = "1.1"
    document["future"] = True
    path = tmp_path / "geometry.json"
    path.write_text(json.dumps(document))
    events = []
    with pytest.warns(cfio.ContractVersionWarning) as warnings:
        assert load_routed_geometry(path, on_warn=events.append) == document
    assert len(warnings) == len(events) == 1
    cfio._reset_version_warnings()


def test_other_major_refused(document, tmp_path):
    document["version"] = "2.0"
    path = tmp_path / "geometry.json"
    path.write_text(json.dumps(document))
    with pytest.raises(cfio.ContractVersionError):
        load_routed_geometry(path)
