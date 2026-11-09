# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the PUBLIC key-sequence API over the shared oracle (SPEC-48).

``chiplet_format_io.split_top_level_keys`` is a thin wrapper over the reader's own
top-level block splitter. This file proves three things about it, each against
conformance/fixtures/top_level_blocks_cases.json and never against another
implementation: (1) on every document the oracle says splits, the API returns
exactly the oracle's key sequence, preamble excluded, in order, and reports it
splittable; (2) on every document the oracle says a SPLITTER refuses, the API
refuses in the form that document calls for (a repeated top-level key raises, as
loads() does; everything else comes back with ``not_splittable`` set and no
keys); (3) the API is NOT a loader and the split alone is NOT an emission check:
a reader-only refusal (a forbidden line break) splits fine here and is refused by
loads(), and a key-shaped continuation inside a multi-line quoted scalar is a key
to this grammar and a scalar to the parser, so a host must compare ``keys``
against the list it intended to write, obtained independently. The C++ reference
runs the same rows from its own test binary.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "conformance" / "fixtures"

sys.path.insert(0, str(ROOT / "reference" / "python"))
import chiplet_format_io as cfio  # noqa: E402

CASES = json.loads(
    (FIXTURES / "top_level_blocks_cases.json").read_text(encoding="utf-8"))
SPLITS = CASES["splits"]
REFUSE = CASES["refuse"]
NOT_DELIMITABLE = CASES["not_delimitable"]

# The splitter's refusals, read off the "refused_by" FIELD, never the group name
# (conformance/test_top_level_blocks.py says why). Two shapes: the ill-formed
# document raises; the not-splittable one is reported.
SPLITTER_REFUSES = [c for c in REFUSE + NOT_DELIMITABLE
                    if "splitter" in c["refused_by"]]
RAISES = [c for c in SPLITTER_REFUSES if c["kind"] == "repeated_top_level_key"]
REPORTS = [c for c in SPLITTER_REFUSES if c["kind"] != "repeated_top_level_key"]
# Reader-only refusals: the splitter has an answer for these bytes.
READER_ONLY = [c for c in REFUSE if c["refused_by"] == ["reader"]]
assert SPLITS and RAISES and REPORTS and READER_ONLY, "a floor emptied out"


def _expected_keys(case):
    # The empty key is the preamble and is not a key.
    return tuple(b["key"] for b in case["blocks"] if b["key"] != "")


# --- (1) every splittable document: exactly the oracle's keys, in order --------
@pytest.mark.parametrize("case", SPLITS, ids=lambda c: c["name"])
def test_split_documents_return_exactly_the_oracle_keys_in_order(case):
    verdict = cfio.split_top_level_keys(case["doc"])
    assert verdict.not_splittable is None, verdict.not_splittable
    assert verdict.splittable
    assert verdict.keys == _expected_keys(case)
    assert isinstance(verdict.keys, tuple)


def test_the_preamble_is_never_a_key():
    doc = "# a comment before any key\n\nformat_version: \"1.0\"\nassembly:\n  name: d\n"
    assert cfio.PREAMBLE_KEY in cfio.top_level_blocks(doc)
    assert cfio.split_top_level_keys(doc).keys == ("format_version", "assembly")


def test_the_api_and_the_block_accessor_are_one_splitter():
    # Same scanner, two shapes of the same verdict: the keys are the accessor's
    # keys minus the preamble, in the accessor's order, on every split case.
    for case in SPLITS:
        blocks = cfio.top_level_blocks(case["doc"])
        keys = tuple(k for k in blocks if k != cfio.PREAMBLE_KEY)
        assert cfio.split_top_level_keys(case["doc"]).keys == keys, case["name"]


# --- (2) every splitter refusal, in the form the document calls for -----------
@pytest.mark.parametrize("case", RAISES, ids=lambda c: c["name"])
def test_a_repeated_top_level_key_raises_as_loads_does(case):
    with pytest.raises(cfio.ChipletFormatError, match="repeated top-level key"):
        cfio.split_top_level_keys(case["doc"])
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads(case["doc"])


@pytest.mark.parametrize("case", REPORTS, ids=lambda c: c["name"])
def test_a_not_splittable_document_is_reported_with_no_keys(case):
    verdict = cfio.split_top_level_keys(case["doc"])
    assert verdict.not_splittable is not None
    assert not verdict.splittable
    assert verdict.keys == (), "keys must not be readable off a refusal"
    assert "docs/CHIPLET_FORMAT_SPEC.md" in verdict.not_splittable
    # The same reason the block accessor raises with: one splitter, one text.
    with pytest.raises(cfio.ChipletFormatError) as caught:
        cfio.top_level_blocks(case["doc"])
    assert str(caught.value) == verdict.not_splittable
    # And these documents still LOAD when the oracle says so (flow rule 1):
    # splittability and loadability are different verdicts.
    if case.get("loads", True):
        cfio.loads(case["doc"])


# --- (3) what the API is NOT ---------------------------------------------------
@pytest.mark.parametrize("case", READER_ONLY, ids=lambda c: c["name"])
def test_the_api_is_not_a_loader(case):
    # A forbidden line break is not a line break to the grammar, so the split has
    # a perfectly good answer and the refusal belongs to loads(). A host that
    # read a splittable verdict as "loadable" would ship these bytes.
    verdict = cfio.split_top_level_keys(case["doc"])
    assert verdict.splittable
    assert verdict.keys, case["name"]
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads(case["doc"])


# The document a host must NOT be able to bless with the API alone: a multi-line
# double-quoted scalar whose continuation line is key-shaped at column zero.
# Valid YAML, two top-level keys to the parser, three to the grammar.
_PHANTOM = 'format_version: "1.0"\nassembly:\n  name: "demo\nflow: injected"\n'


def test_a_quoted_scalar_continuation_can_hide_a_key_so_the_host_must_compare():
    parsed = yaml.safe_load(_PHANTOM)
    assert list(parsed) == ["format_version", "assembly"], "the premise moved"
    assert parsed["assembly"]["name"] == "demo flow: injected"
    verdict = cfio.split_top_level_keys(_PHANTOM)
    # Splittable, and the grammar sees a key the parser does not. That is the
    # SPEC-36 disagreement, and the API alone cannot see it: it reports what the
    # grammar sees. The host's check is the comparison below, against the keys
    # it INTENDED to write, and it must fail here.
    assert verdict.splittable
    assert verdict.keys == ("format_version", "assembly", "flow")
    intended = ("format_version", "assembly")
    assert verdict.keys != intended, \
        "a host comparing against its intended keys refuses this emission"


def test_order_is_part_of_the_verdict():
    swapped = "assembly:\n  name: demo\nformat_version: \"1.0\"\n"
    verdict = cfio.split_top_level_keys(swapped)
    assert verdict.splittable
    assert verdict.keys == ("assembly", "format_version")
    assert verdict.keys != ("format_version", "assembly"), \
        "same set, different order, different emission"


def test_the_public_name_is_exported():
    assert "split_top_level_keys" in cfio.__all__
    assert "TopLevelKeys" in cfio.__all__
