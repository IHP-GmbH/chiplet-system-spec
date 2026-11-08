# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Tests for the chiplet_format_io reference reader/writer."""
import sys
from pathlib import Path

import pytest
import yaml

import chiplet_format_io as cfio

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples"


def test_roundtrip_canonical_example():
    """load -> dump -> load is a semantic fixed point on a real example."""
    path = EXAMPLES / "interposer_demo_design.chiplet"
    first = cfio.load(path)
    text = cfio.dumps(first)
    second = cfio.loads(text)
    assert first == second
    # key order preserved on the top level
    assert list(first.keys())[0] == "format_version"


def test_all_example_chiplets_parse():
    chiplets = list(EXAMPLES.glob("*.chiplet"))
    assert chiplets, "expected at least one example .chiplet"
    for p in chiplets:
        data = cfio.load(p, allow_intermediate=True)
        assert str(data["format_version"]) == cfio.SUPPORTED_FORMAT_VERSION


def test_missing_format_version_rejected():
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads("assembly:\n  name: x\n")


def test_unsupported_version_rejected():
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads('format_version: "2.0"\nassembly:\n  name: x\n')


def test_assembly_name_required():
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads('format_version: "1.0"\nassembly:\n  units: um\n')


def test_component_requires_id_and_type():
    doc = 'format_version: "1.0"\nassembly:\n  name: a\ncomponents:\n- type: die\n'
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads(doc)


def test_intermediate_refused_by_default_then_allowed():
    doc = (
        'format_version: "1.0"\n'
        "_metadata:\n  finalize_required: true\n"
        "assembly:\n  name: a\n"
    )
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads(doc)
    data = cfio.loads(doc, allow_intermediate=True)
    assert data["assembly"]["name"] == "a"


def test_dump_roundtrips_components_order():
    doc = cfio.load(EXAMPLES / "interposer_demo_design.chiplet")
    ids = [c["id"] for c in doc["components"]]
    reloaded = cfio.loads(cfio.dumps(doc))
    assert [c["id"] for c in reloaded["components"]] == ids


def _component(doc, cid):
    for c in doc["components"]:
        if c["id"] == cid:
            return c
    return None


def test_attachment_surface_z_roundtrip():
    """The interposer's die-mount plane parses, survives dump/load and is
    distinct from the physical body thickness; dies carry none (consumers fall
    back to dimensions.thickness)."""
    doc = cfio.load(EXAMPLES / "interposer_demo_design.chiplet")
    interposer = _component(doc, "interposer")
    assert interposer is not None
    assert interposer["attachment_surface_z"] == 10.8103
    # thickness is now the physical body, decoupled from the mount plane.
    assert interposer["dimensions"]["thickness"] == 300.0

    die = _component(doc, "U1")
    assert die is not None
    assert "attachment_surface_z" not in die

    reloaded = cfio.loads(cfio.dumps(doc))
    assert _component(reloaded, "interposer")["attachment_surface_z"] == 10.8103


def test_no_gpl_runtime_dependency():
    """Importing/using the library must not pull in pcbnew or klayout."""
    cfio.loads('format_version: "1.0"\nassembly:\n  name: a\n')
    assert "pcbnew" not in sys.modules
    assert "klayout" not in sys.modules


def test_source_has_no_gpl_imports():
    src = Path(cfio.__file__).read_text(encoding="utf-8")
    assert "import pcbnew" not in src
    assert "import klayout" not in src
    assert "from klayout" not in src


def test_a_forbidden_line_break_is_refused_before_the_yaml_parse():
    """NEL, U+2028 and U+2029 make a document ill-formed, wherever they sit.

    PyYAML implements YAML 1.1 and reads all three as line breaks; yaml-cpp
    implements YAML 1.2 and does not, so the same bytes are two documents. The
    refusal is this library's, not PyYAML's: on the smuggle shape below PyYAML
    does not raise at all, it silently returns a second top-level format_version.
    """
    for char, code_point in (("\u0085", "U+0085"), ("\u2028", "U+2028"),
                             ("\u2029", "U+2029")):
        smuggle = ('format_version: "1.0"\nassembly:\n  name: demo' + char
                   + 'format_version: "9.0"\ncomponents: []\n')
        # What PyYAML alone does with it, which is why the check is here.
        assert yaml.safe_load(smuggle)["format_version"] == "9.0"
        for validate in (True, False):
            with pytest.raises(cfio.ChipletFormatError) as excinfo:
                cfio.loads(smuggle, validate=validate)
            assert code_point in str(excinfo.value)
            assert "line 3" in str(excinfo.value)


