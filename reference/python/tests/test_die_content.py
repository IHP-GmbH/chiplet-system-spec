# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The content form of a die's build record and contract.

The fixtures are the records and contracts of the four writers, named by the
writer: build-die, padring, pad frame, partner die. Three judges: the digests
of the four pairs are pinned, so the form cannot move in place; planted pairs
that differ only in what the form leaves out give one digest, alone and all
at once; and a walk over every leaf of every fixture places each leaf in the
form and checks that changing it alone moves the digest or does not, as its
place says. A leaf the walk does not place is a red.
"""
import copy
import json
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from chiplet_format_io import ChipletFormatError
from chiplet_format_io import die_content as dc

FIXTURES = Path(__file__).parent / "fixtures" / "die_content"
WRITERS = ("build-die", "padring", "pad-frame", "partner-die")

#: The roots the projection tests rewrite under. The pdk root sits inside the
#: design root, so a path under it matches both and the longest must win.
FAKE_ROOTS = {"run": "/fake/run/RUN_1", "design": "/fake/design", "pdk": "/fake/design/pdk"}
MOVED_ROOTS = {"run": "/moved/runs/RUN_2", "design": "/moved/d", "pdk": "/moved/d/pdk"}

#: The digests of the four pairs, first pinned under die-content/v1. The
#: fixtures hold no digest map, so under die-content/v2 they key as written:
#: a record written before v2 reads to the same digest. They move only with a
#: new form name.
PINNED = {
    "build-die": ("157c4b17c0fac3f9569a32bef252bccaa2c0759b7870658d670adbfc9d617517",
                  "557207436414bd2bb7ea34e8f4c5678820badc76b8bb84846767a2f10c67a2b2"),
    "padring": ("629db7b981b75010985a16667c13ea09ec0c2eade45d2ebfd36161c8e573f9c5",
                "c7ac5724826ecb8a1ca0dce1c1eac95d44dd7ed60f7635ed402d29f82b664059"),
    "pad-frame": ("f06eb825b2511291d986b5ddc5b6354eb3cfd91477fe942e7354ebddd0ccdec2",
                  "4d21e7664a8437fb6c8d68fc15f47cebb1b59c018bac6aead937f6031411d1b5"),
    "partner-die": ("c03e47f0853957a2c432b3ca055d2b5a7af3c9c589e6c540cb530a624c6f77e3",
                    "9f98818577a0a599cc3eda35808206740e07bf90b45f178190733ef7703f51ac"),
}


def load(writer, kind):
    return json.loads((FIXTURES / f"{writer}.{kind}.json").read_text(encoding="utf-8"))


def pair(writer):
    return load(writer, "build-record"), load(writer, "contract")


def digests(record, contract):
    return dc.build_record_content_sha256(record), dc.contract_content_sha256(contract, record)


def strings(node, where=()):
    """Every string key and value of a document, with its path."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield where + (key,), key
            yield from strings(value, where + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from strings(value, where + (index,))
    elif isinstance(node, str):
        yield where, node


def leaves(node, where=()):
    """Every leaf of a document, with its path (list indices included)."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, where + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from leaves(value, where + (index,))
    else:
        yield where, node


def get(document, path):
    for segment in path:
        document = document[segment]
    return document


def put(document, path, value):
    parent = get(document, path[:-1])
    parent[path[-1]] = value


def changed(value):
    """The value changed alone: a string plus x, a number plus one, a bool
    negated, null to x."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, (int, float)):
        return value + 1
    if value is None:
        return "x"
    raise AssertionError(f"no change defined for {value!r}")


def dotted(path):
    return "".join(f"[{p}]" if isinstance(p, int) else (f".{p}" if i else p)
                   for i, p in enumerate(path))


def replace_everywhere(node, old, new):
    if isinstance(node, dict):
        return {k: replace_everywhere(v, old, new) for k, v in node.items()}
    if isinstance(node, list):
        return [replace_everywhere(v, old, new) for v in node]
    return new if node == old else node


# -- the fixtures themselves ---------------------------------------------------

HOST_PREFIXES = ("/home/", "/tmp/", "/nix/store/", "/scratch")


def test_no_fixture_holds_a_host_path():
    """A fixture in a public repository names no host: no home, scratch or
    store path, and no absolute path at all outside the fake roots of the
    projection tests."""
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) == 2 * len(WRITERS)
    for file in files:
        for where, text in strings(json.loads(file.read_text(encoding="utf-8"))):
            assert not text.startswith(HOST_PREFIXES), f"{file.name}: {dotted(where)} is {text!r}"
            assert not text.startswith("/") or text.startswith(tuple(FAKE_ROOTS.values())), \
                f"{file.name}: {dotted(where)} holds an absolute path {text!r}"


@pytest.mark.parametrize("writer", WRITERS)
def test_the_digests_of_the_four_pairs_are_pinned(writer):
    assert digests(*pair(writer)) == PINNED[writer]


