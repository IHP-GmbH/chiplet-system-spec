# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""load_pins_json: the pins.json contract, its rails, and prove-it-can-fail."""
from __future__ import annotations

import json

import pytest

from chiplet_system_description import load_pins_json
from tests.conftest import PINS_JSON


def test_reads_the_reference_pin_list():
    pins = load_pins_json(PINS_JSON)
    assert len(pins) == 14
    by_name = {p.name for p in pins}
    assert {"A0", "A5", "B0", "B5", "VDD", "GND"} <= by_name
    a0 = next(p for p in pins if p.name == "A0")
    # 200000 dbu * 0.001 um/dbu == 200.0 um
    assert a0.x_um == pytest.approx(200.0)
    assert a0.y_um == pytest.approx(150.0)
    assert a0.pad_index == 0


def test_missing_dbu_um_raises(tmp_path):
    data = json.loads(PINS_JSON.read_text())
    data.pop("dbu_um")
    bad = tmp_path / "no_dbu.pins.json"
    bad.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_pins_json(bad)


def test_pin_without_name_raises(tmp_path):
    data = json.loads(PINS_JSON.read_text())
    data["pins"][0].pop("name")
    bad = tmp_path / "no_name.pins.json"
    bad.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_pins_json(bad)


# --- prove-it-can-fail: the loader oracle catches a corrupted scale ---
def test_seeded_mutant_wrong_dbu_changes_coordinates(tmp_path):
    """A one-field mutation of dbu_um must move the parsed coordinates, proving
    the loader actually consumes the file's own scale rather than guessing."""
    good = load_pins_json(PINS_JSON)
    data = json.loads(PINS_JSON.read_text())
    data["dbu_um"] = 0.002  # doubled scale
    mut = tmp_path / "mut.pins.json"
    mut.write_text(json.dumps(data))
    bad = load_pins_json(mut)
    a_good = next(p for p in good if p.name == "A0")
    a_bad = next(p for p in bad if p.name == "A0")
    assert a_bad.x_um == pytest.approx(2 * a_good.x_um)
