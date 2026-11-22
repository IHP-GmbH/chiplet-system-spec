# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The digest map of die-content/v2 and the canons it reads stamped files by.

Every file here is written by the test: small GDS streams, SPEF and SDF texts
and JSON observations, built twice with only their stamps changed. The judges:
the canon of two stampings is one, and a change past the stamps moves it; a
projected file whose candidates were written again gives one projection with
its map; a file named only inside a JSON file it names is mapped too; and each
refusal names the file, the file that names it and where.
"""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from chiplet_format_io import ChipletFormatError
from chiplet_format_io import die_content as dc

INPUTS = "chiplet.inputs-before.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return Path(path).read_bytes()


# --- synthetic stamped files -------------------------------------------------

def record(rtype, dtype, payload=b""):
    return (4 + len(payload)).to_bytes(2, "big") + bytes((rtype, dtype)) + payload


def gds(stamp=1, name=b"TOP\x00", pad=b""):
    """A GDS stream with one structure; ``stamp`` sets every time field."""
    times = stamp.to_bytes(2, "big") * 12
    return b"".join((
        record(0x00, 0x02, b"\x02\x58"),           # HEADER
        record(0x01, 0x02, times),                 # BGNLIB
        record(0x02, 0x06, b"LIB\x00"),            # LIBNAME
        record(0x03, 0x05, bytes(16)),             # UNITS
        record(0x05, 0x02, times),                 # BGNSTR
        record(0x06, 0x06, name),                  # STRNAME
        record(0x07, 0x00),                        # ENDSTR
        record(0x04, 0x00),                        # ENDLIB
    )) + pad


def spef(date="Mon Jan  1 00:00:00 2026", program="tool", version="1.0", design="top", cap="1.0"):
    return (f'*SPEF "IEEE 1481-1998"\n*DESIGN "{design}"\n*DATE "{date}"\n*VENDOR "v"\n'
            f'*PROGRAM "{program}"\n*VERSION "{version}"\n*DESIGN_FLOW "NETLIST_TYPE_VERILOG"\n'
            f'*T_UNIT 1 NS\n\n*NAME_MAP\n*1 a\n\n*D_NET *1 {cap}\n*CONN\n*END\n').encode("ascii")


def sdf(date="Mon Jan  1 00:00:00 2026", program="tool", version="1.0", delay="0.1"):
    return (f'(DELAYFILE\n (SDFVERSION "3.0")\n (DESIGN "top")\n (DATE "{date}")\n (VENDOR "v")\n'
            f' (PROGRAM "{program}")\n (VERSION "{version}")\n (DIVIDER /)\n (TIMESCALE 1ns)\n'
            f' (CELL (CELLTYPE "top") (INSTANCE)\n  (DELAY (ABSOLUTE (INTERCONNECT a b ({delay}))))\n'
            f' )\n)\n').encode("ascii")


# --- file kinds and canons ---------------------------------------------------

def test_the_kind_of_a_file_is_read_from_its_first_bytes():
    assert dc.file_kind(gds()) == dc.GDS
    assert dc.file_kind(spef()) == dc.SPEF
    assert dc.file_kind(sdf()) == dc.SDF
    assert dc.file_kind(b'{"a": 1}') is None
    assert dc.file_kind(b"VERSION 5.8 ;\n") is None
    assert dc.file_kind(b"") is None


def test_a_kind_that_is_not_stamped_has_no_canon():
    with pytest.raises(ValueError):
        dc.canon("LEF", b"")


def test_a_gds_canon_zeroes_the_times_of_its_bgnlib_and_bgnstr_records_and_keeps_the_rest():
    data = gds(stamp=7)
    zeroed = gds(stamp=0)
    assert data != zeroed
    assert dc.canon(dc.GDS, data) == zeroed
    assert dc.canon_sha256(dc.GDS, data) == dc.canon_sha256(dc.GDS, gds(stamp=9))
    assert dc.canon_sha256(dc.GDS, data) != dc.canon_sha256(dc.GDS, gds(stamp=7, name=b"TOQ\x00"))


def test_a_gds_canon_keeps_a_padding_of_four_bytes_or_more_and_drops_a_shorter_one():
    assert dc.canon(dc.GDS, gds(pad=bytes(6))) == gds(stamp=0, pad=bytes(6))
    assert dc.canon(dc.GDS, gds(pad=bytes(3))) == gds(stamp=0)


@pytest.mark.parametrize("data, reason", [
    (gds()[:-2], "the stream ends inside a record header"),
    (gds()[:-10], "the stream ends inside a record"),
    (gds()[:-4], "the stream ends before its ENDLIB record"),
    (gds(pad=b"\x00\x00\x00\x01"), "bytes other than padding follow the ENDLIB record"),
    (gds()[:6] + b"\x00\x02\x01\x02" + gds()[6:], "a record at byte 6 is shorter than its own header"),
])
def test_a_gds_cut_short_or_malformed_is_refused(data, reason):
    with pytest.raises(dc.CanonError) as caught:
        dc.canon(dc.GDS, data)
    assert caught.value.kind == dc.GDS
    assert caught.value.reason == reason


def test_a_spef_canon_drops_its_four_stamp_lines_and_nothing_else():
    one = spef()
    two = spef(date="Tue Feb  2 11:11:11 2027", program="other", version="2.0")
    assert one != two
    assert dc.canon(dc.SPEF, one) == dc.canon(dc.SPEF, two)
    lines = set(one.splitlines()) - set(dc.canon(dc.SPEF, one).splitlines())
    assert sorted(line.split()[0].decode() for line in lines) == sorted(dc.SPEF_DROPPED)
    assert dc.canon(dc.SPEF, one) != dc.canon(dc.SPEF, spef(design="other"))
    assert dc.canon(dc.SPEF, one) != dc.canon(dc.SPEF, spef(cap="2.0"))


def test_a_spef_header_ends_at_its_first_section():
    data = spef() + b'*DATE "not a header line"\n'
    assert dc.canon(dc.SPEF, data).endswith(b'*DATE "not a header line"\n')


def test_a_spef_without_stamp_lines_is_its_own_canon():
    data = b'*SPEF "IEEE 1481-1998"\n*DESIGN "top"\n*D_NET *1 1.0\n*END\n'
    assert dc.canon(dc.SPEF, data) == data


def test_a_spef_that_ends_inside_its_header_is_refused():
    with pytest.raises(dc.CanonError) as caught:
        dc.canon(dc.SPEF, b'*SPEF "IEEE 1481-1998"\n*DATE "x"\n')
    assert caught.value.reason == "the file ends inside its header, before any section"


def test_an_sdf_canon_drops_its_four_stamp_entries_and_the_lines_they_leave_blank():
    one = sdf()
    two = sdf(date="Tue Feb  2 11:11:11 2027", program="other", version="2.0")
    assert one != two
    assert dc.canon(dc.SDF, one) == dc.canon(dc.SDF, two)
    assert dc.canon(dc.SDF, one) == (
        b'(DELAYFILE\n (SDFVERSION "3.0")\n (DESIGN "top")\n (DIVIDER /)\n (TIMESCALE 1ns)\n'
        b' (CELL (CELLTYPE "top") (INSTANCE)\n  (DELAY (ABSOLUTE (INTERCONNECT a b (0.1))))\n )\n)\n')
    assert dc.canon(dc.SDF, one) != dc.canon(dc.SDF, sdf(delay="0.2"))


def test_an_sdf_entry_on_a_shared_line_goes_alone():
    data = b'(DELAYFILE (SDFVERSION "3.0") (DATE "x") (DESIGN "top")\n (CELL (CELLTYPE "top")))\n'
    assert dc.canon(dc.SDF, data) == b'(DELAYFILE (SDFVERSION "3.0")  (DESIGN "top")\n (CELL (CELLTYPE "top")))\n'


def test_an_sdf_reads_parentheses_inside_quotes_as_text_and_keeps_entries_after_its_first_cell():
    data = (b'(DELAYFILE\n (DESIGN "a)(b")\n (DATE "x \\" (y")\n (CELL (CELLTYPE "top"))\n'
            b' (DATE "kept")\n)\n')
    assert dc.canon(dc.SDF, data) == b'(DELAYFILE\n (DESIGN "a)(b")\n (CELL (CELLTYPE "top"))\n (DATE "kept")\n)\n'


def test_an_sdf_reads_a_backslash_in_a_name_as_escaping_the_next_character():
    data = b'(DELAYFILE (DATE "x")\n (CELL (CELLTYPE "t") (INSTANCE a\\)b)))\n'
    assert dc.canon(dc.SDF, data) == b'(DELAYFILE \n (CELL (CELLTYPE "t") (INSTANCE a\\)b)))\n'


def test_an_sdf_without_stamp_entries_is_its_own_canon():
    data = b'(DELAYFILE (DESIGN "top") (CELL (CELLTYPE "top")))\n'
    assert dc.canon(dc.SDF, data) == data


@pytest.mark.parametrize("data, reason", [
    (b'(DELAYFILE (DATE "x)\n', "the file ends inside a quoted string"),
    (b'(DELAYFILE (DATE "x")))\n', "a closing parenthesis at byte 22 has no opening one"),
    (b'(DELAYFILE (DATE "x")\n', "the file ends inside an open parenthesis"),
])
def test_an_sdf_cut_short_or_malformed_is_refused(data, reason):
    with pytest.raises(dc.CanonError) as caught:
        dc.canon(dc.SDF, data)
    assert caught.value.kind == dc.SDF
    assert caught.value.reason == reason


# --- candidates --------------------------------------------------------------

def test_the_candidates_of_a_document_are_the_objects_holding_a_digest_and_a_path():
    digest = "a" * 64
    document = {
        "a": {"sha256": digest, "resolved_path": "/x/r", "path": "/x/p", "source": "/x/s"},
        "b": [{"sha256": digest, "path": "rel/p", "source": "/x/s/../t"}],
        "c": {"sha256": digest, "destination": "inputs/d"},
        "d": {"sha256": digest, "destination": "../outside"},
        "e": {"sha256": digest, "path": "rel/only"},
        "f": {"sha256": 3, "path": "/x/n"},
        "g": {"path": "/x/no-digest"},
    }
    assert dc.candidates(document) == [("/a", "/x/r", digest), ("/b/0", "/x/t", digest)]


def test_an_object_naming_a_staged_copy_by_destination_with_its_sources_digest_is_no_candidate(builds):
    # A staged view records where it was copied to and the digest of the file
    # it was made from; the copy itself may be a transformed one.
    one, _ = builds
    staged = one.design / "inputs" / "top.lef"
    staged.parent.mkdir()
    staged.write_bytes(b"VERSION 5.8 ;\nPROPERTY origin ;\nEND LIBRARY\n")
    one.document["core"]["views"].append({"destination": "inputs/top.lef", "sha256": one.raw("lef")})
    assert one.map() == Build(one.design.parent, stamp=1).map()


# --- the digest map ----------------------------------------------------------

class Build:
    """One build's files under ``base``: a GDS, a SPEF, a LEF, an SDF named
    only inside a JSON observation, and the projected inputs file naming the
    first four. ``stamp`` restamps every stamped file; ``delay`` changes the
    SDF past its stamps. A build made again at the same ``base`` replaces the
    last one: the pairs compared are written at one path, as two runs of one
    project are, since a JSON file read through the map keeps its paths."""

    def __init__(self, base, stamp, delay="0.1"):
        shutil.rmtree(base, ignore_errors=True)
        self.design = base / "design"
        self.run = self.design / "runs" / "RUN_1"
        core = base / "core"
        for folder in (self.run, core):
            folder.mkdir(parents=True)
        stamps = {"date": f"day {stamp}", "program": f"tool {stamp}", "version": str(stamp)}
        self.files = {
            "gds": (core / "top.gds", gds(stamp=stamp)),
            "spef": (core / "top.spef", spef(**stamps)),
            "lef": (core / "top.lef", b"VERSION 5.8 ;\nEND LIBRARY\n"),
            "sdf": (core / "top.sdf", sdf(delay=delay, **stamps)),
        }
        for path, data in self.files.values():
            path.write_bytes(data)
        self.observation = core / "core.observations.json"
        self.observation.write_bytes(json.dumps({
            "views": [self.entry("gds", "path"), self.entry("sdf", "path")],
            "written_at": "by the core run",
        }).encode("ascii"))
        self.document = {
            "core": {
                "observation": {"path": str(self.observation), "sha256": self.digest_of(self.observation)},
                "views": [self.entry("gds", "source"), self.entry("spef", "resolved_path"),
                          self.entry("lef", "path")],
            },
            "run_dir": str(self.run),
            "shared_implementation": {"sha256": "f" * 64, "path": str(base / "absent.json")},
        }

    def digest_of(self, path):
        return sha(path.read_bytes())

    def raw(self, name):
        return sha(self.files[name][1])

    def entry(self, name, key):
        path, data = self.files[name]
        return {key: str(path), "sha256": sha(data)}

    @property
    def roots(self):
        return {"run": str(self.run), "design": str(self.design), "pdk": None}

    def map(self, read=read):
        return dc.digest_map(dc.BUILD_DIE, INPUTS, self.document, self.roots, read)

    def projection(self):
        return dc.project(dc.BUILD_DIE, INPUTS, self.document, self.roots, self.map())


@pytest.fixture
def builds(tmp_path):
    return Build(tmp_path / "a", stamp=1), Build(tmp_path / "b", stamp=2)


def projections(base, first, second):
    """The projections of two builds made one after the other at ``base``,
    each with its map: ``first`` and ``second`` are Build keywords, and an
    optional ``edit`` changes the build's files before it is read."""
    out = []
    for keywords in (first, second):
        keywords = dict(keywords)
        edit = keywords.pop("edit", None)
        build = Build(base, **keywords)
        if edit is not None:
            edit(build)
        out.append(build.projection())
    return out