def test_each_fixture_record_is_the_writer_its_name_says():
    assert dc.writer_of(load("build-die", "build-record")) == dc.BUILD_DIE
    assert dc.writer_of(load("padring", "build-record")) == dc.PADRING
    assert dc.writer_of(load("pad-frame", "build-record")) is None
    assert dc.writer_of(load("partner-die", "build-record")) is None


@pytest.mark.parametrize("schema", ["chiplet-full-build-record/v1", "something-else/v1", "", None, 3])
def test_a_schema_other_than_the_two_maps_to_no_writer(schema):
    assert dc.writer_of({"schema": schema}) is None
    assert dc.writer_of({}) is None
    assert dc.writer_of("not a record") is None


#: Paths the fixtures do not carry, one per pattern they leave unexercised.
EXTRA_PATHS = {dc.BUILD_DIE: ("64-chipletflow-bondpadlvs/cell-cdls/0/sg13g2_stdcell.cdl",)}


def _matches(writer, path):
    segments = dc.strip_step(path).split("/")
    return [pattern for w, pattern, _ in dc.BOUND_TABLE if w == writer
            and len(pattern.split("/")) == len(segments)
            and all(fnmatchcase(s, p) for s, p in zip(segments, pattern.split("/")))]


@pytest.mark.parametrize("writer", ["build-die", "padring"])
def test_no_fixture_path_matches_two_rows_and_every_pattern_matches_one(writer):
    record = load(writer, "build-record")
    name = dc.writer_of(record)
    paths = [row["path"] for row in record["bound"]] + list(EXTRA_PATHS.get(name, ()))
    hit = set()
    for path in paths:
        matched = _matches(name, path)
        assert len(matched) <= 1, f"{path} matches {matched}"
        hit.update(matched)
    assert hit == {pattern for w, pattern, _ in dc.BOUND_TABLE if w == name}


def test_every_bound_row_of_the_build_die_fixture_is_decided_by_a_row_of_the_table():
    record = load("build-die", "build-record")
    assert all(_matches(dc.BUILD_DIE, row["path"]) for row in record["bound"])


# -- the table and its helpers -------------------------------------------------

@pytest.mark.parametrize("path, stripped", [
    ("60-klayout-drc/state_in.json", "klayout-drc/state_in.json"),
    ("resolved.json", "resolved.json"),
    ("final/def/top.def", "final/def/top.def"),
    ("7-x", "x"),
    ("60-", "60-"),
    ("60-9x", "60-9x"),
    ("a/60-klayout-drc/x", "a/60-klayout-drc/x"),
    ("60-klayout-drc", "klayout-drc"),
])
def test_strip_step_removes_a_step_number_on_the_first_segment_only(path, stripped):
    assert dc.strip_step(path) == stripped


@pytest.mark.parametrize("writer, path, expected", [
    (dc.BUILD_DIE, "resolved.json", "project"),
    (dc.BUILD_DIE, "60-klayout-drc/state_in.json", "leave"),
    (dc.BUILD_DIE, "klayout-drc/state_in.json", "leave"),
    (dc.BUILD_DIE, "final/def/CF_DRIVER_LS.def", "keep"),
    (dc.BUILD_DIE, "64-chipletflow-bondpadlvs/cell-cdls/3/a.cdl", "keep"),
    (dc.BUILD_DIE, "64-chipletflow-bondpadlvs/CF_DRIVER_LS.spice", "leave"),
    (dc.BUILD_DIE, "64-chipletflow-bondpadlvs/sub/CF_DRIVER_LS.spice", "keep"),
    (dc.BUILD_DIE, "final/metrics.json", "leave"),
    (dc.BUILD_DIE, "something/new.json", "keep"),
    (dc.PADRING, "resolved.json", "project"),
    (dc.PADRING, "final/metrics.json", "leave"),
    (dc.PADRING, "final/pnl/chip_top.pnl.v", "keep"),
    (dc.PADRING, "final/nl/chip_top.nl.v", "keep"),
    (dc.PADRING, "60-klayout-drc/state_in.json", "keep"),
    (None, "resolved.json", "keep"),
    (None, "final/metrics.json", "keep"),
])
def test_the_decision_of_a_path(writer, path, expected):
    assert dc.decision(writer, path) == expected


def test_a_star_never_crosses_a_slash():
    assert dc.decision(dc.BUILD_DIE, "final/def/sub/top.def") == "keep"
    assert dc.decision(dc.BUILD_DIE, "chipletflow-bondpadlvs/cell-cdls/a/b/c.cdl") == "keep"


def test_the_table_names_only_the_writers_and_the_three_decisions():
    assert {w for w, _, _ in dc.BOUND_TABLE} == set(dc.WRITERS)
    assert {d for _, _, d in dc.BOUND_TABLE} == {"keep", "project", "leave"}


def test_every_projected_file_has_its_out_fields():
    """A project row without an entry in the out-field table would carry the
    tool's version or the run directory into the key."""
    assert {pattern for _, pattern, d in dc.BOUND_TABLE if d == "project"} == set(dc._PROJECT_OUT)


