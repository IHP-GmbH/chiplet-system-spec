# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the top-level block grammar and its oracle.

The grammar is normative in docs/CHIPLET_FORMAT_SPEC.md ("Top-level block
grammar"): which run of lines belongs to which top-level key, decided on the raw
text. Flow rule 4 (a host re-emits a flow block it did not author byte for byte)
is defined in terms of it, so three implementations run the SAME oracle file
rather than being compared to each other: the merge splitter in the KiCad plugin,
the Python reference reader, and the C++ reference reader.

This file proves the oracle is well formed and that the Python reference obeys it.
The C++ side reads the same file from its own test binary.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "conformance" / "fixtures"

sys.path.insert(0, str(ROOT / "reference" / "python"))
import chiplet_format_io as cfio  # noqa: E402

CASES = json.loads(
    (FIXTURES / "top_level_blocks_cases.json").read_text(encoding="utf-8"))

ACCEPT = CASES["key_lines"]["accept"]
REJECT = CASES["key_lines"]["reject"]
SPLITS = CASES["splits"]
REFUSE = CASES["refuse"]
NOT_DELIMITABLE = CASES["not_delimitable"]

#: The four kinds of refusal, kept apart because they describe different SHAPES
#: of document: a quoted key at column zero is valid YAML nobody can split; an
#: unattributable line at column zero is the same statement generalised, and is
#: what the reference WRITER used to be able to emit (an explicit key, a bare key
#: outside the grammar); a repeated top-level key is a document no reader may read
#: and no splitter may attribute; a forbidden line break is a document no reader
#: may read while the splitter has a perfectly good answer for it (those bytes are
#: not line breaks to the grammar, so no block starts there), which is why the
#: grammar case for it stays under splits. The VERDICT is a separate field, read
#: just below.
NOT_SPLITTABLE = [c for c in REFUSE if c["kind"] == "quoted_key_at_column_zero"]
UNATTRIBUTABLE = [c for c in REFUSE
                  if c["kind"] == "unattributable_line_at_column_zero"]
ILL_FORMED = [c for c in REFUSE if c["kind"] == "repeated_top_level_key"]
FORBIDDEN_LINE_BREAK = [c for c in REFUSE if c["kind"] == "forbidden_line_break"]

#: Who owes the refusal, read off the case's own "refused_by" list rather than
#: inferred from its kind. The group name says "refuse" and says nothing about
#: WHICH implementation, and inferring it from the membership of the group is what
#: broke two consumers when six reader-only rows landed in it: their
#: "the splitter must raise" test was parametrized over the whole group and went
#: red on a verdict that had been inverted under it. A consumer that filters on
#: this field survives both kinds of addition; one whose vendored copy predates
#: the field raises KeyError, which is the failure we want, because a missing
#: field is loud and an inverted verdict is not.
SPLITTER_REFUSES = [c for c in REFUSE + NOT_DELIMITABLE
                   if "splitter" in c["refused_by"]]
READER_REFUSES = [c for c in REFUSE if "reader" in c["refused_by"]]

#: The cases a splitter refuses and a reader still reads: the asymmetry itself.
SPLITTER_ONLY = [c for c in REFUSE if c["refused_by"] == ["splitter"]]

#: The floor under each of the derived lists. A filter that quietly empties out
#: takes its whole parametrized test with it and leaves a green run behind.
assert SPLITTER_REFUSES and READER_REFUSES and SPLITTER_ONLY

#: The code points the oracle refuses, read off the file. Every test that used
#: to spell them out reads this instead.
FORBIDDEN_CODE_POINTS = sorted({c["code_point"] for c in FORBIDDEN_LINE_BREAK})

#: The escape each code point is written as by the PyYAML emitter. A spelling,
#: not a set: the members come from the oracle above and a code point that turns
#: up here without an entry fails the writer test rather than skipping it.
#: The escaped spelling this format uses for each refused character. U+0085 is
#: "\\x85" and NOT PyYAML's "\\N", which is the spelling PyYAML would pick on its
#: own: measured on PyYAML 6.0.3 and yaml-cpp 0.8.0, a scalar written "a\\Nb" reads
#: back as 61 c2 85 62 here and as 61 85 62 there, a BARE 0x85 that is not valid
#: UTF-8 by itself, so our own escape handed the other reference reader a
#: malformed string. "\\x85" and "\\u0085" both decode to U+0085 in the two.
#: "\\L" and "\\P" round-trip correctly in both and are unchanged: the defect was
#: this one escape, not the family.
_ESCAPES = {"U+000D": "\\r", "U+0085": "\\x85", "U+2028": "\\L", "U+2029": "\\P"}

#: The range the forbidden set is DERIVED over. It runs past the last character
#: any YAML 1.1 parser calls a line break (U+2029) with room to spare, and the
#: sweep costs a fraction of a second.
_DERIVATION_RANGE = range(0x0000, 0x2200)

#: The two halves of the splits group's load verdict, each with its floor guard
#: below. "loadable": false is an OBLIGATION to refuse, not a permission to skip:
#: as a permission it let one row claim a refusal that never happened, with every
#: test green, which is the same overloading "refused_by" fixes one field along.
SPLITS_LOAD = [c for c in SPLITS if c.get("loadable", True)]
SPLITS_REFUSED = [c for c in SPLITS if not c.get("loadable", True)]
assert SPLITS_LOAD and SPLITS_REFUSED

#: A quoted key at column zero, in the spelling the refuse cases must carry.
_QUOTED_KEY = re.compile(r'^(?:"[^"]*"|\'[^\']*\'):(?:\s.*)?\Z')


# --- (a) the oracle itself -------------------------------------------------
def test_oracle_is_wellformed():
    assert ACCEPT and REJECT and SPLITS and REFUSE
    # The file states its own version, so a consumer holding a stale vendored
    # copy can say so instead of failing somewhere further down.
    assert CASES["version"] >= 2
    lines = [c["line"] for c in ACCEPT]
    assert len(lines) == len(set(lines))
    assert len(REJECT) == len(set(REJECT))
    assert not set(lines) & set(REJECT)
    for case in ACCEPT:
        assert case["key"], case
        # The key is what stands before the colon, so it must open the line.
        assert case["line"].startswith(case["key"] + ":"), case
    for line in ACCEPT + [{"line": x} for x in REJECT]:
        assert "\n" not in line["line"], line
    names = [c["name"] for c in SPLITS] + [c["name"] for c in REFUSE]
    assert len(names) == len(set(names)), "duplicate case name"
    docs = [c["doc"] for c in SPLITS] + [c["doc"] for c in REFUSE]
    assert len(docs) == len(set(docs)), "duplicate case document"
    # The two spellings this file exists for, pinned by name: the quoted key at
    # column zero (which a splitter silently mis-attributes) and a CRLF document
    # (which an implementation anchoring on ECMAScript's narrower dot gets wrong).
    assert '"flow":' in REJECT
    assert any("crlf" in c["name"] for c in SPLITS)
    # All four refusal kinds are present. A kind that quietly empties out takes
    # its whole parametrized test with it and leaves a green run behind.
    assert NOT_SPLITTABLE and UNATTRIBUTABLE and ILL_FORMED \
        and FORBIDDEN_LINE_BREAK
    assert (len(NOT_SPLITTABLE) + len(UNATTRIBUTABLE) + len(ILL_FORMED)
            + len(FORBIDDEN_LINE_BREAK)) == len(REFUSE)
    # The two writer spellings the unattributable kind exists for. Named, because
    # they are what SPEC-41 was: our own writer emitting a document its own
    # splitter mis-attributes, and a kind carrying only the tab case would look
    # like a tidy-up of an edge nobody meets.
    assert any('\n? ' in c["doc"] for c in UNATTRIBUTABLE), \
        "the explicit-key spelling the writer emits is not in the oracle"
    assert any("\na b:" in c["doc"] for c in UNATTRIBUTABLE), \
        "the bare-key-with-a-space spelling the writer emits is not in the oracle"
    assert any("\n\u00a0\n" in c["doc"] for c in UNATTRIBUTABLE), \
        "the non-SPACE/TAB blank-line refusal is not in the oracle"
    # Both shapes of the disagreement, for EVERY code point in the set, and the
    # set is read off the file rather than named here: naming it here is how the
    # rule shipped with three of its four members. One shape alone certifies one
    # direction of a break that runs both ways: the smuggle is refused by
    # yaml-cpp and read by PyYAML, and the plain-scalar one is read by yaml-cpp
    # and refused by PyYAML.
    for code_point in FORBIDDEN_CODE_POINTS:
        shapes = [c["name"] for c in FORBIDDEN_LINE_BREAK
                  if c["code_point"] == code_point]
        assert len(shapes) >= 2, code_point
        assert any(n.endswith("_smuggles_a_top_level_key") for n in shapes)
        assert any(n.endswith("_inside_a_plain_scalar") for n in shapes)


@pytest.mark.parametrize("case", SPLITS, ids=lambda c: c["name"])
def test_oracle_split_case_is_wellformed(case):
    keys = [b["key"] for b in case["blocks"]]
    assert len(keys) >= 2, "a split case with one key discriminates nothing"
    assert len(keys) == len(set(keys)), "a key appears twice in one case"
    assert case["doc"]
    for block in case["blocks"]:
        assert block["text"], block
        if block["key"]:
            assert block["text"].startswith(block["key"] + ":"), block
    # Lossless by construction: the slices tile the document, with no exception.
    # The one case that used to need one, a repeated top-level key whose two runs
    # were joined under the first occurrence, is a refuse case now.
    assert "".join(b["text"] for b in case["blocks"]) == case["doc"]
    assert "reconstructs" not in case


@pytest.mark.parametrize("case", REFUSE, ids=lambda c: c["name"])
def test_oracle_refuse_case_is_wellformed(case):
    assert case["doc"] and case["reason"]
    assert isinstance(case["loads"], bool)
    # Every case says WHO refuses it. This is the field the group name used to
    # imply, and implying it is what let six reader-only cases land in a group
    # two consumers read as "the splitter must raise".
    assert isinstance(case["refused_by"], list) and case["refused_by"], case
    assert set(case["refused_by"]) <= {"splitter", "reader"}, case
    assert case["refused_by"] == sorted(case["refused_by"], reverse=True), \
        "keep the list in one order so a byte-exact vendored copy stays stable"
    # The two ways of saying the same thing agree. "loads" is what consumers
    # already read and is kept; "reader" in refused_by is the same fact stated
    # where the other verdict lives, and a case where they disagree is a case
    # nobody can implement.
    assert ("reader" in case["refused_by"]) == (case["loads"] is False), case
    if case["kind"] == "quoted_key_at_column_zero":
        quoted = [ln for ln in case["doc"].split("\n") if _QUOTED_KEY.match(ln)]
        assert quoted, "the case must carry a quoted key at column zero"
        # Valid YAML, so it loads; "writes" is a statement about a source-slice
        # writer and only means anything for a document a reader can hold.
        assert case["loads"] is True
        assert isinstance(case["writes"], bool)
    elif case["kind"] == "unattributable_line_at_column_zero":
        # The generalisation, on the same terms as the quoted key: valid YAML
        # nobody can attribute. The case has to carry a line the rule actually
        # fires on, and it must not be the QUOTED spelling, which keeps its own
        # kind and its own message.
        offending = [ln for ln in case["doc"].split("\n")
                     if cfio._is_unattributable(ln)]
        assert offending, "the case must carry an unattributable line"
        assert not any(_QUOTED_KEY.match(ln) for ln in offending), \
            "a quoted key belongs under quoted_key_at_column_zero"
        assert case["loads"] is True
        assert isinstance(case["writes"], bool)
    elif case["kind"] == "repeated_top_level_key":
        keys = [k for k in (cfio.top_level_key(ln)
                            for ln in case["doc"].split("\n")) if k]
        assert len(keys) != len(set(keys)), "the case must repeat a key"
        assert case["loads"] is False
        assert "writes" not in case, "a document no reader holds is never written"
    elif case["kind"] == "forbidden_line_break":
        assert case["loads"] is False
        assert "writes" not in case, "a document no reader holds is never written"
        # The case has to carry the character it is about, and the reader has to
        # be able to quote it back: all three are invisible in an editor, so a
        # refusal that named neither the code point nor the line would send the
        # author looking at the wrong thing.
        char = chr(int(case["code_point"][2:], 16))
        assert char in case["doc"], case["name"]
        assert case["doc"].split("\n")[case["line"] - 1].count(char) == 1
    else:
        raise AssertionError("unknown refuse kind " + case["kind"])


@pytest.mark.parametrize("case", NOT_DELIMITABLE, ids=lambda c: c["name"])
def test_oracle_not_delimitable_case_is_wellformed(case):
    assert case["doc"] and case["reason"]
    assert case["kind"] == "unattributable_line_at_column_zero", case
    assert case["refused_by"] == ["splitter"], case
    # The section exists for documents that carry a flow node the grammar cannot
    # delimit, so a case without a flow node is not one of them...
    assert "flow" in case["doc"]
    # ...and neither is one whose flow IS a key line, which would have a slice.
    assert not any(cfio.top_level_key(line) == "flow"
                   for line in case["doc"].split("\n"))


# --- (b) the Python reference against the oracle ---------------------------
@pytest.mark.parametrize("case", ACCEPT, ids=lambda c: c["line"])
def test_key_line_accepted(case):
    assert cfio.top_level_key(case["line"]) == case["key"]


@pytest.mark.parametrize("line", REJECT, ids=repr)
def test_key_line_rejected(line):
    assert cfio.top_level_key(line) is None


@pytest.mark.parametrize("case", SPLITS, ids=lambda c: c["name"])
def test_split_matches_the_oracle(case):
    blocks = cfio.top_level_blocks(case["doc"])
    assert list(blocks.items()) == [(b["key"], b["text"]) for b in case["blocks"]]


@pytest.mark.parametrize("case", SPLITS, ids=lambda c: c["name"])
def test_named_block_matches_the_oracle(case):
    for block in case["blocks"]:
        assert cfio.top_level_block(case["doc"], block["key"]) == block["text"]
    assert cfio.top_level_block(case["doc"], "no_such_key") is None


@pytest.mark.parametrize("case", SPLITTER_REFUSES, ids=lambda c: c["name"])
def test_splitting_is_refused(case):
    with pytest.raises(cfio.ChipletFormatError):
        cfio.top_level_blocks(case["doc"])
    with pytest.raises(cfio.ChipletFormatError):
        cfio.top_level_block(case["doc"], "flow")


@pytest.mark.parametrize("case", READER_REFUSES, ids=lambda c: c["name"])
def test_an_ill_formed_document_is_refused_at_load(case):
    # The other half of the asymmetry. A repeated top-level key is not a question
    # of ownership that only a splitter has to answer: PyYAML keeps the LAST value
    # and yaml-cpp the FIRST, so the two reference readers would report different
    # documents from one file and nothing downstream could tell. There is no
    # conforming reading, so there is no reading.
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads(case["doc"])


@pytest.mark.parametrize("case", SPLITTER_ONLY, ids=lambda c: c["name"])
def test_a_document_a_splitter_refuses_is_still_read(case):
    # Splitting and reading are different verdicts. A quoted key at column zero
    # is an ordinary key to YAML: the document is structurally valid, and flow
    # rule 1 says a reader that cannot handle the flow block MUST NOT reject the
    # file. Refusing here would make the reference reader stricter than the spec
    # it defines, over a question (who owns which bytes) that only a host writing
    # the file back has to answer.
    assert cfio.loads(case["doc"])["assembly"]["name"]


@pytest.mark.parametrize("case", NOT_DELIMITABLE, ids=lambda c: c["name"])
def test_a_flow_block_the_grammar_cannot_delimit_still_loads(case):
    # Same rule, the other spelling: the flow node is there, YAML reads it, and
    # the grammar has no slice for it. Loading is unaffected.
    assert cfio.loads(case["doc"])["flow"] is not None


@pytest.mark.parametrize("case", FORBIDDEN_LINE_BREAK, ids=lambda c: c["name"])
def test_a_forbidden_line_break_is_refused_with_a_text_level_reason(case):
    # The refusal has to be the FORMAT's, not a parser's. On the smuggle shape
    # PyYAML does not raise at all and on the plain-scalar shape it raises with a
    # scanner message that describes a simple key, so a test that only asserted
    # "something went wrong" would pass on half the cases for the wrong reason
    # and on the other half by accident.
    with pytest.raises(cfio.ChipletFormatError) as excinfo:
        cfio.loads(case["doc"])
    message = str(excinfo.value)
    assert case["code_point"] in message, message
    assert f"line {case['line']}" in message, message
    assert "LF and CRLF" in message, message
    # Same verdict with validation off: this is a fact about the bytes, and a
    # consumer that opts out of semantic validation has not opted out of it.
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads(case["doc"], validate=False)


@pytest.mark.parametrize(
    "case", [c for c in FORBIDDEN_LINE_BREAK
             if c["name"].endswith("_smuggles_a_top_level_key")],
    ids=lambda c: c["name"])
def test_the_smuggle_changes_a_value_and_not_the_shape(case):
    # What the refusal is for, stated as the property a consumer would have to
    # check otherwise. PyYAML reads the separator as a line break, so the second
    # format_version lands as a top-level key: the KEY LIST is identical to the
    # same document with the separator taken out, and only the VALUE changes.
    # A "no unexpected top-level keys" guard is therefore not a control for this,
    # which is the reason the guard the plugin already has did not see it.
    import yaml  # noqa: PLC0415  (only this test needs the raw parser)

    char = chr(int(case["code_point"][2:], 16))
    # The benign twin: the same document with the smuggled tail cut off the
    # assembly name, which is what an author reading the file in an editor sees.
    plain_doc = case["doc"].replace(char + 'format_version: "9.0"', "")
    smuggled = yaml.safe_load(case["doc"])
    plain = yaml.safe_load(plain_doc)
    assert list(smuggled) == list(plain)
    assert smuggled["format_version"] == "9.0"
    assert plain["format_version"] == "1.0"
    assert plain["assembly"]["name"] == "demo"
    # And the structural view, which is the one every consumer of the grammar
    # has, cannot see it: the LF-only scan reports one format_version block.
    blocks = cfio.top_level_blocks(case["doc"])
    assert list(blocks) == list(plain)


def test_the_grammar_still_does_not_break_a_line_on_a_separator():
    # The control the refusal must not swallow. The splitter's answer for these
    # bytes was never in doubt and has not changed: they are not line breaks, so
    # no block starts at one, and a splitter built on str.splitlines() grows a
    # `flow` block that is not in the file. The LOAD verdict moved; this one did
    # not, and the two are different questions about the same document.
    case = next(c for c in SPLITS if c["name"] ==
                "unicode_line_separator_inside_a_scalar_is_not_a_line_break")
    assert case["loadable"] is False
    blocks = cfio.top_level_blocks(case["doc"])
    assert list(blocks.items()) == [(b["key"], b["text"])
                                    for b in case["blocks"]]
    with pytest.raises(cfio.ChipletFormatError) as excinfo:
        cfio.loads(case["doc"])
    assert "U+2028" in str(excinfo.value)


@pytest.mark.parametrize("code_point", FORBIDDEN_CODE_POINTS)
def test_the_writer_escapes_what_the_reader_refuses(code_point):
    # The writer rule, and it is not decoration: yaml.safe_dump(allow_unicode)
    # writes NEL, LS and PS raw into a SINGLE-quoted scalar, and PyYAML then
    # folds its own output on the way back in, so the value did not survive a
    # round trip through the reference writer. Escaped in a double-quoted scalar
    # it does, and the bytes on disk are a document this reader still accepts.
    # CR is the member the emitter already escaped on its own; it is asserted
    # here on the same terms rather than trusted, because "the emitter does it"
    # is a fact about a version.
    assert code_point in _ESCAPES, \
        code_point + " has no escape spelling; add one rather than skip it"
    char = chr(int(code_point[2:], 16))
    doc = {"format_version": "1.0", "assembly": {"name": "demo" + char + "x"}}
    text = cfio.dumps(doc)
    assert char not in text
    assert _ESCAPES[code_point] in text
    assert cfio.loads(text)["assembly"]["name"] == "demo" + char + "x"


@pytest.mark.parametrize("case", NOT_DELIMITABLE, ids=lambda c: c["name"])
def test_a_flow_block_the_grammar_cannot_delimit_is_not_splittable(case):
    # The split verdict for these two documents MOVED with the unattributable
    # line rule, and it moved in the direction the rule exists for. Both carry a
    # line at column zero that no top-level key owns (`flow :`, and a whole
    # document written in flow style), so what used to be "the split succeeds and
    # simply has no flow block" is now "nobody can say who owns those bytes":
    # YAML reads a `flow` key from both and the grammar reads none, which is the
    # split-versus-parse disagreement, not a missing convenience.
    #
    # The document still LOADS (asserted just above, flow rule 1), and the C++
    # reference's write refusal is unchanged: `not_splittable` was already one of
    # the two ways a flow block ends up with no slice.
    with pytest.raises(cfio.ChipletFormatError) as excinfo:
        cfio.top_level_blocks(case["doc"])
    assert "column zero" in str(excinfo.value), str(excinfo.value)


# --- (c) the floor guard: the set is DERIVED, never written down -----------
def _smuggle_document(char):
    """The shape the whole rule exists for: a top-level key hidden behind CHAR.

    One line to the LF-only grammar, two to a parser that breaks on ``char``.
    """
    return "a: demo" + char + "b: 2\n"


def _content_document(char):
    """The control shape: the same separator with no key hiding behind it."""
    return "a: demo" + char + "tail\n"


def test_the_forbidden_set_is_derived_from_the_parser_and_not_written_down():
    # META-4, and the reason this test exists: the set was carried by hand from
    # a four-member finding into a three-member constant, and every green in the
    # repository stayed green because every one of them read the constant. So
    # nothing here reads it. The criterion is EXECUTED over a code-point range:
    # a character PyYAML breaks a line on that the grammar reads as content
    # smuggles a top-level key past top_level_blocks, past the repeated-key scan
    # and past every ownership guard built on either.
    import yaml  # noqa: PLC0415  (only the derivation needs the raw parser)

    smuggles, refused = set(), set()
    ordinary = 0
    for code_point in _DERIVATION_RANGE:
        char = chr(code_point)
        if char == "\n":
            continue
        smuggle = _smuggle_document(char)
        try:
            cfio._check_line_breaks(smuggle)
        except cfio.ChipletFormatError:
            refused.add(char)
        try:
            data = yaml.safe_load(smuggle)
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict) and "b" in data \
                and list(cfio.top_level_blocks(smuggle)) == ["a"]:
            smuggles.add(char)
        try:
            value = yaml.safe_load(_content_document(char))["a"]
        except yaml.YAMLError:
            value = None
        if isinstance(value, str) and char in value:
            ordinary += 1

    # The probe is not vacuous in either direction: it finds smugglers, and the
    # overwhelming majority of the range is ordinary content to both readings.
    assert smuggles, "the derivation found nothing, so the probe is broken"
    assert ordinary > 8000, ordinary
    # The reader refuses exactly what the parser smuggles. Not a superset, which
    # would refuse documents nobody can attack with, and not a subset, which is
    # the defect this test was written for.
    assert refused == smuggles, {
        "refused, not smuggled": sorted("U+%04X" % ord(c)
                                        for c in refused - smuggles),
        "smuggled, not refused": sorted("U+%04X" % ord(c)
                                        for c in smuggles - refused),
    }
    # And the oracle every other implementation runs names the same set.
    assert FORBIDDEN_CODE_POINTS == sorted("U+%04X" % ord(c) for c in smuggles)


