# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the one version policy (docs/VERSION_POLICY.md).

Three parts, because the policy has three.

The RULE half is the reference implementation,
``chiplet_format_io.check_contract_version``: same major with a minor at or below
supported accepted, same-major higher minor accepted with a warning, different
major or malformed refused, PATCH ignored. That is the whole of "which versions a
consumer accepts", and it lives in code, not in a schema.

The STRUCTURAL half is the five governed sidecar schemas. Each one's version field
is a quoted MAJOR.MINOR(.PATCH) string and nothing more: the schemas deliberately
do NOT enumerate accepted versions, so a same-major minor bump does not require
every consumer to ship a new schema first. A test that a different major still
validates structurally is therefore a positive, not an oversight; the refusal is
tested on the checker, above.

The PARITY part is the two reference implementations. A consumer that vendors a
reader pins a reader RELEASE, not a document version, so the Python and C++
references must ship one release number between them; a C++ mirror whose release
constant drifts from ``chiplet_format_io.__version__`` would force every C++
consumer back to byte comparison, which is the very thing the release constant
exists to end. The C++ side cannot see Python and Python cannot see the compiled
constant, so the agreement is checked here, on the text of both files.

"""
import copy
import json
import re
import sys
import warnings
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

import chiplet_format_io as cfio  # noqa: E402

SCHEMAS = ROOT / "schemas"
EXAMPLES = SCHEMAS / "examples"

#: The single pattern every governed sidecar uses for its version field. Written
#: out here rather than read from one of the schemas: this file is the place that
#: says what the pattern must be, so a schema that drifts fails instead of
#: redefining the expectation.
VERSION_PATTERN = r"^[0-9]+\.[0-9]+(\.[0-9]+)?(?![\s\S])"

#: schema file -> the key that carries the contract version in it. The key is
#: spelled ``schema_version`` in the interconnect registry for historical reasons;
#: it is the same contract version, under the same policy.
VERSIONED_SIDECARS = {
    "io_pads.schema.json": "version",
    "pins.schema.json": "version",
    "blackbox_padmap.schema.json": "version",
    "boundary_manifest.schema.json": "version",
    "interconnect_methods.schema.json": "schema_version",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(schema: dict):
    cls = validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def _valid(schema: dict, instance) -> bool:
    return _validator(schema).is_valid(instance)


def _version_subschema(schema_file: str) -> dict:
    """The version field's own subschema, as a standalone schema.

    Used for the two sidecars that have no committed example instance
    (boundary manifest, interconnect registry): building a full valid instance of
    those would pin far more of their shape than this test is about. For the three
    that do have examples, the full document is exercised as well, below.
    """
    schema = _load(SCHEMAS / schema_file)
    key = VERSIONED_SIDECARS[schema_file]
    sub = copy.deepcopy(schema["properties"][key])
    sub["$schema"] = schema["$schema"]
    return sub


@pytest.fixture(autouse=True)
def _no_leaked_warn_state():
    """The checker warns once per (name, version); do not leak that across cases."""
    cfio._reset_version_warnings()
    yield
    cfio._reset_version_warnings()


# --- the rule: accepted ----------------------------------------------------
@pytest.mark.parametrize("declared,expected", [
    ("1.0", "1.0"),
    ("1.0.0", "1.0"),      # existing emitters write this; PATCH is ignored
    ("1.0.7", "1.0"),      # any patch, same verdict
    ("1.1", "1.1"),        # at supported
])
def test_same_major_at_or_below_supported_is_accepted(declared, expected):
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a silent accept must stay silent
        assert cfio.check_contract_version(
            declared, "1.1", name="io_pads.json") == expected


def test_patch_never_changes_the_verdict():
    # The point of ignoring PATCH: same answer with and without it.
    assert (cfio.check_contract_version("1.0", "1.0", name="pins.json")
            == cfio.check_contract_version("1.0.99", "1.0", name="pins.json"))


# --- the rule: higher minor accepted, with a warning -----------------------
def test_higher_minor_is_accepted_with_a_warning():
    with pytest.warns(cfio.ContractVersionWarning) as record:
        got = cfio.check_contract_version("1.4", "1.0", name="io_pads.json")
    assert got == "1.4"  # normalized, not clamped: the caller sees what it read
    assert "io_pads.json" in str(record[0].message)


def test_higher_minor_warning_reaches_the_on_warn_callback():
    seen = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfio.check_contract_version("1.4", "1.0", name="pins.json",
                                    on_warn=seen.append)
    assert len(seen) == 1 and "pins.json" in seen[0]


def test_the_warning_is_deduped_per_artifact_not_globally():
    # Two sidecars at the same newer minor must both be heard; one must not
    # suppress the other by sharing the warn-once key.
    with pytest.warns(cfio.ContractVersionWarning) as record:
        cfio.check_contract_version("1.4", "1.0", name="io_pads.json")
        cfio.check_contract_version("1.4", "1.0", name="pins.json")
        cfio.check_contract_version("1.4", "1.0", name="io_pads.json")  # deduped
    assert len(record) == 2


def test_format_version_warning_is_a_contract_version_warning():
    # A consumer that wants every governed artifact catches the general class;
    # the .chiplet-specific class stays catchable by name for existing filters.
    assert issubclass(cfio.FormatVersionWarning, cfio.ContractVersionWarning)


# --- the rule: refused -----------------------------------------------------
@pytest.mark.parametrize("declared", ["2.0", "2.0.0", "0.9", "10.0"])
def test_a_different_major_is_refused(declared):
    # Lower majors too: an older major is a different document shape, and
    # half-parsing it is worse than refusing it.
    with pytest.raises(cfio.ContractVersionError):
        cfio.check_contract_version(declared, "1.0", name="io_pads.json")


@pytest.mark.parametrize("declared", [
    None,            # missing key
    "",
    "1",             # MAJOR alone is not a version
    "1.",
    "1.0.0.0",
    "v1.0",
    "1.0-rc1",
    "one.zero",
    "-1.0",
    1,               # a JSON number is not a quoted version
    1.0,             # and 1.10 would already have become 1.1
    ["1", "0"],
    {"major": 1},
    True,
    " 1.0",          # surrounding whitespace is malformed, not a version (SPEC-2)
    "1.0 ",
    "1.0\n",
    "1.0.0 ",
])
def test_a_malformed_version_is_refused(declared):
    with pytest.raises(cfio.ContractVersionError):
        cfio.check_contract_version(declared, "1.0", name="pins.json")


def test_the_refusal_names_the_artifact():
    with pytest.raises(cfio.ContractVersionError) as excinfo:
        cfio.check_contract_version("2.0.0", "1.0", name="interconnect_methods.json")
    assert "interconnect_methods.json" in str(excinfo.value)


def test_the_error_is_catchable_as_the_general_format_error():
    # A consumer that already catches ChipletFormatError keeps working.
    assert issubclass(cfio.ContractVersionError, cfio.ChipletFormatError)
    with pytest.raises(cfio.ChipletFormatError):
        cfio.check_contract_version("2.0", "1.0", name="io_pads.json")


def test_a_malformed_supported_argument_is_a_caller_bug_not_a_data_error():
    # Wrong-data raises ContractVersionError; wrong-call raises ValueError, so a
    # typo in a consumer never reads as "the file is bad".
    with pytest.raises(ValueError) as excinfo:
        cfio.check_contract_version("1.0", "1", name="io_pads.json")
    assert not isinstance(excinfo.value, cfio.ContractVersionError)


def test_the_legacy_pins_integer_must_be_normalized_by_the_consumer():
    # pins.schema.json still accepts the bare integer 1 (the legacy spelling of
    # "1.0", deprecated per docs/VERSION_POLICY.md). The checker does not: a JSON
    # number cannot carry a minor. A consumer reading a legacy pin list maps 1 to
    # "1.0" before applying the policy, and that mapping is the only place the
    # legacy spelling is understood.
    with pytest.raises(cfio.ContractVersionError):
        cfio.check_contract_version(1, "1.0", name="pins.json")
    assert cfio.check_contract_version("1.0", "1.0", name="pins.json") == "1.0"


# --- the structural half: one pattern, five schemas ------------------------
def test_every_governed_sidecar_uses_the_same_version_pattern():
    for schema_file, key in VERSIONED_SIDECARS.items():
        schema = _load(SCHEMAS / schema_file)
        prop = schema["properties"][key]
        # pins keeps a oneOf for its legacy integer spelling; the string branch
        # still carries the shared pattern.
        branches = prop.get("oneOf", [prop])
        patterns = [b.get("pattern") for b in branches if b.get("type") == "string"]
        assert patterns == [VERSION_PATTERN], schema_file
        text = json.dumps(prop)
        assert "VERSION_POLICY.md" in text, schema_file


@pytest.mark.parametrize("schema_file", sorted(VERSIONED_SIDECARS))
@pytest.mark.parametrize("declared", ["1.0", "1.0.0", "1.10", "2.0.0"])
def test_a_wellformed_version_string_is_structurally_valid(schema_file, declared):
    # Including a different major: the schema is structural, the refusal is the
    # checker's job. Enumerating versions in the schema would mean every consumer
    # needs a new schema before it may read a compatible minor bump.
    assert _valid(_version_subschema(schema_file), declared)


@pytest.mark.parametrize("schema_file", sorted(VERSIONED_SIDECARS))
@pytest.mark.parametrize("declared", ["1", "1.", "1.0.0.0", "v1.0", "1.0-rc1",
                                      "", "one.zero", [1, 0], {"major": 1},
                                      # a trailing newline is what $ lets through
                                      # under Python re (SPEC-11)
                                      "1.0\n", " 1.0", "1.0 "])
def test_a_malformed_version_is_structurally_rejected(schema_file, declared):
    assert not _valid(_version_subschema(schema_file), declared)


def test_only_pins_still_accepts_the_legacy_integer_spelling():
    for schema_file in VERSIONED_SIDECARS:
        accepted = _valid(_version_subschema(schema_file), 1)
        assert accepted == (schema_file == "pins.schema.json"), schema_file


def test_an_unquoted_number_is_only_readable_where_the_legacy_branch_survives():
    # One more reason the rule says QUOTED: JSON Schema calls 1.0 an integer
    # (draft-06 onward, zero fractional part), so the deprecated pins branch
    # cannot tell 1.0 from 1 and neither can a consumer. Every schema that has
    # only the string branch rejects it outright, which is the end state.
    for schema_file in VERSIONED_SIDECARS:
        accepted = _valid(_version_subschema(schema_file), 1.0)
        assert accepted == (schema_file == "pins.schema.json"), schema_file
    # The checker refuses the number under every spelling regardless.
    for number in (1, 1.0):
        with pytest.raises(cfio.ContractVersionError):
            cfio.check_contract_version(number, "1.0", name="pins.json")


# --- the structural half, on whole documents where an example exists -------
IO_PADS_SCHEMA = _load(SCHEMAS / "io_pads.schema.json")
PINS_SCHEMA = _load(SCHEMAS / "pins.schema.json")
PADMAP_SCHEMA = _load(SCHEMAS / "blackbox_padmap.schema.json")
IO_PADS_EXAMPLE = _load(EXAMPLES / "io_pads.board_absolute.example.json")
PINS_EXAMPLE = _load(EXAMPLES / "pins.example.json")
PADMAP_EXAMPLE = _load(EXAMPLES / "blackbox_padmap.example.json")


def test_the_committed_examples_still_declare_a_policy_conformant_version():
    # The tripwire for the change itself: the shipped emitters write "1.0.0" and
    # the integer 1, and both must survive it.
    assert _valid(IO_PADS_SCHEMA, IO_PADS_EXAMPLE)
    assert cfio.check_contract_version(
        IO_PADS_EXAMPLE["version"], "1.0", name="io_pads.json") == "1.0"
    assert _valid(PINS_SCHEMA, PINS_EXAMPLE)


@pytest.mark.parametrize("declared", ["1.1", "1.1.0", "2.0.0"])
def test_a_minor_or_major_bump_keeps_io_pads_documents_valid(declared):
    doc = copy.deepcopy(IO_PADS_EXAMPLE)
    doc["version"] = declared
    assert _valid(IO_PADS_SCHEMA, doc)


@pytest.mark.parametrize("declared", ["1", "1.0.0.0", "latest", 1, 1.0])
def test_a_malformed_version_rejects_the_whole_io_pads_document(declared):
    doc = copy.deepcopy(IO_PADS_EXAMPLE)
    doc["version"] = declared
    assert not _valid(IO_PADS_SCHEMA, doc)


@pytest.mark.parametrize("declared", ["1.0", "1.0.0", 1])
def test_pins_accepts_both_spellings_on_a_whole_document(declared):
    doc = copy.deepcopy(PINS_EXAMPLE)
    doc["version"] = declared
    assert _valid(PINS_SCHEMA, doc)


@pytest.mark.parametrize("declared", ["1", "v1", 2, 2.0])
def test_pins_rejects_a_malformed_or_unknown_legacy_version(declared):
    # The legacy integer branch stays pinned to 1: it is a deprecated spelling of
    # one specific version, not a second numeric version axis.
    doc = copy.deepcopy(PINS_EXAMPLE)
    doc["version"] = declared
    assert not _valid(PINS_SCHEMA, doc)


def test_the_padmap_version_is_optional_but_checked_when_present():
    # This artifact shipped unversioned; requiring a version would invalidate
    # every padmap already on disk. Absent means 1.0.
    doc = copy.deepcopy(PADMAP_EXAMPLE)
    doc.pop("version", None)
    assert _valid(PADMAP_SCHEMA, doc)
    doc["version"] = "1.0.0"
    assert _valid(PADMAP_SCHEMA, doc)
    doc["version"] = "1"
    assert not _valid(PADMAP_SCHEMA, doc)


# --- the vendored-reader identity ------------------------------------------
def test_both_version_constants_exist_and_parse():
    # The two answer different questions (which documents / which reader), so
    # both must be present and both must be readable by the same rule. A vendored
    # copy that carries neither leaves a consumer with only byte comparison.
    assert cfio.check_contract_version(
        cfio.SUPPORTED_FORMAT_VERSION, cfio.SUPPORTED_FORMAT_VERSION,
        name="SUPPORTED_FORMAT_VERSION") == "1.0"
    major_minor = cfio.check_contract_version(
        cfio.__version__, cfio.__version__, name="chiplet_format_io.__version__")
    assert major_minor == ".".join(cfio.__version__.split(".")[:2])
    assert len(cfio.__version__.split(".")) == 3  # readers ship a full MAJOR.MINOR.PATCH


def test_the_reader_release_is_exported():
    assert "__version__" in cfio.__all__
    assert "SUPPORTED_FORMAT_VERSION" in cfio.__all__


def test_the_installed_distribution_agrees_with_the_module():
    # pyproject.toml reads the version off the module, so an installed package
    # and a vendored copy of the file can never disagree. Skipped rather than
    # failed when the suite runs off the source tree with nothing installed.
    metadata = pytest.importorskip("importlib.metadata")
    try:
        installed = metadata.version("chiplet-format-io")
    except metadata.PackageNotFoundError:
        pytest.skip("chiplet-format-io is not installed in this environment")
    assert installed == cfio.__version__


# --- the reference implementations ship one reader release -------------------
CPP_HEADER = ROOT / "reference" / "cpp" / "include" / "chiplet_format_io" / "chiplet_format_io.hpp"
CPP_CMAKE = ROOT / "reference" / "cpp" / "CMakeLists.txt"


def _cpp_reader_release() -> str:
    text = CPP_HEADER.read_text(encoding="utf-8")
    found = re.search(r'READER_RELEASE\s*=\s*"([^"]+)"', text)
    assert found, f"no READER_RELEASE constant in {CPP_HEADER}"
    return found.group(1)


def _cmake_project_version() -> str:
    text = CPP_CMAKE.read_text(encoding="utf-8")
    found = re.search(r"project\([^)]*\bVERSION\s+([0-9][^\s)]*)", text)
    assert found, f"no project(... VERSION ...) in {CPP_CMAKE}"
    return found.group(1)


def test_the_cpp_reader_declares_the_same_release_as_python():
    # The whole point of the constant: a vendored C++ copy is gateable by
    # version. That only holds while both references answer the same number.
    assert _cpp_reader_release() == cfio.__version__


def test_the_cmake_project_version_is_the_reader_release():
    # Package metadata is a third place the release can rot. pyproject reads the
    # module, so Python cannot drift; CMake has its own literal, so it can.
    assert _cmake_project_version() == cfio.__version__


def test_the_cpp_supported_format_version_agrees_too():
    # The document-side constant has always been duplicated across the two
    # references; nothing gated it until now.
    text = CPP_HEADER.read_text(encoding="utf-8")
    found = re.search(r'SUPPORTED_FORMAT_VERSION\s*=\s*"([^"]+)"', text)
    assert found, f"no SUPPORTED_FORMAT_VERSION constant in {CPP_HEADER}"
    assert found.group(1) == cfio.SUPPORTED_FORMAT_VERSION


def test_the_cpp_source_stamp_and_accepted_set_agree_too():
    # Format 1.1 split the stamp per document kind and moved the accepted floor
    # to the highest minor implemented; the C++ copies of both are gated here.
    text = CPP_HEADER.read_text(encoding="utf-8")
    found = re.search(r'SOURCE_FORMAT_VERSION\s*=\s*"([^"]+)"', text)
    assert found, f"no SOURCE_FORMAT_VERSION constant in {CPP_HEADER}"
    assert found.group(1) == cfio.SOURCE_FORMAT_VERSION
    found = re.search(r'ACCEPTED_FORMAT_VERSIONS\s*\{\{([^}]*)\}\}', text)
    assert found, f"no ACCEPTED_FORMAT_VERSIONS array in {CPP_HEADER}"
    assert tuple(re.findall(r'"([^"]+)"', found.group(1))) == \
        cfio.ACCEPTED_FORMAT_VERSIONS


def test_the_cpp_release_obeys_the_shared_version_policy():
    release = _cpp_reader_release()
    assert len(release.split(".")) == 3  # readers ship a full MAJOR.MINOR.PATCH
    assert cfio.check_contract_version(
        release, cfio.__version__, name="C++ READER_RELEASE") == ".".join(
            release.split(".")[:2])

# --- reader parity: Python must not be laxer than C++ ----------------------
# The two reference readers are the same contract in two languages, so a string
# one accepts and the other throws on is a defect no matter which is "nicer".
# The C++ parse_version_parts has always required ASCII digits on both sides of
# the single dot; the Python one used int(), which accepts a sign, underscore
# separators, surrounding whitespace and non-ASCII digits. Those five strings
# loaded in Python and threw in C++, and no fixture used one, so the corpus
# never saw it. Pinned here in the direction of the stricter reader, which is
# also the direction of the schema pattern.
CPP_REJECTS_THESE = ("+1.0", "1.0_0", "1. 0", " 1.0 ", "1.\u0660", "1.0\n")


@pytest.mark.parametrize("fv", CPP_REJECTS_THESE)
def test_python_rejects_every_spelling_the_cpp_reader_rejects(fv):
    assert cfio.parse_format_version(fv) is None, fv


@pytest.mark.parametrize("fv", ["1.0", "1.10", "0.9", "10.0"])
def test_the_spellings_the_spec_actually_defines_still_parse(fv):
    # The tightening must not cost a single legal document: MAJOR.MINOR, ASCII
    # digits, no padding. Multi-digit on both sides, because "1.10" is the
    # case the unquoted-YAML divergence has always turned on.
    assert cfio.parse_format_version(fv) is not None, fv


def test_the_unquoted_number_coercion_survives_the_tightening():
    # The one tolerance the docstring justifies is an unquoted 1.0 in YAML
    # arriving as a float. That is a YAML spelling, not an int() quirk, and it
    # stays: removing it would break real documents, unlike the five above.
    assert cfio.parse_format_version(1.0) == (1, 0)
    assert cfio.parse_format_version(2) is None  # still not MAJOR.MINOR


# --- escalation must be consistent, not first-read-only --------------------
def test_escalating_host_refuses_every_read_not_just_the_first():
    # With the dedup key recorded BEFORE warnings.warn, a host running
    # warnings-as-errors saw read 1 raise and reads 2 and 3 succeed silently:
    # the verdict moved depending on who read the artifact first. Found by the
    # kicad-plugin session against interconnect_pdk's reader; the reference
    # reader had the same ordering. Under escalation every read must raise.
    cfio._reset_version_warnings()
    with warnings.catch_warnings():
        warnings.simplefilter("error", cfio.ContractVersionWarning)
        for _ in range(3):
            with pytest.raises(cfio.ContractVersionWarning):
                cfio.check_contract_version("1.7", "1.0", name="io_pads.json")
        for _ in range(3):
            with pytest.raises(cfio.FormatVersionWarning):
                cfio.check_format_version("1.7")


def test_non_escalating_host_still_warns_exactly_once():
    cfio._reset_version_warnings()
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        for _ in range(3):
            cfio.check_contract_version("1.7", "1.0", name="io_pads.json")
    assert sum(isinstance(w.message, cfio.ContractVersionWarning) for w in seen) == 1


# --- the transition window: one oracle, both readers (SPEC-21) --------------
#
# conformance/fixtures/version_policy_cases.json is the shared verdict oracle.
# The rows are run here against the Python check_contract_version and in
# reference/cpp/tests against the C++ one, so the two references are measured
# against the file and never against each other.
#
# What a green over the oracle does NOT cover (META-2): warning DELIVERY. A row
# says a higher minor is accepted and reported; which channel carries it, how it
# is deduplicated, and what a host running warnings-as-errors sees are covered by
# the tests above and by nothing in the oracle. It also covers no I/O: the rows
# are versions and verdicts, never documents.
ORACLE = ROOT / "conformance" / "fixtures" / "version_policy_cases.json"
ORACLE_CASES = _load(ORACLE)["cases"]
VERDICTS = {"accept", "accept_warn", "refuse", "call_error"}
KINDS = {"string": str, "number": (int, float), "null": type(None), "list": list}


def _case_id(case):
    return case["name"].replace(" ", "_").replace(",", "").replace(":", "")


def test_the_oracle_is_wellformed():
    # An oracle nobody checks is a place for typos to hide: a row with a verdict
    # nobody implements, or a declared_kind that lies about the JSON type, would
    # pass silently as "one more green row".
    assert ORACLE_CASES, "the oracle parsed to nothing"
    names = [c["name"] for c in ORACLE_CASES]
    assert len(set(names)) == len(names), "two oracle rows share a name"
    for case in ORACLE_CASES:
        for key in ("name", "accepted", "declared", "declared_kind", "verdict",
                    "why"):
            assert key in case, f"{case.get('name')}: missing {key}"
        assert case["verdict"] in VERDICTS, case["name"]
        assert isinstance(case["accepted"], list), case["name"]
        assert isinstance(case["declared_kind"], str), case["name"]
        assert case["declared_kind"] in KINDS, case["name"]
        assert isinstance(case["declared"], KINDS[case["declared_kind"]]), \
            f"{case['name']}: declared_kind {case['declared_kind']!r} is a lie"
        assert case["why"].strip(), case["name"]
        if case["verdict"] in ("accept", "accept_warn"):
            assert "normalized" in case, case["name"]
        if case["verdict"] == "refuse" and "names_majors" in case:
            assert case["names_majors"], case["name"]


def test_the_oracle_covers_every_shape_the_policy_names():
    # The coverage the brief asked for, asserted rather than eyeballed: one
    # major and two, each of lower/equal/higher minor, a missing major, a lower
    # major, a duplicate accepted major, a malformed value, a bare number and a
    # patch. A row deleted from the file has to fail here, not shrink the gate.
    by_verdict = {}
    for case in ORACLE_CASES:
        by_verdict.setdefault(case["verdict"], []).append(case)
    assert VERDICTS <= set(by_verdict), "a verdict of the policy has no row"
    one_major = [c for c in ORACLE_CASES if len(c["accepted"]) == 1]
    two_majors = [c for c in ORACLE_CASES if len(c["accepted"]) == 2
                  and c["verdict"] != "call_error"]
    assert one_major and two_majors
    for group in (one_major, two_majors):
        verdicts = {c["verdict"] for c in group}
        assert {"accept", "accept_warn", "refuse"} <= verdicts
    # per-major minors, on the two-major rows: below, at and above a floor
    normalized = {c["normalized"] for c in two_majors if "normalized" in c}
    assert {"2.1", "2.3", "2.9"} <= normalized, \
        "the second major is not exercised below, at and above its floor"
    # a lower major, a missing one, and a refusal that names the whole window
    assert any(c["declared"] == "0.9" for c in ORACLE_CASES)
    assert any(len(c.get("names_majors", [])) == 2 for c in ORACLE_CASES)
    assert any(c["declared_kind"] == "number" for c in ORACLE_CASES)
    assert any(isinstance(c["declared"], str) and c["declared"].count(".") == 2
               for c in ORACLE_CASES)
    assert any(c["verdict"] == "call_error" and len(c["accepted"]) == 2
               for c in ORACLE_CASES)


@pytest.mark.parametrize("case", ORACLE_CASES, ids=_case_id)
def test_the_python_reader_gives_the_oracle_verdict(case):
    accepted = case["accepted"]
    declared = case["declared"]
    verdict = case["verdict"]
    if verdict == "call_error":
        # A programming error in the SET, refused at call time: ValueError and
        # deliberately not ContractVersionError, so a consumer's typo never
        # reads as "the artifact is bad".
        with pytest.raises(ValueError) as excinfo:
            cfio.check_contract_version(declared, accepted, name="io_pads.json")
        assert not isinstance(excinfo.value, cfio.ContractVersionError), \
            case["name"]
        return
    if verdict == "refuse":
        with pytest.raises(cfio.ContractVersionError) as excinfo:
            cfio.check_contract_version(declared, accepted, name="io_pads.json")
        message = str(excinfo.value)
        assert "io_pads.json" in message
        for major in case.get("names_majors", []):
            assert str(major) in message, f"{case['name']}: {message}"
            assert all(spelling in message for spelling in accepted), message
        return
    if verdict == "accept":
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # a silent accept must stay silent
            got = cfio.check_contract_version(declared, accepted,
                                              name="io_pads.json")
    else:
        with pytest.warns(cfio.ContractVersionWarning):
            got = cfio.check_contract_version(declared, accepted,
                                              name="io_pads.json")
    assert got == case["normalized"], case["name"]


def test_the_single_string_form_is_the_one_element_set():
    # The compatibility promise of the change: every caller that passes a bare
    # string keeps its exact verdict, because a string IS the one-element set.
    for case in ORACLE_CASES:
        if len(case["accepted"]) != 1 or case["verdict"] == "call_error":
            continue
        one = case["accepted"][0]
        as_set = as_str = None
        for form in (case["accepted"], one):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    got = ("ok", cfio.check_contract_version(
                        case["declared"], form, name="io_pads.json"))
            except cfio.ContractVersionError as exc:
                got = ("refuse", str(exc))
            if form is one:
                as_str = got
            else:
                as_set = got
        assert as_set == as_str, case["name"]


def test_every_writer_stamp_reads_back_silently():
    # The half of the constant pair a comment cannot keep: what the writer
    # stamps has to be something this reader reads without a warning, or the
    # library emits documents it refuses (or warns on) when read back. There is
    # one stamp per document kind (assembly, source), and an ACCEPTED entry is
    # the highest minor implemented, so a stamp at or below it is readable; it
    # need not be a member. The C++ side asserts the same at compile time.
    for stamp in (cfio.SUPPORTED_FORMAT_VERSION, cfio.SOURCE_FORMAT_VERSION):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert cfio.check_format_version(stamp) == stamp
    assert cfio.ACCEPTED_FORMAT_VERSIONS, "a reader that accepts nothing"
    majors = [cfio.parse_format_version(v)[0] for v in cfio.ACCEPTED_FORMAT_VERSIONS]
    assert len(set(majors)) == len(majors), \
        "two entries with the same major is a programming error"


def test_the_format_version_entry_point_uses_the_accepted_set():
    # check_format_version reads ACCEPTED_FORMAT_VERSIONS rather than a private
    # copy of the supported major: a transition window opened in the tuple has
    # to change what the .chiplet entry point accepts, or the set is decoration.
    original = cfio.ACCEPTED_FORMAT_VERSIONS
    try:
        with pytest.raises(cfio.ChipletFormatError) as excinfo:
            cfio.check_format_version("2.0")
        assert "major 1" in str(excinfo.value)
        cfio.ACCEPTED_FORMAT_VERSIONS = ("1.0", "2.3")
        assert cfio.check_format_version("2.0") == "2.0"
        with pytest.raises(cfio.ChipletFormatError) as excinfo:
            cfio.check_format_version("3.0")
        assert "majors 1, 2" in str(excinfo.value)
    finally:
        cfio.ACCEPTED_FORMAT_VERSIONS = original
    assert cfio.check_format_version("1.0") == "1.0"


def test_the_policy_document_states_the_transition_window():
    # The code can only be right about a rule the document actually carries. A
    # green here says the normative text exists and says the three things a
    # consumer has to know; it says nothing about the text being GOOD, and
    # nothing about any consumer having adopted the set form (interconnect_pdk's
    # reader and interposer-pnr's ir.py have rows of their own, still open).
    policy = (ROOT / "docs" / "VERSION_POLICY.md").read_text(encoding="utf-8")
    assert "## Changing the major" in policy
    section = policy.split("## Changing the major", 1)[1].split("\n## ", 1)[0]
    assert "SET of majors" in section
    assert "naming EVERY accepted major" in section
    for phrase in ("PROGRAMMING error", "call time"):
        assert phrase in section, phrase
    for step in ("Consumers add the new major", "Producers switch to the new",
                 "consumers drop the old"):
        assert step in section, step
    assert "a MINOR only adds what a consumer can ignore and remain correct" \
        in policy


# --- the retroactive sweep (SPEC-21, pass two) ------------------------------
def _sweep_rows():
    policy = (ROOT / "docs" / "VERSION_POLICY.md").read_text(encoding="utf-8")
    assert "## Unversioned corrections since 1.0" in policy, \
        "the sweep table is gone from the policy"
    section = policy.split("## Unversioned corrections since 1.0", 1)[1]
    rows = []
    for line in section.split("\n"):
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) != 3 or set(cells[0]) <= set("- "):
            continue
        if cells[0] == "Change":
            continue
        rows.append(cells)
    return rows


def test_every_sweep_row_names_a_change_a_commit_and_a_reason():
    """The sweep table stays a table of facts, not a list of headings.

    What a green here does NOT cover: whether a justification is CORRECT. It
    checks that every row carries all three cells, that every seven-hex token in
    the commits column is a real commit in this repository, and that the two
    rows with wording ruled elsewhere still carry it. Judging a justification is
    a review, and a review is not a test.
    """
    import subprocess
    rows = _sweep_rows()
    assert len(rows) >= 6, f"only {len(rows)} sweep rows"
    shas = []
    for change, commits, why in rows:
        assert change and commits and why, change
        assert len(why) > 80, f"{change}: the reason is a label, not a reason"
        shas += re.findall(r"\b[0-9a-f]{7}\b", commits)
    assert len(shas) >= 8, "the sweep names almost no commits"
    if not (ROOT / ".git").exists():
        return  # a source tarball, not a checkout: nothing to resolve against
    for sha in shas:
        proc = subprocess.run(["git", "cat-file", "-e", sha + "^{commit}"],
                              cwd=ROOT, capture_output=True)
        assert proc.returncode == 0, f"{sha} is not a commit in this repository"


def test_the_two_ruled_sweep_rows_keep_their_wording():
    rows = {change: why for change, _commits, why in _sweep_rows()}
    solder = next(w for c, w in rows.items() if "solder_bump" in c)
    assert "MINOR by this policy's own words" in solder
    assert "1.1" in solder and "out of contract" in solder
    io_class = next(w for c, w in rows.items() if "io_class" in c)
    assert "fifteen" in io_class and "research/aspdac2027" in io_class


def test_the_dividing_line_justifies_the_minor_at_ELEMENT_level():
    """The reason a new enum member is a MINOR must not contradict the criterion.

    The text once read "a minor precisely because a consumer that does not know
    the value refuses the document instead of misreading it". Under the sentence
    two lines above it (a MINOR only adds what a consumer can IGNORE and remain
    correct) a consumer that refuses the document has not ignored anything, so
    that justification made every added enum member a MAJOR by the policy's own
    test. It also gave normative cover to exactly the whole-document refusal
    SPEC-25 exists to remove. Right conclusion, wrong reason, in normative text.

    What a green here does NOT cover: whether any consumer actually refuses at
    element level. This reads a document; the behaviour is SPEC-25's and the
    per-consumer rows own it.
    """
    policy = (ROOT / "docs" / "VERSION_POLICY.md").read_text(encoding="utf-8")
    line = policy.split("The dividing line", 1)[1].split("## Changing the major", 1)[0]
    assert "refuse the ELEMENT that carries it" in line
    assert "incomplete rather than wrong" in line
    assert "refuses the document instead of misreading it" not in policy, \
        "the retired justification is back"
    # The trap has to stay written down, not just avoided.
    assert "would eat the criterion" in line