def test_content_json_cannot_carry_is_refused_with_the_format_error():
    for bad in ({"x": float("nan")}, {"x": {1, 2}}, {1: 1, "a": 2}):
        record, contract = pair("build-die")
        put(record, ("checks", "bad"), bad)
        with pytest.raises(dc.ChipletFormatError, match="build record: not JSON content"):
            dc.build_record_content_sha256(record)
        record, contract = pair("build-die")
        put(contract, ("die", "bad"), bad)
        with pytest.raises(dc.ChipletFormatError, match="contract: not JSON content"):
            dc.contract_content_sha256(contract, record)


def test_canonical_json_is_the_bytes_every_signature_hashes():
    assert dc.canonical_json({"b": 1, "a": [True, None, 1.5, "\u00e9"]}) == \
        b'{"a":[true,null,1.5,"\\u00e9"],"b":1}'
    with pytest.raises(ValueError):
        dc.canonical_json(float("nan"))


# -- projections ----------------------------------------------------------------

RESOLVED = {
    "meta": {"librelane_version": "3.0.0rc0", "flow": "Chiplet"},
    "DESIGN_DIR": "/fake/design",
    "PDK_ROOT": "/fake/design/pdk",
    "RUN_DIR": "/fake/run/RUN_1",
    "FILES": ["/fake/run/RUN_1/final/gds/top.gds", "/fake/design/pdk/libs.ref/a.lef",
              "/fake/design/src/top.v", "/elsewhere/abs/path", "note: /fake/run/RUN_1 inside"],
    "BY_PATH": {"/fake/run/RUN_1/x": 1, "/fake/design/pdk": 2, "plain": 3},
    "N": 7, "B": True, "Z": None,
}
RESOLVED_PROJECTED = {
    "meta": {"flow": "Chiplet"},
    "DESIGN_DIR": "design:.",
    "PDK_ROOT": "pdk:.",
    "RUN_DIR": "run:.",
    "FILES": ["run:final/gds/top.gds", "pdk:libs.ref/a.lef", "design:src/top.v",
              "/elsewhere/abs/path", "note: /fake/run/RUN_1 inside"],
    "BY_PATH": {"run:x": 1, "pdk:.": 2, "plain": 3},
    "N": 7, "B": True, "Z": None,
}
INPUTS_BEFORE = {"run_dir": "/fake/run/RUN_1", "shared_implementation": {"a.py": "abc"},
                 "config": {"path": "/fake/design/config.json", "sha256": "11"}}
OBSERVATIONS = {"status": "built", "final_artifacts": {"x": "22"}, "maintained_step_sources": ["s"],
                "macro_inclusion": {"gds": "/fake/design/m.gds"}}


def test_a_projection_takes_the_out_fields_and_rewrites_paths_under_the_longest_root():
    assert dc.project(dc.BUILD_DIE, "resolved.json", RESOLVED, FAKE_ROOTS) == RESOLVED_PROJECTED
    assert dc.project(dc.PADRING, "resolved.json", RESOLVED, FAKE_ROOTS) == RESOLVED_PROJECTED
    assert dc.project(dc.BUILD_DIE, "chiplet.inputs-before.json", INPUTS_BEFORE, FAKE_ROOTS) == {
        "config": {"path": "design:config.json", "sha256": "11"}}
    assert dc.project(dc.BUILD_DIE, "chiplet.build-observations.json", OBSERVATIONS, FAKE_ROOTS) == {
        "status": "built", "macro_inclusion": {"gds": "design:m.gds"}}


def test_a_projection_leaves_its_document_as_it_was():
    before = copy.deepcopy(RESOLVED)
    dc.project(dc.BUILD_DIE, "resolved.json", RESOLVED, FAKE_ROOTS)
    assert RESOLVED == before


def test_a_root_given_as_a_pathlike_or_with_a_trailing_slash_or_as_none_reads():
    roots = {"run": Path("/fake/run/RUN_1/"), "design": "/fake/design/", "pdk": None}
    out = dc.project(dc.BUILD_DIE, "resolved.json", RESOLVED, roots)
    assert out["RUN_DIR"] == "run:." and out["FILES"][1] == "design:pdk/libs.ref/a.lef"
    out = dc.project(dc.BUILD_DIE, "resolved.json", {"a": "/fake/run/RUN_1/", "b": "/fake/run/"}, roots)
    assert out == {"a": "run:.", "b": "/fake/run/"}
    out = dc.project(dc.BUILD_DIE, "resolved.json", RESOLVED, {"pdk": "/"})
    assert out["FILES"][3] == "pdk:elsewhere/abs/path" and out["BY_PATH"] == {"pdk:fake/run/RUN_1/x": 1,
                                                                             "pdk:fake/design/pdk": 2,
                                                                             "plain": 3}