def test_crlf_survives_the_carriage_return_rule():
    # CR is the one conditional member, so the rule has to be measured from both
    # sides on a real document: a CRLF file is ordinary and loses nothing, and
    # the same file with a single LF taken out of a terminator is refused. The
    # split verdict for a CRLF document is asserted elsewhere; this is the load.
    case = next(c for c in SPLITS if "crlf" in c["name"])
    assert "\r\n" in case["doc"]
    assert cfio.loads(case["doc"])["assembly"]["name"] == "demo"
    broken = case["doc"].replace("\r\n", "\r", 1)
    with pytest.raises(cfio.ChipletFormatError) as excinfo:
        cfio.loads(broken)
    assert "U+000D" in str(excinfo.value), str(excinfo.value)
    assert "line 1" in str(excinfo.value), str(excinfo.value)


def test_a_carriage_return_at_end_of_file_is_refused_by_decision():
    # The case the spec decides rather than inherits. Both parsers agree here
    # (they drop the CR), so nothing forces the refusal; what forces it is that
    # the rule stays one property of the bytes, and that both line splitters pop
    # a trailing CR whether or not an LF follows, so accepting the document means
    # reading a byte less than the file holds and never saying so.
    case = next(c for c in FORBIDDEN_LINE_BREAK
                if c["name"] == "carriage_return_at_end_of_file")
    assert case["doc"].endswith("\r")
    with pytest.raises(cfio.ChipletFormatError) as excinfo:
        cfio.loads(case["doc"])
    assert "end of file" in str(excinfo.value), str(excinfo.value)
    # The line splitter's own view, which is what the decision is about: it pops
    # the CR, so the grammar reads a line the file does not literally end with.
    assert list(cfio.top_level_blocks(case["doc"])) == ["format_version",
                                                        "assembly"]