def test_a_map_holds_a_canon_for_each_stamped_file_and_a_content_value_for_json_evidence(builds):
    one, _ = builds
    canons = {name: dc.CANON + dc.canon_sha256(kind, one.files[name][1])
              for name, kind in (("gds", dc.GDS), ("spef", dc.SPEF), ("sdf", dc.SDF))}
    observed = one.observation.read_text()
    for name in ("gds", "sdf"):
        observed = observed.replace(one.raw(name), canons[name])
    assert one.map() == {
        one.raw("gds"): canons["gds"],
        one.raw("spef"): canons["spef"],
        one.digest_of(one.observation): dc.CONTENT + dc.canonical_sha256(json.loads(observed)),
    }


def test_a_file_named_only_inside_a_json_candidate_is_mapped_in_that_files_content_value(builds):
    one, _ = builds
    observation = json.loads(one.observation.read_bytes())
    canons = {one.raw("gds"): dc.CANON + dc.canon_sha256(dc.GDS, one.files["gds"][1]),
              one.raw("sdf"): dc.CANON + dc.canon_sha256(dc.SDF, one.files["sdf"][1])}
    expected = dc.CONTENT + dc.canonical_sha256(dc._replace_digests(observation, canons))
    assert one.map()[one.digest_of(one.observation)] == expected