def test_the_writer_escapes_a_forbidden_line_break():
    """dumps() must not write bytes loads() refuses, and must not lose the value.

    yaml.safe_dump(allow_unicode=True) writes all three raw into a single-quoted
    scalar and PyYAML folds them back on the next read, so the value did not
    survive its own round trip before this.
    """
    # U+0085 is "\\x85" and not PyYAML's own "\\N". This test used to pin \\N,
    # which is how the wrong spelling stayed correct-looking: measured on PyYAML
    # 6.0.3 and yaml-cpp 0.8.0, "a\\Nb" reads back as 61 c2 85 62 here and as
    # 61 85 62 there, a bare 0x85 that is not valid UTF-8 on its own, so the
    # document this writer produced handed the other reference reader a malformed
    # string. LS and PS round-trip correctly on \\L and \\P in both and are
    # unchanged; the defect was this one escape, not the family.
    for char, escape in (("\u0085", "\\x85"), ("\u2028", "\\L"),
                         ("\u2029", "\\P")):
        doc = {"format_version": "1.0",
               "assembly": {"name": "demo" + char + "x"}}
        text = cfio.dumps(doc)
        assert char not in text
        assert escape in text
        assert cfio.loads(text)["assembly"]["name"] == "demo" + char + "x"


#: Every top-level key the PyYAML 6.0.3 emitter cannot write as a KEY LINE, with
#: the line it writes instead. Measured, not predicted, and that is the point of
#: the writer rule being a post-check on the emitted text: three different
#: spellings escape the grammar and only one of them is quoted, so a regex on the
#: key would pass `yes`, `null` and `1.0` and a rule about quoting would miss the
#: explicit key and the bare key with a space.
_KEYS_THE_EMITTER_CANNOT_WRITE_BARE = [
    ("a\u2028b", '? "a\\Lb"'),
    ("a\u0085b", '? "a\\x85b"'),
    ("a\u2029b", '? "a\\Pb"'),
    ("a" * 200 + "\rb", "? \"" + "a" * 200 + "\\r\\"),
    ("a\rb", '"a\\rb":'),
    ("a b", "a b:"),
    ("a: b", "'a: b':"),
    ("yes", "'yes':"),
    ("null", "'null':"),
    ("1.0", "'1.0':"),
]


@pytest.mark.parametrize("key,line", _KEYS_THE_EMITTER_CANNOT_WRITE_BARE,
                         ids=[repr(k) for k, _ in
                              _KEYS_THE_EMITTER_CANNOT_WRITE_BARE])
def test_the_writer_refuses_a_top_level_key_it_cannot_emit_as_a_key_line(key,
                                                                        line):
    """The writer must not emit a document its own splitter misreads.

    Each of these produced a file whose split reported two top-level keys and
    whose parse reported three, with no diagnostic from either side, and the
    lines went to `assembly`, whose owner drops them on the next re-export. The
    refusal names the key and the line, because the author's mental model is the
    dict they passed and the defect is in the bytes.
    """
    doc = {"format_version": "1.0", "assembly": {"name": "demo"},
           key: {"x": 1}}
    for validate in (True, False):
        with pytest.raises(cfio.ChipletFormatError) as excinfo:
            cfio.dumps(doc, validate=validate)
        message = str(excinfo.value)
        assert repr(key) in message, message
        assert repr(line) in message, message
        assert "Rename the key, or nest it" in message, message


def test_the_writer_rule_is_about_the_top_level_only():
    """Nested keys are unrestricted, and the refusal must not creep into them.

    The grammar is a statement about column zero and nothing else. A nested key
    with a space, a colon or a reserved word is quoted by the emitter, indented,
    and owned by the block it sits in, so it costs nobody anything.
    """
    doc = {"format_version": "1.0",
           "assembly": {"name": "demo", "a b": 1, "yes": 2, "a: b": 3}}
    text = cfio.dumps(doc, validate=False)
    assert cfio.loads(text, validate=False)["assembly"]["a b"] == 1
    assert list(cfio.top_level_blocks(text)) == ["format_version", "assembly"]


def test_the_writer_output_splits_into_exactly_the_keys_it_was_given():
    """The property the refusal protects, asserted on a document that passes.

    A legal document goes out with every top-level key recoverable from the text
    by the same splitter every host runs, in the order it was written. Without
    this the refusal above could be satisfied by a writer that refuses
    everything.
    """
    doc = cfio.load(EXAMPLES / "interposer_demo_design.chiplet")
    text = cfio.dumps(doc)
    assert list(cfio.top_level_blocks(text)) == list(doc)
    assert list(cfio.loads(text)) == list(doc)