@pytest.mark.parametrize("path, document, out", [
    ("resolved.json", RESOLVED, ("meta", "librelane_version")),
    ("chiplet.inputs-before.json", INPUTS_BEFORE, ("run_dir",)),
    ("chiplet.inputs-before.json", INPUTS_BEFORE, ("shared_implementation",)),
    ("chiplet.build-observations.json", OBSERVATIONS, ("final_artifacts",)),
    ("chiplet.build-observations.json", OBSERVATIONS, ("maintained_step_sources",)),
])
def test_an_out_field_changed_alone_leaves_the_projection_digest(path, document, out):
    other = copy.deepcopy(document)
    put(other, out, "moved")
    assert writer_content_sha256(path, other, FAKE_ROOTS) == writer_content_sha256(path, document, FAKE_ROOTS)
    absent = copy.deepcopy(document)
    parent = get(absent, out[:-1])
    del parent[out[-1]]
    assert writer_content_sha256(path, absent, FAKE_ROOTS) == writer_content_sha256(path, document, FAKE_ROOTS)


def test_the_roots_moved_leave_the_projection_digest():
    moved = RESOLVED
    for name in FAKE_ROOTS:
        moved = replace_everywhere(moved, FAKE_ROOTS[name], MOVED_ROOTS[name])
    moved["FILES"] = [f.replace("/fake/run/RUN_1", "/moved/runs/RUN_2")
                      .replace("/fake/design/pdk", "/moved/d/pdk").replace("/fake/design", "/moved/d")
                      if f.startswith("/") else f for f in RESOLVED["FILES"]]
    moved["BY_PATH"] = {"/moved/runs/RUN_2/x": 1, "/moved/d/pdk": 2, "plain": 3}
    assert writer_content_sha256("resolved.json", moved, MOVED_ROOTS) == \
        writer_content_sha256("resolved.json", RESOLVED, FAKE_ROOTS)


def test_a_content_field_changed_alone_moves_the_projection_digest():
    other = copy.deepcopy(RESOLVED)
    other["N"] = 8
    assert writer_content_sha256("resolved.json", other, FAKE_ROOTS) != \
        writer_content_sha256("resolved.json", RESOLVED, FAKE_ROOTS)


@pytest.mark.parametrize("writer, path, document, roots, words", [
    (dc.BUILD_DIE, "final/metrics.json", RESOLVED, FAKE_ROOTS, "not a file the content form projects"),
    (None, "resolved.json", RESOLVED, FAKE_ROOTS, "not a file the content form projects"),
    (dc.BUILD_DIE, "resolved.json", ["not", "an", "object"], FAKE_ROOTS, "not an object"),
    (dc.BUILD_DIE, "resolved.json", RESOLVED, {"run": "relative/run"}, "not an absolute path"),
    (dc.BUILD_DIE, "resolved.json", RESOLVED, {"home": "/fake/home"}, "not one of run, design, pdk"),
    (dc.BUILD_DIE, "resolved.json", RESOLVED, {"run": 3}, "not an absolute path"),
    (dc.BUILD_DIE, "resolved.json", RESOLVED, {"run": "/fake/r", "design": "/fake/r/"}, "same directory"),
    (dc.BUILD_DIE, "resolved.json", {"k": {"/fake/run/RUN_1/x": 1, "run:x": 2}}, FAKE_ROOTS, "collides"),
    (dc.BUILD_DIE, "resolved.json", {"meta": "a string"}, FAKE_ROOTS, "meta is not an object"),
])
def test_a_projection_refuses(writer, path, document, roots, words):
    with pytest.raises(ChipletFormatError, match=words):
        dc.project(writer, path, document, roots)


def writer_content_sha256(path, document, roots):
    """What a writer puts in a projected row's content_sha256: the digest of
    the projection, in the form's canonical JSON. A writer that hashed the
    file's bytes instead would key the die on what the form takes out."""
    return dc.canonical_sha256(dc.project(dc.BUILD_DIE, path, document, roots))


def test_two_records_whose_projected_file_differs_in_an_out_field_give_one_digest():
    """The planted red of the writer side: a writer that digests the raw file
    moves the key when LibreLane's version moves."""
    record = load("build-die", "build-record")
    other = copy.deepcopy(RESOLVED)
    other["meta"]["librelane_version"] = "3.1.0"
    a, b = copy.deepcopy(record), copy.deepcopy(record)
    for rec, doc in ((a, RESOLVED), (b, other)):
        row = next(r for r in rec["bound"] if r["path"] == "resolved.json")
        row["sha256"] = dc.canonical_sha256(doc)
        row["content_sha256"] = writer_content_sha256("resolved.json", doc, FAKE_ROOTS)
    assert a != b
    assert dc.build_record_content_sha256(a) == dc.build_record_content_sha256(b)


# -- planted pairs ----------------------------------------------------------------

def _leave_rows(record):
    writer = dc.writer_of(record)
    return [row for row in record["bound"] if dc.decision(writer, row["path"]) == "leave"]


def _project_rows(record):
    writer = dc.writer_of(record)
    return [row for row in record["bound"] if dc.decision(writer, row["path"]) == "project"]