# --- (d) the splits group's load verdict, which nothing used to check ------
@pytest.mark.parametrize("case", SPLITS_LOAD, ids=lambda c: c["name"])
def test_a_splits_case_without_the_flag_loads(case):
    # The half that was missing. Nothing asserted that a splits case WITHOUT
    # "loadable": false loads, so the flag was documentation: a future
    # forbidden-character case could be parked under splits and never meet a
    # load verdict at all, and one row already claimed a refusal that does not
    # happen. Both halves are executed now.
    assert cfio.loads(case["doc"], validate=False)


@pytest.mark.parametrize("case", SPLITS_REFUSED, ids=lambda c: c["name"])
def test_a_splits_case_with_the_flag_is_refused(case):
    # And the flag means refused, in both readers, not "not required to load".
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads(case["doc"], validate=False)


def test_the_escaped_spelling_is_the_way_out_and_it_loads():
    # The discriminating control for the whole line-break rule: the refusal is on
    # the RAW bytes, so a value that genuinely needs one of these characters is
    # written escaped and nothing is lost. Without a case that LOADS, the rule
    # and a blanket ban on the character look the same from the outside.
    case = next(c for c in SPLITS if c["name"] ==
                "escaped_line_break_in_a_double_quoted_scalar_is_ordinary_text")
    assert "\u2028" not in case["doc"], "the control must carry no raw character"
    assert "\\L" in case["doc"]
    assert cfio.loads(case["doc"])["assembly"]["name"] == "demo\u2028x"
    # And the grammar reads it as ordinary text: the escape is two characters to
    # everything that splits on LF, which is the whole reason it is the way out.
    assert list(cfio.top_level_blocks(case["doc"])) == [b["key"]
                                                        for b in case["blocks"]]


