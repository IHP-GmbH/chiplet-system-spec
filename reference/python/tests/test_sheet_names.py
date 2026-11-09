# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The sheet name rule over the forms a die's bus pads take, and the forms it
refuses."""
import pytest

from chiplet_format_io.sheet_names import is_sheet_name

ADMITTED = [
    "VDD", "sig0", "a.b", "x+", "-n", "0", "_",
    "x[3]",                                   # a trailing subscript
    "x[03]",                                  # kept as written: a second net beside x[3]
    "U1_IO_BOND_bidirs[0].bidir_pad",         # mid-name, as example 10's nets are named
    "IO_BOND_outputs[0].output_pad",
    "a[1].b[2]",                              # repeated
    "a[1][2]",
    "x[12345678901234567890]",                # no digit limit
]

REFUSED = [
    "", "[0]", "[1-3]",                       # a bracket first: KiCad stacks such pins
    "B[0..3]", "x[3:0]",                      # ranges
    "x[]", "x[a]", "x[-1]", "x[+1]", "x[ 3]",  # empty, non-digit
    "x[[0]]", "x[0", "x0]", "x]0[",           # nested, unbalanced
    "x 3", "x/y", "x\n", "x[3]\n",            # characters outside the set
    "x[٣]",                              # a non-ASCII digit
]


@pytest.mark.parametrize("name", ADMITTED)
def test_a_sheet_name_is_admitted(name):
    assert is_sheet_name(name)


@pytest.mark.parametrize("name", REFUSED)
def test_a_form_outside_the_rule_is_refused(name):
    assert not is_sheet_name(name)


@pytest.mark.parametrize("value", [None, 3, b"x[3]", ["x"]])
def test_only_a_string_is_a_sheet_name(value):
    assert not is_sheet_name(value)

