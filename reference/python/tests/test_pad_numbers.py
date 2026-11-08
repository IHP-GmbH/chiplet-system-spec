# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Symbol pin numbers from a die contract's terminals alone."""
from __future__ import annotations

import pytest

from chiplet_format_io.die_contract import DieContractError
from chiplet_format_io.pad_numbers import PadNumber, pad_numbers


def _term(name, index, port=None):
    row = {"name": name, "pad_index": index, "role": "signal"}
    if port is not None:
        row["port"] = port
        row["ordinal"] = index
    return row


def _raises(contract):
    with pytest.raises(DieContractError):
        pad_numbers(contract)


def test_a_ports_repeated_pads_share_one_number():
    contract = {"version": "1.2", "terminals": [_term("GND", i, "GND") for i in (3, 0, 12)]}
    numbered = pad_numbers(contract)
    assert numbered == (
        PadNumber("GND", "GND", 0, "GND", 0),
        PadNumber("GND", "GND", 3, "GND", 3),
        PadNumber("GND", "GND", 12, "GND", 12),
    )
    assert all(pin.is_port and not pin.no_connect for pin in numbered)


def test_a_unique_port_pad_keeps_its_name():
    numbered = pad_numbers({"version": "1.2", "terminals": [_term("d2d_fclk", 0, "d2d_fclk")]})
    assert numbered == (PadNumber("d2d_fclk", "d2d_fclk", 0, "d2d_fclk", 0),)
    assert numbered[0].is_port and not numbered[0].no_connect


def test_a_repeated_name_that_is_no_port_is_numbered_in_pad_index_order():
    contract = {"version": "1.2", "terminals": [
        _term("NC", 4), _term("NC", 1), _term("CLK", 0, "CLK")]}
    numbered = pad_numbers(contract)
    assert numbered == (
        PadNumber("CLK", "CLK", 0, "CLK", 0),
        PadNumber("NC_1", "NC", 1, None, None),
        PadNumber("NC_2", "NC", 4, None, None),
    )
    assert numbered[0].is_port and not numbered[0].no_connect
    assert not numbered[1].is_port and numbered[1].no_connect
    assert not numbered[2].is_port and numbered[2].no_connect


def test_pad_numbers_read_nothing_but_the_terminals():
    _raises({"inventory": {"path": "pins.json"}})


def test_a_contract_with_no_port_identity_is_refused():
    _raises({"version": "1.0", "terminals": [_term("NC", 4), _term("NC", 1), _term("CLK", 0)]})


def test_an_empty_terminal_list_is_no_port_identity():
    _raises({"version": "1.0", "terminals": []})


def test_a_half_port_identity_is_refused():
    _raises({"version": "1.2", "terminals": [
        _term("A", 0, "A"), {"name": "B", "pad_index": 1, "port": "B", "role": "signal"}]})


def test_a_name_on_a_port_and_off_it_is_refused():
    _raises({"version": "1.2", "terminals": [_term("VSS", 0, "VSS"), _term("VSS", 1)]})


def test_one_name_on_two_ports_is_refused():
    _raises({"version": "1.2", "terminals": [
        _term("VSS", 0, "VSS_core"), _term("VSS", 1, "VSS_io")]})


def test_a_generated_number_that_equals_another_terminal_is_refused():
    _raises({"version": "1.2", "terminals": [
        _term("NC", 0), _term("NC", 1), _term("NC_1", 2, "NC_1")]})