def test_dump_to_a_file_goes_through_the_same_refusal():
    """dump() is dumps() plus a write, so it must not be a way around the rule."""
    import tempfile
    doc = {"format_version": "1.0", "assembly": {"name": "demo"}, "a b": {}}
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "out.chiplet"
        with pytest.raises(cfio.ChipletFormatError):
            cfio.dump(doc, target)


@pytest.mark.parametrize("validate", [False, True])
def test_dump_refused_key_preserves_existing_file_bytes(tmp_path, validate):
    """Serialization refusal must precede opening for write."""
    target = tmp_path / "existing.chiplet"
    original = b'# keep this comment\r\nformat_version: "1.0"\r\nassembly:\r\n  name: demo\r\n'
    target.write_bytes(original)
    doc = {"format_version": "1.0", "assembly": {"name": "demo"}, "a b": {}}
    with pytest.raises(cfio.ChipletFormatError, match="cannot be written as a key line"):
        cfio.dump(doc, target, validate=validate)
    assert target.read_bytes() == original


@pytest.mark.parametrize("key, emitted, recovered", [
    (123, "123: {}", "123"),
    (None, "null: {}", "null"),
])
@pytest.mark.parametrize("validate", [False, True])
def test_non_string_top_level_key_has_type_diagnostic(key, emitted, recovered, validate):
    doc = {"format_version": "1.0", "assembly": {"name": "demo"}, key: {}}
    with pytest.raises(cfio.ChipletFormatError) as caught:
        cfio.dumps(doc, validate=validate)
    message = str(caught.value)
    assert f"top-level key {key!r} is not a string" in message
    assert f"emitter wrote {emitted!r}" in message
    assert f"block reader reads it back as the string key {recovered!r}" in message
    assert "Make it a string key" in message
    assert "or nest it" in message
    assert "attributes those lines to the preceding key" not in message


def test_string_key_outside_grammar_keeps_its_distinct_diagnostic():
    doc = {"format_version": "1.0", "assembly": {"name": "demo"}, "a b": {}}
    with pytest.raises(cfio.ChipletFormatError) as caught:
        cfio.dumps(doc)
    message = str(caught.value)
    assert "top-level key 'a b' cannot be written as a key line" in message
    assert "emitter wrote 'a b: {}'" in message
    assert "is not a string" not in message


@pytest.mark.parametrize("blank", ["", " ", "\t", " \t \t"])
def test_block_grammar_blank_lines_are_space_and_tab(blank):
    text = 'format_version: "1.0"\n' + blank + '\nassembly:\n  name: demo\n'
    blocks = cfio.top_level_blocks(text)
    assert list(blocks) == ["format_version", "assembly"]
    assert blocks["format_version"] == 'format_version: "1.0"\n' + blank + '\n'


@pytest.mark.parametrize("char", ["\u00a0", "\u1680", "\u2000", "\u2003",
                                  "\u202f", "\u205f", "\u3000"])
def test_unicode_space_continuation_is_readable_but_not_splittable(char):
    text = ('format_version: "1.0"\nassembly:\n  name: "demo\n'
            + char + '\n  cont"\nflow:\n  steps: []\n')
    assert cfio.loads(text)["assembly"]["name"]
    with pytest.raises(cfio.ChipletFormatError, match="line 4: unattributable"):
        cfio.top_level_blocks(text)


@pytest.mark.parametrize("char", ["\v", "\f", "\x1c", "\x1d", "\x1e", "\x1f"])
def test_c0_separators_are_not_blank_line_content(char):
    # These bytes need not be valid YAML: the splitter's blank set is a
    # line-grammar rule and cannot depend on Python's Unicode whitespace table.
    text = 'format_version: "1.0"\n' + char + '\nassembly:\n  name: demo\n'
    with pytest.raises(cfio.ChipletFormatError, match="line 2: unattributable"):
        cfio.top_level_blocks(text)


def test_document_markers_stay_in_the_current_block():
    text = '---\nformat_version: "1.0"\nassembly:\n  name: demo\n...\n'
    blocks = cfio.top_level_blocks(text)
    assert blocks[cfio.PREAMBLE_KEY] == '---\n'
    assert blocks["assembly"] == 'assembly:\n  name: demo\n...\n'