def out_pairs():
    """One mutation per out path: changed where present, added where absent."""
    for out in dc.BUILD_RECORD_OUT:
        def mutate(record, out=out):
            node = record
            for segment in out[:-1]:
                node = node.setdefault(segment, {})
            node[out[-1]] = "moved" if out[-1] not in node else {"generator": "moved", "n": 1}
            return record
        yield pytest.param(mutate, id="out:" + ".".join(out))


def emitted_under_pairs():
    def mutate(record):
        record["emitted_under"] = {"generator": "chiplet-flow emit-ready-die", "reader_release": "9.9.9",
                                   "reader_capability": 99, "emitter_revision": "0" * 40,
                                   "klayout_version": "KLayout 9.9"}
        return record
    yield pytest.param(mutate, id="emitted_under varied")


def leave_pairs():
    for row in _leave_rows(load("build-die", "build-record")):
        def mutate(record, path=row["path"]):
            next(r for r in record["bound"] if r["path"] == path)["sha256"] = "f" * 64
            return record
        yield pytest.param(mutate, id=f"leave sha256:{row['path']}")


def project_raw_pairs():
    for row in _project_rows(load("build-die", "build-record")):
        def mutate(record, path=row["path"]):
            next(r for r in record["bound"] if r["path"] == path)["sha256"] = "e" * 64
            return record
        yield pytest.param(mutate, id=f"project raw sha256:{row['path']}")


def renumber(path, offset):
    head, sep, tail = path.partition("/")
    match = dc._STEP.match(head)
    if match and sep:
        return f"{int(head[:match.end() - 1]) + offset}-{head[match.end():]}/{tail}"
    return path


def step_pairs():
    def bound_steps(record):
        for row in record["bound"]:
            row["path"] = renumber(row["path"], 10)
        return record
    yield pytest.param(bound_steps, id="step numbers in bound paths")

    def reference_steps(record):
        for reference in record["checks"]["lvs"]["reference"]:
            if reference["root"] == "run":
                reference["path"] = renumber(reference["path"], 10)
        return record
    yield pytest.param(reference_steps, id="step numbers in run-rooted reference paths")

    def swap(record):
        bound = record["bound"]
        bound[0], bound[-1] = bound[-1], bound[0]
        bound[3], bound[4] = bound[4], bound[3]
        return record
    yield pytest.param(swap, id="rows in another order")

    def renumber_and_reorder(record):
        bound = record["bound"]
        first = bound.pop(0)
        first["path"] = renumber(first["path"], 50)
        bound.append(first)
        return record
    yield pytest.param(renumber_and_reorder, id="a renumbering that moves a row")


def layout_moved(record, contract):
    """The layout's raw digest changed consistently at every canon place of
    the record and the contract."""
    anchor = record["layout"]["sha256"]
    return replace_everywhere(record, anchor, "a" * 64), replace_everywhere(contract, anchor, "a" * 64)


BUILD_DIE_PAIRS = list(out_pairs()) + list(emitted_under_pairs()) + list(leave_pairs()) \
    + list(project_raw_pairs()) + list(step_pairs())


@pytest.mark.parametrize("mutate", BUILD_DIE_PAIRS)
def test_a_build_die_pair_that_differs_only_outside_the_form_gives_one_digest(mutate):
    record, contract = pair("build-die")
    other = mutate(copy.deepcopy(record))
    assert other != record, "the pair must differ"
    assert digests(other, contract) == PINNED["build-die"]


def test_the_layout_digest_moved_at_every_canon_place_gives_one_digest():
    record, contract = pair("build-die")
    other_record, other_contract = layout_moved(record, contract)
    assert other_record != record and other_contract != contract
    assert digests(other_record, other_contract) == PINNED["build-die"]
    frame, frame_contract = layout_moved(*pair("pad-frame"))
    assert digests(frame, frame_contract) == PINNED["pad-frame"]


def test_every_build_die_pair_at_once_gives_the_pinned_digest():
    record, contract = pair("build-die")
    other = copy.deepcopy(record)
    for param in BUILD_DIE_PAIRS:
        other = param.values[0](other)
    assert digests(*layout_moved(other, contract)) == PINNED["build-die"]


def padring_pairs():
    def top_level(record):
        record["run"] = "RUN_2999-01-01_00-00-00"
        record["librelane_version"] = "9.9.9"
        record["pdk_version_from"] = "elsewhere"
        return record
    yield pytest.param(top_level, id="run, librelane_version, pdk_version_from")

    def streamed_in(record):
        record["layout"]["streamed_in"] = [renumber(p + "/", 10).rstrip("/")
                                           for p in record["layout"]["streamed_in"]]
        return record
    yield pytest.param(streamed_in, id="step numbers in streamed_in")

    def measured_in(record):
        for metric in record["producer_reported"].values():
            metric["measured_in"] = [renumber(p + "/", 10).rstrip("/") for p in metric["measured_in"]]
        return record
    yield pytest.param(measured_in, id="step numbers in measured_in")

    def metrics_sha(record):
        next(r for r in record["bound"] if r["path"] == "final/metrics.json")["sha256"] = "d" * 64
        return record
    yield pytest.param(metrics_sha, id="leave sha256:final/metrics.json")

    def resolved_raw(record):
        next(r for r in record["bound"] if r["path"] == "resolved.json")["sha256"] = "c" * 64
        return record
    yield pytest.param(resolved_raw, id="project raw sha256:resolved.json")

    def emitted(record):
        record["emitted_under"] = {"generator": "chiplet-die-flow derive-padring-die",
                                   "reader_release": "9.9.9", "reader_capability": 99}
        return record
    yield pytest.param(emitted, id="emitted_under varied")