@pytest.mark.parametrize("code_point", FORBIDDEN_CODE_POINTS)
def test_every_refused_character_has_a_legal_escaped_spelling(code_point):
    # The way out exists for each member, not just the one the oracle carries as
    # a document. A refusal with no way out would push a producer to drop the
    # value, which is the data loss these rules exist to prevent.
    char = chr(int(code_point[2:], 16))
    doc = 'format_version: "1.0"\nassembly:\n  name: "demo%sx"\n' % _ESCAPES[
        code_point]
    assert char not in doc
    assert cfio.loads(doc)["assembly"]["name"] == "demo" + char + "x"


def test_the_writer_never_emits_the_escape_the_other_reader_gets_wrong():
    # The writer half of the rule above, and the reason this test exists rather
    # than a comment: PyYAML's emitter picks "\\N" for U+0085 by itself, so the
    # correct spelling is not what happens by default and a future dumper change
    # would silently restore the broken one. Asserted on the emitted TEXT, not on
    # a round trip through this reader, because this reader decodes both
    # spellings identically and so cannot tell them apart. That is the point: the
    # reader that could tell them apart is the C++ one, and its half is asserted
    # in reference/cpp/tests.
    import io
    text = cfio.dumps({"format_version": "1.0",
                       "assembly": {"name": "a\u0085b"},
                       "components": []}, validate=False)
    assert "\\x85" in text
    assert "\\N" not in text, \
        "the emitter fell back to \\N, which yaml-cpp reads as a bare 0x85"
    assert "\u0085" not in text, "the raw character must never reach the file"
    assert cfio.loads(text)["assembly"]["name"] == "a\u0085b"


