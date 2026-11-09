# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""lanes_from_pins: deterministic lane selection and its prove-it-can-fail
oracle."""
from __future__ import annotations

import pytest

from chiplet_system_description import DiePin, LaneSelectionError, lanes_from_pins, load_pins_json
from tests.conftest import PINS_JSON

PINS = load_pins_json(PINS_JSON)


# --- positive: the canonical selection reproduces the hand-written tuple ---
def test_canonical_selection_reproduces_a0_a5():
    assert lanes_from_pins(PINS, prefix="A", count=6) == ("A0", "A1", "A2", "A3", "A4", "A5")


def test_canonical_selection_reproduces_b0_b5():
    assert lanes_from_pins(PINS, prefix="B", count=6) == ("B0", "B1", "B2", "B3", "B4", "B5")


def test_selection_orders_by_pad_index_not_file_order():
    pins = (
        DiePin("A2", pad_index=2, x_um=0.0, y_um=0.0),
        DiePin("A0", pad_index=0, x_um=0.0, y_um=0.0),
        DiePin("A1", pad_index=1, x_um=0.0, y_um=0.0),
    )
    assert lanes_from_pins(pins, prefix="A", count=3) == ("A0", "A1", "A2")


# --- refusals: every LaneSelectionError branch ---
def test_count_off_by_one_high_raises():
    with pytest.raises(LaneSelectionError):
        lanes_from_pins(PINS, prefix="A", count=7)  # only 6 A-pins


def test_count_off_by_one_low_raises_no_silent_truncation():
    with pytest.raises(LaneSelectionError):
        lanes_from_pins(PINS, prefix="A", count=5)  # 6 selected, 5 asked


def test_empty_selection_raises():
    with pytest.raises(LaneSelectionError):
        lanes_from_pins(PINS, prefix="Z", count=1)  # no Z-pins


def test_missing_pad_index_raises():
    pins = (
        DiePin("A0", pad_index=0, x_um=0.0, y_um=0.0),
        DiePin("A1", pad_index=None, x_um=0.0, y_um=0.0),  # no pad_index
    )
    with pytest.raises(LaneSelectionError):
        lanes_from_pins(pins, prefix="A", count=2)


def test_repeated_name_raises():
    pins = (
        DiePin("A0", pad_index=0, x_um=0.0, y_um=0.0),
        DiePin("A0", pad_index=1, x_um=0.0, y_um=0.0),  # duplicate name
    )
    with pytest.raises(LaneSelectionError):
        lanes_from_pins(pins, prefix="A", count=2)