def test_two_builds_that_differ_only_in_their_stamps_give_one_projection(tmp_path):
    one, two = Build(tmp_path / "a", stamp=1), Build(tmp_path / "b", stamp=2)
    for name in ("gds", "spef", "sdf"):
        assert one.raw(name) != two.raw(name)
    first, second = projections(tmp_path / "p", {"stamp": 1}, {"stamp": 2})
    assert first == second
    unmapped = Build(tmp_path / "p", stamp=1)
    assert first != dc.project(dc.BUILD_DIE, INPUTS, unmapped.document, unmapped.roots)


def fixed(text):
    def edit(build):
        observation = json.loads(build.observation.read_bytes())
        observation["written_at"] = text
        build.observation.write_bytes(json.dumps(observation).encode("ascii"))
        build.document["core"]["observation"]["sha256"] = build.digest_of(build.observation)
    return edit


def test_a_json_file_that_differs_past_its_mapped_digests_keeps_the_builds_apart(tmp_path):
    base = tmp_path / "p"
    first, second = projections(base, {"stamp": 1, "edit": fixed("same")}, {"stamp": 2, "edit": fixed("same")})
    assert first == second
    first, second = projections(base, {"stamp": 1, "edit": fixed("same")}, {"stamp": 2, "edit": fixed("other")})
    assert first != second