@pytest.mark.parametrize("code_point,escape",
                         [("U+2028", "\\L"), ("U+2029", "\\P")])
def test_the_other_two_escapes_are_deliberately_unchanged(code_point, escape):
    # The floor guard for the narrow fix: LS and PS round-trip correctly through
    # both readers, so changing them would churn every existing document for no
    # gain. Without this, "use the hex form everywhere" reads like a tidy-up.
    text = cfio.dumps({"format_version": "1.0",
                       "assembly": {"name": "a" + chr(int(code_point[2:], 16)) + "b"},
                       "components": []}, validate=False)
    assert escape in text


# --- (e) SPEC-41: the writer must not emit what this splitter refuses ------
@pytest.mark.parametrize("case", UNATTRIBUTABLE, ids=lambda c: c["name"])
def test_the_unattributable_refusal_names_the_line_and_the_way_out(case):
    # The message is the assertion, on the same terms as the line-break rule:
    # all three spellings are ordinary-looking YAML, so a refusal that did not
    # quote the line and name the spellings would send the author looking at the
    # wrong part of the file. The two spellings named are the ones our own writer
    # produced, which is what makes this a defect report rather than a style rule.
    with pytest.raises(cfio.ChipletFormatError) as excinfo:
        cfio.top_level_blocks(case["doc"])
    message = str(excinfo.value)
    assert "unattributable line at column zero" in message, message
    offending = next(ln for ln in case["doc"].split("\n")
                     if cfio._is_unattributable(ln))
    assert repr(offending) in message, message
    assert "? ..." in message and "a b:" in message, message
    assert "docs/CHIPLET_FORMAT_SPEC.md" in message, message
    assert "line grammar cannot establish ownership" in message, message
    assert "a YAML parser reads a top-level key the split never saw" not in message


