# SPDX-License-Identifier: Apache-2.0
"""Shared authored capture oracle; no schema acceptance inferred from slices."""
import json
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference/python"))
import chiplet_format_io as cfio

ORACLE = json.loads((ROOT / "conformance/fixtures/root_capture_cases.json").read_text(encoding="utf-8"))
CASES = ORACLE["cases"]


def test_capture_oracle_has_named_cases():
    assert CASES
    names = [case["name"] for case in CASES]
    assert all(isinstance(name, str) and name for name in names)
    assert len(names) == len(set(names))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_shared_capture_oracle(case):
    text = case.get("text")
    if text is None:
        text = bytes.fromhex(case["text_hex"]).decode("utf-8", errors="surrogateescape")
    result = cfio.capture_top_level_blocks(text)
    assert result.safe is case["safe"]
    assert isinstance(result.blocks, tuple)
    if not result.safe:
        assert result.preamble == "" and result.blocks == ()
        assert result.reason_code and result.reason
        if result.byte_offset is not None:
            assert 0 <= result.byte_offset <= len(text.encode("utf-8"))
            if "error_byte_offset_if_present" in case:
                assert result.byte_offset == case["error_byte_offset_if_present"]
        return
    assert result.reason_code == result.reason == "" and result.byte_offset is None
    assert result.preamble == case["preamble"]
    assert len(result.blocks) == len(case["blocks"])
    # Expected bytes come from authored oracle slices, never from the splitter.
    cursor = len(case["preamble"].encode("utf-8"))
    for actual, expected in zip(result.blocks, case["blocks"]):
        end = cursor + len(expected["raw"].encode("utf-8"))
        assert (actual.key, actual.raw) == (expected["key"], expected["raw"])
        assert (actual.byte_start, actual.byte_end) == (cursor, end)
        assert expected.get("byte_start", cursor) == cursor
        assert expected.get("byte_end", end) == end
        assert text.encode("utf-8")[cursor:end] == expected["raw"].encode("utf-8")
        cursor = end
    assert case["preamble"] + "".join(b["raw"] for b in case["blocks"]) == text
    if case.get("load_refuses"):
        with pytest.raises(cfio.ChipletFormatError):
            cfio.loads(text)


def test_original_all_blocks_fixture_remains_loadable_and_capturable():
    text = (ROOT / "conformance/fixtures/v1_0_all_blocks.chiplet").read_bytes().decode("utf-8")
    result = cfio.capture_top_level_blocks(text)
    assert result.safe, result.reason
    assert [b.key for b in result.blocks] == [
        "format_version", "assembly", "interposer", "interconnect", "technologies",
        "connection_stacks", "components", "interfaces", "netlist", "flow"]
    assert result.preamble + "".join(b.raw for b in result.blocks) == text
    cfio.loads(text)


@pytest.mark.parametrize("text,offset", [
    (" \t\n # lead\na: 1\n", 1), ("\t\na: 1\n", 0),
    ("  \t# lead\na: 1\n", 2), ("# é\n \t\na: 1\n", 6),
    ("# é\r\n \t\r\na: 1\r\n", 7),
])
def test_tab_in_preamble_is_a_capture_profile_refusal_with_exact_byte_offset(text, offset):
    result = cfio.capture_top_level_blocks(text)
    assert not result.safe
    assert result.reason_code == "preamble_tab"
    assert result.byte_offset == offset
    assert result.preamble == "" and result.blocks == ()


def test_capture_does_not_narrow_existing_loader_or_lexical_api():
    quoted = "format_version: '1.0'\nassembly: {name: demo}\n'interposer': {adapter: intm4tm2}\n"
    assert cfio.loads(quoted)["assembly"]["name"] == "demo"
    assert not cfio.capture_top_level_blocks(quoted).safe
    phantom = "a: \"first\ninterposer: hidden\"\n"
    assert cfio.split_top_level_keys(phantom).keys == ("a", "interposer")
    assert not cfio.capture_top_level_blocks(phantom).safe


def test_capture_is_deeply_immutable_and_owns_original_text():
    text = "a: 1\n"
    result = cfio.capture_top_level_blocks(text)
    text = "a: 2\n"
    assert result.blocks[0].raw == "a: 1\n"
    with pytest.raises(FrozenInstanceError):
        result.safe = False
    with pytest.raises(FrozenInstanceError):
        result.blocks[0].raw = text


@pytest.mark.parametrize("key", ["y", "N", "yes", "NO", "True", "FALSE", "on", "OFF", "null"])
def test_ambiguous_scalar_root_names_are_not_captured(key):
    assert not cfio.capture_top_level_blocks(key + ": value\n").safe


def test_capture_rejects_programmer_type_errors():
    with pytest.raises(TypeError):
        cfio.capture_top_level_blocks(b"a: 1\n")