def test_a_stamped_file_changed_past_its_stamps_inside_a_json_candidate_keeps_the_builds_apart(tmp_path):
    first, second = projections(tmp_path / "p", {"stamp": 1, "edit": fixed("same")},
                                {"stamp": 2, "delay": "0.2", "edit": fixed("same")})
    assert first != second


def test_descent_goes_through_every_json_candidate_at_any_depth(tmp_path):
    base = tmp_path / "p"
    def deeper(build):
        timing = build.observation.with_name("timing.json")
        timing.write_bytes(json.dumps({"corner": build.entry("sdf", "path")}).encode("ascii"))
        observation = {"timing": {"path": str(timing), "sha256": build.digest_of(timing)}}
        build.observation.write_bytes(json.dumps(observation).encode("ascii"))
        build.document["core"]["observation"]["sha256"] = build.digest_of(build.observation)
    first, second = projections(base, {"stamp": 1, "edit": deeper}, {"stamp": 2, "edit": deeper})
    assert first == second
    first, second = projections(base, {"stamp": 1, "edit": deeper}, {"stamp": 2, "delay": "0.2", "edit": deeper})
    assert first != second


def test_a_file_that_is_neither_stamped_nor_json_evidence_stays_raw(builds):
    one, _ = builds
    assert one.raw("lef") not in one.map()
    assert one.raw("lef") in json.dumps(one.projection())


