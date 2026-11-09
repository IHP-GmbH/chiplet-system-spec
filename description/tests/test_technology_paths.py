# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""A technology's optional layer sheet (``layer_properties``) and stackup:
written verbatim when set, absent when not, so a System that sets neither
emits the bytes it always did."""
from __future__ import annotations

from dataclasses import replace

import pytest

from chiplet_system_description import INTM4TM2, SG13G2, SystemSpecError, Technology, elaborate
from chiplet_system_description._cfio import cfio

#: The technologies block of the two-die example, which sets neither path. Any
#: change here moves the digest of every document a System emits.
UNSET_BLOCK = """\
technologies:
  intm4tm2:
    description: IHP 130nm IntM4TM2 aluminium BEOL interposer
    dbu: 0.001
  sg13g2:
    description: IHP SG13G2 130nm BiCMOS die
    dbu: 0.001
"""

LYP = "${INTERPOSER_PDK_ROOT}/libs.tech/klayout/tech/intm4tm2.lyp"
STACKUP = "${INTERPOSER_PDK_ROOT}/libs.tech/chiplet_studio/intm4tm2.stackup.yaml"


def with_technologies(system, *techs):
    return replace(system, technologies=techs)


def test_unset_paths_leave_the_document_unchanged(built_system):
    assert INTM4TM2.layer_properties is None and INTM4TM2.stackup is None
    text = cfio.dumps(elaborate(built_system))
    assert cfio.top_level_block(text, "technologies") == UNSET_BLOCK


def test_set_paths_are_written_verbatim_in_spec_order(built_system):
    interposer = replace(INTM4TM2, layer_properties=LYP, stackup=STACKUP)
    die = replace(SG13G2, layer_properties="/opt/pdks/sg13g2/sg13g2.lyp")
    doc = elaborate(with_technologies(built_system, interposer, die))
    techs = doc["technologies"]
    assert list(techs["intm4tm2"]) == ["description", "layer_properties", "stackup", "dbu"]
    assert (techs["intm4tm2"]["layer_properties"], techs["intm4tm2"]["stackup"]) == (LYP, STACKUP)
    assert list(techs["sg13g2"]) == ["description", "layer_properties", "dbu"]
    assert techs["sg13g2"]["layer_properties"] == "/opt/pdks/sg13g2/sg13g2.lyp"
    assert cfio.loads(cfio.dumps(doc)) == doc


def test_only_the_technologies_block_changes(built_system):
    before = cfio.dumps(elaborate(built_system))
    after = cfio.dumps(elaborate(with_technologies(
        built_system, replace(INTM4TM2, stackup=STACKUP), SG13G2)))
    split = cfio.top_level_blocks
    assert {k: v for k, v in split(after).items() if k != "technologies"} == {
        k: v for k, v in split(before).items() if k != "technologies"}
    assert split(after)["technologies"] != split(before)["technologies"]


@pytest.mark.parametrize("field", ["layer_properties", "stackup"])
@pytest.mark.parametrize("value", ["", 3, b"/x.lyp"])
def test_a_path_must_be_a_nonempty_string(field, value):
    with pytest.raises(SystemSpecError, match="non-empty path string"):
        Technology("intm4tm2", **{field: value})


@pytest.mark.parametrize("field", ["layer_properties", "stackup"])
@pytest.mark.parametrize("value", ["./tech/sg13g2.lyp", "tech/sg13g2.lyp", "sg13g2.lyp"])
def test_a_relative_path_is_refused(field, value):
    with pytest.raises(SystemSpecError, match="is a relative path"):
        Technology("sg13g2", **{field: value})


@pytest.mark.parametrize("field", ["layer_properties", "stackup"])
def test_a_variable_without_braces_is_refused(field):
    with pytest.raises(SystemSpecError, match="is a relative path"):
        Technology("sg13g2", **{field: "$PDK_ROOT/libs.tech/klayout/tech/sg13g2.lyp"})


@pytest.mark.parametrize("value", [LYP, "${PDK_ROOT}/libs.tech/klayout/tech/sg13g2.lyp",
                                   "/opt/pdks/sg13g2/sg13g2.lyp"])
def test_a_braced_variable_or_absolute_path_is_accepted(value):
    tech = Technology("sg13g2", layer_properties=value, stackup=value)
    assert (tech.layer_properties, tech.stackup) == (value, value)