PADRING_PAIRS = list(padring_pairs())


@pytest.mark.parametrize("mutate", PADRING_PAIRS)
def test_a_padring_pair_that_differs_only_outside_the_form_gives_one_digest(mutate):
    record, contract = pair("padring")
    other = mutate(copy.deepcopy(record))
    assert other != record
    assert digests(other, contract) == PINNED["padring"]


def test_every_padring_pair_at_once_gives_the_pinned_digest():
    record, contract = pair("padring")
    other = copy.deepcopy(record)
    for param in PADRING_PAIRS:
        other = param.values[0](other)
    assert digests(other, contract) == PINNED["padring"]


@pytest.mark.parametrize("writer", WRITERS)
def test_the_contract_generator_and_its_record_digest_are_not_in_the_form(writer):
    record, contract = pair(writer)
    other = copy.deepcopy(contract)
    other["generator"] = "another tool"
    other["build_record"]["sha256"] = "b" * 64
    assert dc.contract_content_sha256(other, record) == PINNED[writer][1]


def test_a_contract_over_two_records_that_differ_only_in_out_fields_gives_one_digest():
    record, contract = pair("build-die")
    other_record = copy.deepcopy(record)
    other_record["emitted_under"]["emitter_revision"] = "1" * 40
    other_contract = copy.deepcopy(contract)
    other_contract["build_record"]["sha256"] = dc.canonical_sha256(other_record)
    assert dc.contract_content_sha256(other_contract, other_record) == PINNED["build-die"][1]


def test_an_old_record_lacking_every_out_field_reads_to_the_same_digest():
    record, contract = pair("build-die")
    old = copy.deepcopy(record)
    for out in dc.BUILD_RECORD_OUT:
        try:
            del get(old, out[:-1])[out[-1]]
        except KeyError:
            pass
    assert "emitted_under" not in old and "pdk_version_from" not in old["process"]
    assert digests(old, contract) == PINNED["build-die"]


# -- the content form read back ----------------------------------------------------

def test_the_build_die_content_form_reads_as_the_table_says():
    record = load("build-die", "build-record")
    content = dc.build_record_content(record)
    for out in dc.BUILD_RECORD_OUT:
        try:
            get(content, out)
        except KeyError:
            continue
        raise AssertionError(f"{'.'.join(out)} is in the content form")
    rows = {row["path"]: row for row in content["bound"]}
    assert [row["path"] for row in content["bound"]] == sorted(rows)
    assert not any(dc._STEP.match(path) for path in rows)
    assert set(rows) == {"resolved.json", "chiplet.inputs-before.json", "chiplet.build-observations.json",
                         "final/def/CF_DRIVER_LS.def", "chipletflow-bondpadfiller/rewritten/CF_DRIVER_LS.gds",
                         "openroad-writecdl/CF_DRIVER_LS.cdl"}
    assert set(rows["resolved.json"]) == {"path", "content_sha256"}
    assert set(rows["final/def/CF_DRIVER_LS.def"]) == {"path", "sha256"}
    assert rows["chipletflow-bondpadfiller/rewritten/CF_DRIVER_LS.gds"]["sha256"] == "layout"
    assert content["layout"]["sha256"] == "layout"
    assert content["checks"]["layout_sha256"] == "layout"
    assert all(content["checks"][part]["input_sha256"] == "layout" for part in ("drc", "density", "antenna"))
    assert content["checks"]["lvs"]["fabricated_sha256"] == "layout"
    design = next(r for r in content["checks"]["lvs"]["reference"] if r["root"] == "run")
    assert design["path"] == "openroad-writecdl/CF_DRIVER_LS.cdl"
    assert content["schema"] == record["schema"] and content["limitations"] == record["limitations"]


def test_the_padring_content_form_reads_as_the_table_says():
    content = dc.build_record_content(load("padring", "build-record"))
    rows = {row["path"]: row for row in content["bound"]}
    assert set(rows) == {"resolved.json", "final/def/chip_top.def", "final/pnl/chip_top.pnl.v",
                         "ip/bondpad_70x70/lef/bondpad_70x70_5L.lef", "final/nl/chip_top.nl.v"}
    assert set(rows["resolved.json"]) == {"path", "content_sha256"}
    assert content["layout"]["streamed_in"] == ["klayout-streamout"]
    assert all(not dc._STEP.match(step) for metric in content["producer_reported"].values()
               for step in metric["measured_in"])
    assert "run" not in content and "librelane_version" not in content and "pdk_version_from" not in content