def test_a_json_candidate_that_records_no_mapped_digest_stays_raw(tmp_path):
    folder = tmp_path / "design"
    (folder / "runs" / "RUN_1").mkdir(parents=True)
    note = tmp_path / "note.json"
    note.write_bytes(b'{"paths": ["/somewhere/else"]}')
    document = {"note": {"path": str(note), "sha256": sha(note.read_bytes())}}
    roots = {"run": str(folder / "runs" / "RUN_1"), "design": str(folder)}
    assert dc.digest_map(dc.BUILD_DIE, INPUTS, document, roots, read) == {}


def test_the_files_of_the_out_fields_are_not_read(builds):
    one, _ = builds
    seen = []
    one.map(read=lambda path: seen.append(path) or read(path))
    assert str(one.run) not in seen
    assert not any(path.endswith("absent.json") for path in seen)
    assert sorted(seen) == sorted([str(path) for path, _ in one.files.values()] + [str(one.observation)])


def test_each_candidate_is_read_once(builds):
    one, _ = builds
    seen = []
    one.map(read=lambda path: seen.append(path) or read(path))
    assert len(seen) == len(set(seen)) == 5


# --- refusals of the map -----------------------------------------------------

def test_a_candidate_that_cannot_be_read_is_refused_naming_it_and_where(builds):
    one, _ = builds
    one.files["lef"][0].unlink()
    with pytest.raises(dc.CandidateUnreadable) as caught:
        one.map()
    assert (caught.value.file, caught.value.named_by, caught.value.at) == (
        str(one.files["lef"][0]), INPUTS, "/core/views/2")
    assert caught.value.reason


def test_a_candidate_whose_path_cannot_be_opened_is_refused_as_unreadable(builds):
    # A path the platform refuses before any read, such as one holding a NUL
    # byte, reaches the person who built the die as an unreadable file.
    one, _ = builds

    def refuse(path):
        raise ValueError("embedded null byte")
    with pytest.raises(dc.CandidateUnreadable) as caught:
        one.map(read=refuse)
    assert caught.value.reason == "embedded null byte"


def test_a_candidate_whose_bytes_moved_is_refused(builds):
    one, _ = builds
    one.files["spef"][0].write_bytes(spef(cap="9.0"))
    with pytest.raises(dc.CandidateChanged) as caught:
        one.map()
    assert (caught.value.file, caught.value.at) == (str(one.files["spef"][0]), "/core/views/1")


def test_a_file_named_with_two_digests_is_refused_naming_both_places(builds):
    one, _ = builds
    one.document["core"]["views"].append({"path": str(one.files["gds"][0]), "sha256": "0" * 64})
    with pytest.raises(dc.CandidateNamedTwice) as caught:
        one.map()
    assert (caught.value.file, caught.value.named_by, caught.value.at,
            caught.value.other_named_by, caught.value.other_at) == (
        str(one.files["gds"][0]), INPUTS, "/core/views/3", INPUTS, "/core/views/0")