@pytest.mark.parametrize("case", SPLITTER_ONLY, ids=lambda c: c["name"])
def test_the_writer_never_reproduces_a_document_the_splitter_refuses(case):
    # SPEC-41 stated as one property over the whole group: whatever this writer
    # emits, the splitter every host runs must recover exactly the top-level keys
    # the writer was given. The writer meets that either by writing a splittable
    # document (a quoted key comes back bare, and a tab inside a scalar was never
    # a top-level key at all) or by refusing; what it may not do is what it used
    # to do, which is emit a third document its own splitter reads differently.
    data = cfio.loads(case["doc"], validate=False)
    try:
        text = cfio.dumps(data, validate=False)
    except cfio.ChipletFormatError as excinfo:
        assert "cannot be written as a key line" in str(excinfo)
        return
    assert list(cfio.top_level_blocks(text)) == list(data)


def test_the_writer_puts_sequence_entries_at_column_zero():
    # Why the sequence-entry exemption is in the rule rather than a tidy
    # afterthought: this writer PUTS those lines at column zero, so a rule that
    # called every non-key line there unattributable would refuse to split the
    # documents this repository itself produces. Measured on the emitter rather
    # than asserted about it, because the indentation is PyYAML's default and a
    # default is a fact about a version.
    case = next(c for c in SPLITS if c["name"] ==
                "components_block_with_sequence_entries_at_column_zero")
    text = cfio.dumps(cfio.loads(case["doc"], validate=False), validate=False)
    entries = [ln for ln in text.split("\n") if ln.startswith("- ")]
    assert entries, "the emitter stopped writing sequence entries at column zero"
    assert list(cfio.top_level_blocks(text)) == [b["key"] for b in
                                                 case["blocks"]]