def test_a_project_row_without_a_projection_is_kept_with_its_digest():
    record = load("build-die", "build-record")
    row = next(r for r in record["bound"] if r["path"] == "resolved.json")
    del row["content_sha256"]
    rows = {r["path"]: r for r in dc.build_record_content(record)["bound"]}
    assert set(rows["resolved.json"]) == {"path", "sha256"}
    row["sha256"] = "9" * 64
    assert dc.build_record_content_sha256(record) != PINNED["build-die"][0]


def test_a_record_of_no_named_writer_keeps_every_row():
    record = load("build-die", "build-record")
    record["schema"] = "chiplet-full-build-record/v1"
    content = dc.build_record_content(record)
    assert len(content["bound"]) == len(record["bound"])
    assert all("sha256" in row for row in content["bound"])
    metrics = next(r for r in record["bound"] if r["path"] == "final/metrics.json")
    before = dc.build_record_content_sha256(record)
    metrics["sha256"] = "8" * 64
    assert dc.build_record_content_sha256(record) != before


def test_two_steps_of_one_slug_keep_their_order():
    record = load("padring", "build-record")
    record["bound"] += [{"path": "60-klayout-drc/x.json", "sha256": "1" * 64},
                        {"path": "70-klayout-drc/x.json", "sha256": "2" * 64}]
    rows = [row for row in dc.build_record_content(record)["bound"] if row["path"] == "klayout-drc/x.json"]
    assert [row["sha256"] for row in rows] == ["1" * 64, "2" * 64]


def test_the_partner_die_and_pad_frame_forms_keep_everything_but_the_out_fields():
    for writer in ("pad-frame", "partner-die"):
        record = load(writer, "build-record")
        content = dc.build_record_content(record)
        assert "emitted_under" not in content and "pdk_version_source" not in content["process"]
        assert "bound" not in content
    frame = dc.build_record_content(load("pad-frame", "build-record"))
    assert frame["layout"]["sha256"] == "layout"
    partner, contract = pair("partner-die")
    assert "layout" not in dc.build_record_content(partner)
    assert dc.contract_content(contract, partner)["die"]["layout_sha256"] == "layout"


def test_a_contract_without_a_record_is_its_own_content():
    contract = load("partner-die", "contract")
    contract["build_record"] = None
    content = dc.contract_content(contract)
    assert content["build_record"] is None and "generator" not in content
    assert content["die"]["layout_sha256"] == "layout"
    del contract["build_record"]
    assert dc.contract_content(contract) == {k: v for k, v in content.items() if k != "build_record"}


def test_a_contract_whose_layout_is_not_the_records_keeps_its_digest_raw():
    record, contract = pair("build-die")
    contract["die"]["layout_sha256"] = "5" * 64
    assert dc.contract_content(contract, record)["die"]["layout_sha256"] == "5" * 64


@pytest.mark.parametrize("out", [out for out in dc.BUILD_RECORD_OUT if len(out) > 1])
def test_an_out_path_under_a_parent_of_the_wrong_type_is_refused(out):
    record = load("build-die", "build-record")
    put(record, out[:-1], "a string, not an object")
    with pytest.raises(ChipletFormatError, match=rf"{out[0]} is not an object, so {out[0]}\."):
        dc.build_record_content(record)


@pytest.mark.parametrize("record, words", [
    (["a", "list"], "not an object"),
    ("text", "not an object"),
    (None, "not an object"),
    ({"schema": dc.PADRING_RECORD_SCHEMA, "bound": {"path": "x"}}, "bound is not a list"),
    ({"schema": dc.PADRING_RECORD_SCHEMA, "bound": [{"sha256": "0"}]}, r"bound\[0\] is not an object with a path"),
    ({"schema": dc.PADRING_RECORD_SCHEMA, "bound": [{"path": 3}]}, r"bound\[0\]"),
])
def test_a_record_that_is_not_a_record_is_refused(record, words):
    with pytest.raises(ChipletFormatError, match=words):
        dc.build_record_content(record)


def test_a_contract_and_its_record_must_agree_on_whether_there_is_one():
    record, contract = pair("build-die")
    with pytest.raises(ChipletFormatError, match="names a build record and none was given"):
        dc.contract_content(contract)
    contract["build_record"] = None
    with pytest.raises(ChipletFormatError, match="names no build record and one was given"):
        dc.contract_content(contract, record)
    contract["build_record"] = "a string"
    with pytest.raises(ChipletFormatError, match="neither an object nor null"):
        dc.contract_content(contract, record)
    with pytest.raises(ChipletFormatError, match="not an object"):
        dc.contract_content(["x"], record)
    contract["build_record"] = {"path": "build-record.json", "sha256": "0"}
    contract["generator"] = {"name": "x"}
    assert "generator" not in dc.contract_content(contract, record)