def test_a_file_named_with_two_digests_by_two_files_names_each_file(builds):
    one, _ = builds
    observed = json.loads(one.observation.read_text())
    observed["views"][0]["sha256"] = "0" * 64
    one.observation.write_text(json.dumps(observed))
    one.document["core"]["observation"]["sha256"] = one.digest_of(one.observation)
    with pytest.raises(dc.CandidateNamedTwice) as caught:
        one.map()
    assert (caught.value.named_by, caught.value.at, caught.value.other_named_by,
            caught.value.other_at) == (str(one.observation), "/views/0", INPUTS, "/core/views/0")


@pytest.mark.parametrize("written", [str.upper, lambda digest: "sha256:" + digest])
def test_a_digest_not_written_as_the_files_digest_is_refused_as_not_the_files(builds, written):
    one, _ = builds
    one.document["core"]["views"][1]["sha256"] = written(one.raw("spef"))
    with pytest.raises(dc.CandidateChanged) as caught:
        one.map()
    assert (caught.value.file, caught.value.at) == (str(one.files["spef"][0]), "/core/views/1")


def test_a_refusal_inside_a_json_candidate_names_that_file_as_the_one_naming(builds):
    one, _ = builds
    one.files["sdf"][0].unlink()
    with pytest.raises(dc.CandidateUnreadable) as caught:
        one.map()
    assert (caught.value.named_by, caught.value.at) == (str(one.observation), "/views/1")


def test_a_stamped_candidate_cut_short_is_refused_with_its_kind_and_reason(tmp_path):
    folder = tmp_path / "design"
    (folder / "runs" / "RUN_1").mkdir(parents=True)
    cut = tmp_path / "cut.gds"
    cut.write_bytes(gds()[:-4])
    document = {"layout": {"path": str(cut), "sha256": sha(cut.read_bytes())}}
    roots = {"run": str(folder / "runs" / "RUN_1"), "design": str(folder)}
    with pytest.raises(dc.CandidateMalformed) as caught:
        dc.digest_map(dc.BUILD_DIE, INPUTS, document, roots, read)
    assert (caught.value.kind, caught.value.reason, caught.value.at) == (
        dc.GDS, "the stream ends before its ENDLIB record", "/layout")


def test_json_candidates_that_record_each_other_are_refused_as_a_cycle():
    # Two files cannot hold each other's true digests, so the cycle is built
    # on the candidates as read; it guards the order, not the files.
    a = dc._Candidate("/a.json", "a" * 64, None)
    b = dc._Candidate("/b.json", "b" * 64, None)
    a.document = {"other": "b" * 64}
    b.document = {"other": "a" * 64}
    with pytest.raises(dc.DigestCycle) as caught:
        dc._evidence_values({a.path: a, b.path: b})
    assert caught.value.files == ["/a.json", "/b.json"]


def test_a_json_candidate_that_records_its_own_digest_is_no_cycle():
    a = dc._Candidate("/a.json", "a" * 64, None)
    a.document = {"self": "a" * 64}
    dc._evidence_values({a.path: a})
    assert a.value is None


def test_the_map_refuses_what_a_projection_refuses(builds):
    one, _ = builds
    with pytest.raises(ChipletFormatError):
        dc.digest_map(dc.BUILD_DIE, "final/def/top.def", one.document, one.roots, read)
    with pytest.raises(ChipletFormatError):
        dc.digest_map(dc.BUILD_DIE, INPUTS, [], one.roots, read)


# --- the map in a row: project() and check_digest_map() ----------------------

def test_a_row_without_a_map_projects_as_before(builds):
    one, _ = builds
    assert (dc.project(dc.BUILD_DIE, INPUTS, one.document, one.roots, None)
            == dc.project(dc.BUILD_DIE, INPUTS, one.document, one.roots, {})
            == dc.project(dc.BUILD_DIE, INPUTS, one.document, one.roots))


@pytest.mark.parametrize("entry", [
    {"g" * 64: dc.CANON + "0" * 64},
    {"0" * 63: dc.CANON + "0" * 64},
    {"0" * 64: "raw:" + "0" * 64},
    {"0" * 64: dc.CONTENT + "0" * 63},
    {"0" * 64: 5},
])
def test_a_malformed_map_entry_is_refused(builds, entry):
    one, _ = builds
    with pytest.raises(ChipletFormatError, match="is malformed: the key must be a raw SHA-256"):
        dc.project(dc.BUILD_DIE, INPUTS, one.document, one.roots, entry)


def test_a_map_that_is_not_an_object_is_refused(builds):
    one, _ = builds
    with pytest.raises(ChipletFormatError, match="digest_map is not an object"):
        dc.project(dc.BUILD_DIE, INPUTS, one.document, one.roots, [])


def test_a_map_entry_for_a_digest_the_file_does_not_name_is_refused(builds):
    one, _ = builds
    stray = {"1" * 64: dc.CANON + "0" * 64}
    with pytest.raises(ChipletFormatError, match="which the file does not name"):
        dc.project(dc.BUILD_DIE, INPUTS, one.document, one.roots, stray)


def test_a_map_entry_for_a_digest_only_an_out_field_names_is_refused(builds):
    one, _ = builds
    stray = {"f" * 64: dc.CANON + "0" * 64}
    with pytest.raises(ChipletFormatError, match="which the file does not name"):
        dc.project(dc.BUILD_DIE, INPUTS, one.document, one.roots, stray)


def test_the_map_written_from_the_files_checks(builds):
    one, _ = builds
    dc.check_digest_map(dc.BUILD_DIE, INPUTS, one.document, one.roots, one.map(), read)


def test_a_map_lacking_an_entry_the_files_give_is_refused_naming_the_digest(builds):
    one, _ = builds
    mapping = one.map()
    del mapping[one.raw("spef")]
    with pytest.raises(ChipletFormatError) as caught:
        dc.check_digest_map(dc.BUILD_DIE, INPUTS, one.document, one.roots, mapping, read)
    assert str(caught.value) == (
        f"{INPUTS}: its digest map has no entry for {one.raw('spef')}, which the file names at "
        f"/core/views/1/sha256; a die-content/v2 row records the content value of every digest it "
        "replaces.")


def test_a_map_recording_another_canon_is_refused_naming_the_file_and_its_kind(builds):
    one, two = builds
    mapping = one.map()
    mapping[one.raw("gds")] = two.map()[two.raw("gds")].replace(dc.CANON, dc.CONTENT)
    with pytest.raises(ChipletFormatError) as caught:
        dc.check_digest_map(dc.BUILD_DIE, INPUTS, one.document, one.roots, mapping, read)
    assert str(caught.value).startswith(f"{one.files['gds'][0]}: its GDS canon is {dc.CANON}")
    assert str(caught.value).endswith("the map was not written from this file")


def test_a_map_recording_another_content_value_is_refused_naming_the_files_content_digest(builds):
    one, _ = builds
    mapping = one.map()
    mapping[one.digest_of(one.observation)] = dc.CONTENT + "0" * 64
    with pytest.raises(ChipletFormatError, match=r"core.observations.json: its content digest is content:[0-9a-f]{64}, but "):
        dc.check_digest_map(dc.BUILD_DIE, INPUTS, one.document, one.roots, mapping, read)


def test_a_record_reads_without_the_map_of_a_projected_row():
    row = {"path": "resolved.json", "sha256": "1" * 64, "content_sha256": "2" * 64}
    record = {"schema": dc.EMITTED_RECORD_SCHEMA, "bound": [row]}
    mapped = {"schema": dc.EMITTED_RECORD_SCHEMA,
              "bound": [{**row, dc.DIGEST_MAP: {"3" * 64: dc.CANON + "4" * 64}}]}
    assert dc.build_record_content(mapped) == dc.build_record_content(record)
    assert dc.DIGEST_MAP not in json.dumps(dc.build_record_content(mapped))


def test_a_row_written_before_v2_is_refused_with_its_files_in_hand_when_they_give_entries(builds):
    one, _ = builds
    with pytest.raises(ChipletFormatError, match="its digest map has no entry for"):
        dc.check_digest_map(dc.BUILD_DIE, INPUTS, one.document, one.roots, None, read)


def test_a_row_written_before_v2_whose_files_give_no_entry_checks():
    document = {"meta": {"flow": "x"}}
    roots = {"run": "/fake/design/runs/RUN_1", "design": "/fake/design"}
    dc.check_digest_map(dc.BUILD_DIE, INPUTS, document, roots, None, read)