# -- the walk ---------------------------------------------------------------------

#: Where a leaf of a record sits in the form: out and a leave row's digest
#: leave the digest where it is when changed alone; every other place moves
#: it. A project row's raw digest is out once the row has a projection. The
#: anchor, the record's own layout digest (the contract's own where the record
#: has none), is the word ``layout`` whatever its value: the gate's subject
#: keys the layout by its canon digest, and every other canon place moves
#: when it stops equalling the anchor.
NO_CHANGE = ("out", "leave sha256", "project raw sha256", "replaced", "anchor")

RECORD_KEEP_HEADS = ("schema", "origin", "status", "qualified", "design", "flow", "source", "process",
                     "layout", "limitations", "view", "checks", "pins", "producer_reported", "pad_master",
                     "spec", "die", "package", "signoff", "supply_volts")
CONTRACT_KEEP_HEADS = ("schema", "version", "die", "inventory", "terminals", "process", "supply_domains")


def place_record_leaf(record, path):
    for out in dc.BUILD_RECORD_OUT:
        if path[:len(out)] == out:
            return "out"
    if path[0] == "bound":
        row = record["bound"][path[1]]
        verdict = dc.decision(dc.writer_of(record), row["path"])
        field = path[2]
        if verdict == "leave":
            return "leave sha256" if field == "sha256" else "leave path"
        if verdict == "project" and field == "sha256" and isinstance(row.get("content_sha256"), str):
            return "project raw sha256"
        return f"{verdict} {field}"
    if path == ("layout", "sha256"):
        return "anchor"
    if path in (("checks", "layout_sha256"), ("checks", "lvs", "fabricated_sha256")) \
            or (len(path) == 3 and path[0] == "checks" and path[2] == "input_sha256"):
        return "canon"
    if path[0] in RECORD_KEEP_HEADS:
        return "keep"
    raise AssertionError(f"{dotted(path)} is not placed in the form")


def place_contract_leaf(record, path):
    if path[0] == "generator":
        return "out"
    if path == dc.CONTRACT_REPLACED:
        return "replaced"
    if path == ("build_record", "path"):
        return "keep"
    if path == ("die", "layout_sha256"):
        layout = record.get("layout")
        return "canon" if isinstance(layout, dict) and isinstance(layout.get("sha256"), str) else "anchor"
    if path[0] in CONTRACT_KEEP_HEADS:
        return "keep"
    raise AssertionError(f"{dotted(path)} is not placed in the form")


def record_leaves():
    for writer in WRITERS:
        record = load(writer, "build-record")
        for path, value in leaves(record):
            yield pytest.param(writer, path, id=f"{writer}:{dotted(path)}")


def contract_leaves():
    for writer in WRITERS:
        for path, value in leaves(load(writer, "contract")):
            yield pytest.param(writer, path, id=f"{writer}:{dotted(path)}")


@pytest.mark.parametrize("writer, path", list(record_leaves()))
def test_a_record_leaf_changed_alone_moves_the_digest_as_its_place_says(writer, path):
    record = load(writer, "build-record")
    place = place_record_leaf(record, path)
    expected = place not in NO_CHANGE
    if place == "anchor":
        # The anchor's own value is never in the form; it moves the digest only
        # through the other places that equalled it and now stay raw.
        anchor = get(record, path)
        expected = any(value == anchor for where, value in leaves(record) if where != path)
    put(record, path, changed(get(record, path)))
    moved = dc.build_record_content_sha256(record) != PINNED[writer][0]
    assert moved == expected, f"{dotted(path)} placed as {place}"


@pytest.mark.parametrize("writer, path", list(contract_leaves()))
def test_a_contract_leaf_changed_alone_moves_the_digest_as_its_place_says(writer, path):
    record, contract = pair(writer)
    place = place_contract_leaf(record, path)
    put(contract, path, changed(get(contract, path)))
    moved = dc.contract_content_sha256(contract, record) != PINNED[writer][1]
    assert moved == (place not in NO_CHANGE), f"{dotted(path)} placed as {place}"


def test_the_walk_reaches_every_place_of_the_form():
    places = set()
    for writer in WRITERS:
        record = load(writer, "build-record")
        places.update(place_record_leaf(record, path) for path, _ in leaves(record))
        places.update(place_contract_leaf(record, path) for path, _ in leaves(load(writer, "contract")))
    assert places >= {"out", "leave sha256", "leave path", "project raw sha256", "project path",
                      "project content_sha256", "keep path", "keep sha256", "canon", "anchor", "keep",
                      "replaced"}


def test_a_leave_row_moved_to_an_unmatched_path_becomes_keep():
    record = load("build-die", "build-record")
    row = _leave_rows(record)[0]
    row["path"] = "somewhere/else.json"
    content = dc.build_record_content(record)
    assert any(r["path"] == "somewhere/else.json" and r["sha256"] == row["sha256"] for r in content["bound"])
