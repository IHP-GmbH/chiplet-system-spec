# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the technology package validator.

The synthetic carrier under ``fixtures/technology_package`` is a complete
package that describes no process. It passes sets A, B and C, and every
refusal and finding below is made on a copy of it with ONE thing changed, so
the same copy with that thing changed back is the control: a check that always
fails would pass a test that only asks it to fail.
"""

import ast
import hashlib
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

from chiplet_format_io import ContractVersionWarning  # noqa: E402
from chiplet_format_io import technology_package as tp  # noqa: E402

FIXTURE = ROOT / "conformance" / "fixtures" / "technology_package" / "synthetic_carrier"
SCHEMA = ROOT / "schemas" / "technology_package.schema.json"
MODULE = ROOT / "reference" / "python" / "chiplet_format_io" / "technology_package.py"
DESCRIPTOR = "libs.tech/adk/routing/synthetic_carrier.routing_tech.json"
BASE = "routing.variants.base"

@pytest.fixture
def package(tmp_path):
    root = tmp_path / "synthetic_carrier"
    shutil.copytree(FIXTURE, root)
    return root


def _manifest(root):
    return json.loads(tp.manifest_path(root).read_text())


def _edit_manifest(root, edit):
    manifest = _manifest(root)
    edit(manifest)
    tp.manifest_path(root).write_text(json.dumps(manifest, indent=2) + "\n")


def _bind(root, variant, rel):
    digest = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    _edit_manifest(root, lambda m: m["routing"]["variants"].update(
        {variant: {"path": rel, "sha256": digest}}))


def _edit_descriptor(root, edit, *, rebind=True):
    """Edit the descriptor and, unless told not to, record its new sha256."""
    path = root / DESCRIPTOR
    descriptor = json.loads(path.read_text())
    edit(descriptor)
    path.write_text(json.dumps(descriptor, indent=2) + "\n")
    if rebind:
        _bind(root, "base", DESCRIPTOR)


def _ids(report, set_name):
    return [(f.message_id, f.where) for f in report.findings[set_name]]


def _only(report, set_name):
    """The findings of one set, after checking every other set passes."""
    others = [name for name in tp.SET_MEMBERS if name != set_name and not report.passes(name)]
    assert others == [], {name: _ids(report, name) for name in others}
    return _ids(report, set_name)


def _refusal(root):
    with pytest.raises(tp.TechnologyPackageError) as caught:
        tp.check_package(root)
    return caught.value.message_id


# --- the schema, its sets and its ids ----------------------------------------

def test_schema_is_wellformed_and_shipped():
    schema = json.loads(SCHEMA.read_text())
    validator_for(schema).check_schema(schema)
    setup = (ROOT / "reference" / "python" / "setup.py").read_text()
    assert '"technology_package.schema.json"' in setup


def test_schema_sets_are_the_module_sets():
    """The schema's ``x-capability-set`` and ``x-capability-optional`` are the
    module's SET_MEMBERS and OPTIONAL_MEMBERS."""
    schema = json.loads(SCHEMA.read_text())
    needed, optional = {}, {}
    for member, node in schema["properties"].items():
        for set_name in node.get("x-capability-set", ()):
            if node.get("x-capability-optional"):
                optional[member] = set_name
            else:
                needed.setdefault(set_name, set()).add(member)
    assert needed == {name: set(members) for name, members in tp.SET_MEMBERS.items()}
    assert optional == tp.OPTIONAL_MEMBERS


def _emitted_ids():
    """The ids the module's code uses, outside the MESSAGE_IDS table itself."""
    tree = ast.parse(MODULE.read_text())
    table = next(node for node in tree.body if isinstance(node, ast.AnnAssign)
                 and getattr(node.target, "id", None) == "MESSAGE_IDS")
    listed = {id(node) for node in ast.walk(table)}
    return {node.value for node in ast.walk(tree)
            if id(node) not in listed and isinstance(node, ast.Constant)
            and isinstance(node.value, str) and re.fullmatch(r"PACKAGE_[A-Z_]+", node.value)}


def test_every_message_id_is_listed_used_and_tested():
    assert _emitted_ids() == set(tp.MESSAGE_IDS)
    assert {kind for kind, _ in tp.MESSAGE_IDS.values()} == {"refusal", "verdict", "finding", "note"}
    tested = set(re.findall(r'"(PACKAGE_[A-Z_]+)"', Path(__file__).read_text()))
    assert set(tp.MESSAGE_IDS) - tested == set()


#: Schema keywords the member path walk does not descend into.
_UNWALKED = ("anyOf", "allOf", "not", "if", "then", "else", "patternProperties",
             "prefixItems", "contains", "dependentSchemas")


def _reaches_a_member_path(node, defs, seen=()):
    if isinstance(node, list):
        return any(_reaches_a_member_path(n, defs, seen) for n in node)
    if not isinstance(node, dict):
        return False
    ref = node.get("$ref")
    if ref in tp._PATH_KINDS:
        return True
    if ref and ref not in seen:
        return _reaches_a_member_path(defs[ref.rsplit("/", 1)[1]], defs, seen + (ref,))
    return any(_reaches_a_member_path(v, defs, seen) for v in node.values())


def test_no_member_path_hides_where_the_walk_does_not_go():
    """The walk follows $ref, oneOf object branches, items, properties and
    additionalProperties. A member path under any other keyword, or under a
    oneOf branch that is not an object, would never be checked."""
    schema = json.loads(SCHEMA.read_text())
    defs = schema["$defs"]
    hidden = []

    def visit(node, where):
        if isinstance(node, list):
            for k, item in enumerate(node):
                visit(item, f"{where}[{k}]")
            return
        if not isinstance(node, dict):
            return
        for keyword in _UNWALKED:
            if keyword in node and _reaches_a_member_path(node[keyword], defs):
                hidden.append(f"{where}.{keyword}")
        for k, branch in enumerate(node.get("oneOf", ())):
            if branch.get("type") != "object" and _reaches_a_member_path(branch, defs):
                hidden.append(f"{where}.oneOf[{k}]")
        for key, value in node.items():
            visit(value, f"{where}.{key}")

    visit(schema, "#")
    assert hidden == []


def test_an_installation_without_the_schema_refuses(package, monkeypatch):
    monkeypatch.setattr(tp, "_read_packaged_schema", lambda name: None)
    tp._schema_text.cache_clear()
    try:
        assert _refusal(package) == "PACKAGE_SCHEMA_ABSENT"
    finally:
        tp._schema_text.cache_clear()


# --- the passing package -----------------------------------------------------

def test_synthetic_carrier_passes_every_set():
    report = tp.validate_package(FIXTURE, require=("A", "B", "C"))
    assert report.id == "synthetic_carrier"
    assert all(report.passes(name) for name in tp.SET_MEMBERS)


def test_synthetic_occupied_pairs_are_the_measured_ones():
    """The manifest's measured_by points here: the pairs are recomputed from
    the layer sheet and the descriptor's write-out map, never copied."""
    sheet = ET.parse(FIXTURE / _manifest(FIXTURE)["layer_properties"]["lyp"])
    measured = {tuple(int(v) for v in s.split("/"))
                for s in (p.findtext("source") for p in sheet.getroot().iter("properties"))}
    descriptor = json.loads((FIXTURE / DESCRIPTOR).read_text())
    written = {tuple(int(v) for v in pair.split("/"))
               for purposes in descriptor["gds_layermap"].values() for pair in purposes.values()}
    assert written <= measured
    pairs = _manifest(FIXTURE)["occupied_pairs"]
    assert pairs["measured_by"].endswith("::" + test_synthetic_occupied_pairs_are_the_measured_ones.__name__)
    assert sorted(map(tuple, pairs["pairs"])) == sorted(measured)


def test_nothing_the_fixture_exercises_names_the_reference_carrier():
    """A second carrier must pass without any engine naming the first one."""
    sources = [p for p in FIXTURE.rglob("*") if p.is_file()] + [MODULE, SCHEMA]
    hits = [str(p) for p in sources if "intm4tm2" in p.read_text().lower()]
    assert hits == []


# --- refusals: nothing about the sets can be said ----------------------------

def test_manifest_missing_is_refused(package):
    tp.manifest_path(package).unlink()
    assert _refusal(package) == "PACKAGE_MANIFEST_MISSING"


@pytest.mark.parametrize("text", ["{ not json", "[]"])
def test_manifest_not_an_object_is_refused(package, text):
    tp.manifest_path(package).write_text(text)
    assert _refusal(package) == "PACKAGE_MANIFEST_UNREADABLE"


def test_unknown_major_is_refused(package):
    _edit_manifest(package, lambda m: m.update(schema_version="2.0"))
    assert _refusal(package) == "PACKAGE_SCHEMA_VERSION"
    _edit_manifest(package, lambda m: m.update(schema_version="1.0"))
    assert tp.check_package(package).passes("A")


def test_unknown_member_at_this_minor_is_refused(package):
    _edit_manifest(package, lambda m: m.update(future_member={"path": "x"}))
    assert _refusal(package) == "PACKAGE_SCHEMA_INVALID"


def test_unknown_member_at_a_higher_minor_is_ignored_and_reported(package):
    _edit_manifest(package, lambda m: m.update(schema_version="1.1", future_member={"path": "x"}))
    seen = []
    with pytest.warns(ContractVersionWarning):
        report = tp.check_package(package, on_warn=seen.append)
    assert all(report.passes(name) for name in tp.SET_MEMBERS)
    assert any("'future_member'" in text and "ignored" in text for text in seen)


def test_die_role_is_not_modelled(package):
    _edit_manifest(package, lambda m: m.update(roles=["carrier", "die"]))
    assert _refusal(package) == "PACKAGE_ROLE_NOT_MODELLED"


# --- member paths ------------------------------------------------------------

def test_drc_entry_removed_fails_set_b_only(package):
    _edit_manifest(package, lambda m: m.pop("drc"))
    report = tp.check_package(package)
    assert _only(report, "B") == [("PACKAGE_MEMBER_ABSENT", "drc")]
    assert tp.validate_package(package, require=("A",)).passes("A")
    with pytest.raises(tp.TechnologyPackageError) as caught:
        tp.validate_package(package)
    assert caught.value.message_id == "PACKAGE_SET_INCOMPLETE"
    assert "set B, drc" in str(caught.value)


@pytest.mark.parametrize("raw, words", [
    ("/abs/run_drc.py", "absolute"),
    ("../run_drc.py", "climbs out"),
    ("${PDK_ROOT}/drc/run_drc.py", "variable"),
    ("%PDK_ROOT%/drc/run_drc.py", "variable"),
    ("~/drc/run_drc.py", "home folder"),
    ("C:/drc/run_drc.py", "drive"),
    ("D:run_drc.py", "drive"),
    ("drc\\run_drc.py", "backslash"),
    ("drc/run\0drc.py", "NUL"),
])
def test_unportable_member_path(package, raw, words):
    _edit_manifest(package, lambda m: m["drc"].update(runner=raw))
    (finding,) = tp.check_package(package).findings["B"]
    assert (finding.message_id, finding.where) == ("PACKAGE_MEMBER_PATH", "drc.runner")
    assert words in finding.text


def test_a_link_out_of_the_root_is_refused(package, tmp_path):
    outside = tmp_path / "elsewhere.py"
    outside.write_text("")
    runner = package / "drc" / "run_drc.py"
    runner.unlink()
    os.symlink(outside, runner)
    (finding,) = tp.check_package(package).findings["B"]
    assert (finding.message_id, finding.where) == ("PACKAGE_MEMBER_PATH", "drc.runner")
    assert "link" in finding.text
    # Control: a link that stays inside the root is an ordinary member.
    runner.unlink()
    os.symlink(package / "drc" / "synthetic_carrier.drc", runner)
    assert tp.check_package(package).passes("B")


def test_a_manifest_linked_from_outside_the_root_is_refused(package, tmp_path):
    manifest = tp.manifest_path(package)
    outside = tmp_path / "technology.json"
    shutil.move(manifest, outside)
    os.symlink(outside, manifest)
    assert _refusal(package) == "PACKAGE_MANIFEST_MISSING"


def test_member_file_missing(package):
    (package / "drc" / "run_drc.py").unlink()
    assert _only(tp.check_package(package), "B") == [("PACKAGE_MEMBER_MISSING", "drc.runner")]


@pytest.mark.parametrize("member, key, raw", [
    ("drc", "runner", "drc"),                 # a folder where a file is expected
    ("layer_properties", "lyt", "."),
    ("drc", "deck_folder", "drc/run_drc.py"),  # a file where a folder is expected
])
def test_member_of_the_wrong_kind(package, member, key, raw):
    _edit_manifest(package, lambda m: m[member].update({key: raw}))
    report = tp.check_package(package)
    found = [f for name in tp.SET_MEMBERS for f in report.findings[name]]
    assert [(f.message_id, f.where) for f in found] == [("PACKAGE_MEMBER_MISSING", f"{member}.{key}")]
    assert "is not a" in found[0].text


def test_halo_tool_is_a_member_path(package):
    _edit_manifest(package, lambda m: m.update(fill={"halo_tool": "drc/halo.py"}))
    assert _only(tp.check_package(package), "B") == [("PACKAGE_MEMBER_MISSING", "fill.halo_tool")]
    (package / "drc" / "halo.py").write_text("")
    assert tp.check_package(package).passes("B")


# --- routing descriptors (sets A and C) --------------------------------------

def test_descriptor_bytes_must_match_the_recorded_sha256(package):
    _edit_descriptor(package, lambda d: d["identity"].update(version="0.1.1"), rebind=False)
    report = tp.check_package(package)
    assert _ids(report, "A") == [("PACKAGE_DIGEST_MISMATCH", f"{BASE}.path")]
    # A descriptor that is not the bound one cannot vouch for its resistances.
    assert _ids(report, "C") == [("PACKAGE_RESISTANCE_MISSING", BASE)]


def test_descriptor_not_json(package):
    (package / DESCRIPTOR).write_text("{ not json")
    _bind(package, "base", DESCRIPTOR)
    report = tp.check_package(package)
    assert _ids(report, "A") == [("PACKAGE_MEMBER_UNREADABLE", f"{BASE}.path")]
    assert _ids(report, "C") == [("PACKAGE_RESISTANCE_MISSING", BASE)]


def test_routing_absent_fails_sets_a_and_c(package):
    _edit_manifest(package, lambda m: m.pop("routing"))
    report = tp.check_package(package)
    assert _ids(report, "A") == [("PACKAGE_MEMBER_ABSENT", "routing")]
    assert _ids(report, "C") == [("PACKAGE_MEMBER_ABSENT", "routing")]
    assert report.passes("B")


def test_every_variant_is_judged(package):
    """One readable variant does not vouch for another that is not."""
    _edit_manifest(package, lambda m: m["routing"]["variants"].update(
        rdl={"path": "libs.tech/adk/routing/rdl.routing_tech.json", "sha256": "0" * 64}))
    report = tp.check_package(package)
    assert _ids(report, "A") == [("PACKAGE_MEMBER_MISSING", "routing.variants.rdl.path")]
    assert _ids(report, "C") == [("PACKAGE_RESISTANCE_MISSING", "routing.variants.rdl")]
    # Control: the same second variant, present, named and bound, passes both.
    _second_variant(package, "rdl", "synthetic_carrier_rdl")
    report = tp.check_package(package)
    assert report.passes("A") and report.passes("C")


def _second_variant(root, variant, name):
    rel = f"libs.tech/adk/routing/{variant}.routing_tech.json"
    descriptor = json.loads((root / DESCRIPTOR).read_text())
    descriptor["identity"]["name"] = name
    (root / rel).write_text(json.dumps(descriptor, indent=2) + "\n")
    _bind(root, variant, rel)


def test_two_variants_may_not_share_a_name(package):
    _second_variant(package, "rdl", "synthetic_carrier")
    assert _only(tp.check_package(package), "A") == [("PACKAGE_DESCRIPTOR_NAME", "routing.variants")]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_descriptor_that_cannot_be_read(package):
    (package / DESCRIPTOR).chmod(0)
    try:
        report = tp.check_package(package)
    finally:
        (package / DESCRIPTOR).chmod(0o644)
    assert _ids(report, "A") == [("PACKAGE_MEMBER_UNREADABLE", f"{BASE}.path")]
    assert _ids(report, "C") == [("PACKAGE_RESISTANCE_MISSING", BASE)]


@pytest.mark.parametrize("value, passes", [
    ("1.3", True), ("1.9.2", True), ("1.0", True), ("2.0", False), ("1", False), (13, False)])
def test_descriptor_requires_reader_names_the_accepted_major(package, value, passes):
    _edit_descriptor(package, lambda d: d.update(requires_reader=value))
    expected = [] if passes else [("PACKAGE_DESCRIPTOR_READER", BASE)]
    assert _only(tp.check_package(package), "A") == expected


@pytest.mark.parametrize("edit_manifest, edit_descriptor", [
    (lambda m: m["routing"]["accepted"].update(min_minor=4), None),
    (lambda m: m["routing"]["accepted"].update(major=2), None),
    (None, lambda d: d.pop("schema_version")),
])
def test_descriptor_schema_version_outside_the_accepted_range(package, edit_manifest, edit_descriptor):
    """A shape this reader does not know vouches for nothing, in set C either."""
    if edit_manifest:
        _edit_manifest(package, edit_manifest)
    if edit_descriptor:
        _edit_descriptor(package, edit_descriptor)
    report = tp.check_package(package)
    assert ("PACKAGE_DESCRIPTOR_VERSION", BASE) in _ids(report, "A")
    assert _ids(report, "C") == [("PACKAGE_RESISTANCE_MISSING", BASE)]


def test_descriptor_must_agree_with_the_manifest_grid(package):
    _edit_descriptor(package, lambda d: d["identity"].update(manufacturing_grid_um=0.001))
    assert _only(tp.check_package(package), "A") == [("PACKAGE_DESCRIPTOR_DISAGREES", BASE)]


def test_default_variant_must_be_a_variant(package):
    _edit_manifest(package, lambda m: m["routing"].update(default_variant="rdl"))
    assert _only(tp.check_package(package), "A") == [("PACKAGE_DEFAULT_VARIANT", "routing.default_variant")]


@pytest.mark.parametrize("edit", [
    lambda d: d.update(identity="x"),
    lambda d: d.update(routing_layers={"a": 1}),
    lambda d: d.update(vias=[1, 2]),
    lambda d: d.update(vias=[]),
    lambda d: d.pop("routing_layers"),
])
def test_a_descriptor_of_an_unexpected_shape_is_a_finding_not_a_crash(package, edit):
    _edit_descriptor(package, edit)
    report = tp.check_package(package)
    assert not (report.passes("A") and report.passes("C"))


def test_missing_resistance_fails_set_c_only(package):
    _edit_descriptor(package, lambda d: d["vias"][0].pop("resistance_ohm_per_cut"))
    report = tp.check_package(package)
    (finding,) = report.findings["C"]
    assert finding.message_id == "PACKAGE_RESISTANCE_MISSING" and "V1" in finding.text
    assert report.passes("A") and report.passes("B")


def test_em_limits_absent_fails_set_c(package):
    _edit_manifest(package, lambda m: m.pop("em_limits"))
    assert _only(tp.check_package(package), "C") == [("PACKAGE_MEMBER_ABSENT", "em_limits")]


def test_em_limits_not_json(package):
    (package / "libs.tech/adk/em_limits.json").write_text("[]")
    assert _only(tp.check_package(package), "C") == [("PACKAGE_MEMBER_UNREADABLE", "em_limits.path")]


# --- layer sheet, stack and pairs (set A) -------------------------------------

@pytest.mark.parametrize("value", [None, True, -1.0, 0, "300", 10**400])
def test_stack_needs_positive_numbers(package, value):
    stack = package / "libs.tech" / "synthetic_carrier_stack.json"
    data = json.loads(stack.read_text())
    if value is None:
        del data["beol"]["attachment_surface_z_um"]
    else:
        data["beol"]["attachment_surface_z_um"] = value
    stack.write_text(json.dumps(data))
    (finding,) = tp.check_package(package).findings["A"]
    assert finding.message_id == "PACKAGE_STACK_KEY"
    assert "beol.attachment_surface_z_um" in finding.text


def test_external_layer_must_be_in_the_layer_sheet(package):
    _edit_manifest(package, lambda m: m["external"]["wire_bond"].update(opening_layer="Passiv"))
    assert _only(tp.check_package(package), "A") == [
        ("PACKAGE_LAYER_UNKNOWN", "external.wire_bond.opening_layer")]


def test_layer_sheet_not_xml(package):
    (package / "libs.tech/klayout/tech/synthetic_carrier.lyp").write_text("<layer-properties>")
    assert _only(tp.check_package(package), "A") == [
        ("PACKAGE_MEMBER_UNREADABLE", "layer_properties.lyp")]


@pytest.mark.parametrize("edit", [
    lambda p: p.update(state="declared"),
    lambda p: p.pop("measured_by"),
])
def test_unmeasured_pairs_do_not_pass_set_a(package, edit):
    _edit_manifest(package, lambda m: edit(m["occupied_pairs"]))
    assert _only(tp.check_package(package), "A") == [("PACKAGE_PAIRS_DECLARED", "occupied_pairs")]


# --- escape capacity (set A, optional) ----------------------------------------

def _entry(**changes):
    entry = {
        "method": "vendorx_microbump", "crossing_axis": "horizontal", "supply": "bare",
        "pitch_um": 50.0, "depth_clean": 6, "depth_failing": 8, "edge_load_per_mm": 120.0,
        "reading": "escape_limited",
        "bounds": {"lower": "router setting below", "upper": "order-preserving pin map"},
        "router": {"engine": "openroad", "engine_version": "0123abc",
                   "setting": {"descriptor": "synthetic_carrier", "drt_iterations": 5}},
        "coupon": {"shape": "square", "far_end": "mirrored die", "pairing": "order_preserving",
                   "end_margin_um": 100.0, "margin_rule": "band-free", "channel_um": 100.0,
                   "interposer_margin_um": 100.0, "flank_margin_um": 0.0},
        "measured_by": {"repo": "interposer-pnr", "sha": "0123abc",
                        "argv": ["interposer-pnr", "escape-capacity"]},
        "report_sha256": "a" * 64,
    }
    entry.update(changes)
    return entry


@pytest.fixture
def measured(package, tmp_path):
    """The package with one escape capacity entry, its descriptor routing on
    both axes, and a registry giving the method's pitch."""
    _edit_descriptor(package, lambda d: d["routability"].update(route_layers=["met1", "met2"]))
    _edit_manifest(package, lambda m: m.update(escape_capacity=[_entry()]))
    registry = tmp_path / "interconnect_methods.json"
    registry.write_text(json.dumps({"schema_version": "1.1", "methods": {
        "vendorx_microbump": {"pitch_rules": {"IXN_spacing": 25.0, "IXN_pitch": 50.0}}}}))
    return package, registry


def test_escape_capacity_absent_is_not_measured_and_passes():
    assert "escape_capacity" not in _manifest(FIXTURE)
    report = tp.check_package(FIXTURE)
    assert report.passes("A") and report.notes == []


def test_escape_capacity_entry_passes_with_the_registry(measured):
    package, registry = measured
    report = tp.check_package(package, methods=registry)
    assert all(report.passes(name) for name in tp.SET_MEMBERS) and report.notes == []


def test_without_the_registry_the_pitch_is_noted_not_checked(measured):
    package, _ = measured
    report = tp.check_package(package)
    assert report.passes("A")
    assert [(n.message_id, n.where) for n in report.notes] == [
        ("PACKAGE_PITCH_NOT_CHECKED", "escape_capacity[0]")]


def _setting(descriptor):
    return {"engine": "openroad", "engine_version": "0123abc",
            "setting": {"descriptor": descriptor, "drt_iterations": 5}}


@pytest.mark.parametrize("entries, expected", [
    ([_entry(), _entry(depth_clean=5, edge_load_per_mm=100.0)],
     [("PACKAGE_ESCAPE_KEY", "escape_capacity[1]")]),
    # A method outside attach is also one the registry gives no pitch for.
    ([_entry(method="cupillar_x")],
     [("PACKAGE_ESCAPE_METHOD", "escape_capacity[0]"), ("PACKAGE_ESCAPE_PITCH", "escape_capacity[0]")]),
    ([_entry(depth_failing=6)], [("PACKAGE_ESCAPE_MEASURE", "escape_capacity[0]")]),
    ([_entry(edge_load_per_mm=120.0 * (1 + 1e-7))], [("PACKAGE_ESCAPE_MEASURE", "escape_capacity[0]")]),
    ([_entry(depth_clean=10**400)], [("PACKAGE_ESCAPE_MEASURE", "escape_capacity[0]")]),
    ([_entry(router=_setting("other"))], [("PACKAGE_ESCAPE_AXIS", "escape_capacity[0]")]),
    ([_entry(pitch_um=40.0, edge_load_per_mm=150.0)], [("PACKAGE_ESCAPE_PITCH", "escape_capacity[0]")]),
])
def test_escape_capacity_entry_findings(measured, entries, expected):
    package, registry = measured
    _edit_manifest(package, lambda m: m.update(escape_capacity=entries))
    assert _only(tp.check_package(package, methods=registry), "A") == expected


def test_supply_is_part_of_the_key(measured):
    package, registry = measured
    _edit_manifest(package, lambda m: m.update(escape_capacity=[_entry(), _entry(supply="mesh")]))
    assert tp.check_package(package, methods=registry).passes("A")


def test_escape_axis_needs_a_route_layer_with_that_direction(measured):
    package, registry = measured
    _edit_descriptor(package, lambda d: d["routing_layers"][0].update(preferred_direction="VERTICAL"))
    assert _only(tp.check_package(package, methods=registry), "A") == [
        ("PACKAGE_ESCAPE_AXIS", "escape_capacity[0]")]
    # Control: both route layers now run vertical, the axis this entry crosses.
    _edit_manifest(package, lambda m: m["escape_capacity"][0].update(crossing_axis="vertical"))
    assert tp.check_package(package, methods=registry).passes("A")


@pytest.mark.parametrize("edit", [
    lambda d: d["routability"].pop("route_layers"),
    lambda d: d["routability"].update(route_layers=["met1"]),
])
def test_a_single_layer_backend_carries_no_entry(measured, edit):
    """interposer-pnr routes one route layer, or none, with its single-layer
    backend; the measurement is the multi-layer router's."""
    package, registry = measured
    _edit_descriptor(package, edit)
    report = tp.check_package(package, methods=registry)
    assert _only(report, "A") == [("PACKAGE_ESCAPE_AXIS", "escape_capacity[0]")]
    assert "single-layer" in report.findings["A"][0].text


def test_an_entry_naming_two_variants_is_a_finding(measured):
    """Two variants under one name: the verdict must not depend on which sorts last."""
    package, registry = measured
    _second_variant(package, "aa", "synthetic_carrier")
    found = _only(tp.check_package(package, methods=registry), "A")
    assert found == [("PACKAGE_DESCRIPTOR_NAME", "routing.variants"),
                     ("PACKAGE_ESCAPE_AXIS", "escape_capacity[0]")]


@pytest.mark.parametrize("edit", [
    lambda d: d["identity"].update(name=["synthetic_carrier"]),
    lambda d: d["routability"].update(route_layers=[["met1"], "met2"]),
    lambda d: d["routing_layers"][0].update(abstract_name=["met1"]),
])
def test_a_malformed_descriptor_under_an_entry_is_a_finding_not_a_crash(measured, edit):
    package, registry = measured
    _edit_descriptor(package, edit)
    assert not tp.check_package(package, methods=registry).passes("A")


def test_a_registry_pitch_too_large_for_a_float_gives_no_pitch(measured):
    package, registry = measured
    registry.write_text('{"schema_version": "1.1", "methods": {"vendorx_microbump": '
                        '{"pitch_rules": {"IXN_pitch": 1' + "0" * 400 + '}}}}')
    assert _only(tp.check_package(package, methods=registry), "A") == [
        ("PACKAGE_ESCAPE_PITCH", "escape_capacity[0]")]


@pytest.mark.parametrize("text", ["{ not json", '{"schema_version": "2.0", "methods": {}}', "[]"])
def test_an_unreadable_registry_is_refused(measured, text):
    package, registry = measured
    registry.write_text(text)
    with pytest.raises(tp.TechnologyPackageError) as caught:
        tp.check_package(package, methods=registry)
    assert caught.value.message_id == "PACKAGE_METHODS_UNREADABLE"


# --- the command -------------------------------------------------------------

def test_command_exit_codes_and_report(package, tmp_path, capsys):
    out = tmp_path / "report.json"
    assert tp.main([str(package), "--require", "A,B,C", "--report", str(out)]) == 0
    assert json.loads(out.read_text())["sets"]["C"]["passes"] is True

    _edit_manifest(package, lambda m: m.pop("drc"))
    assert tp.main([str(package)]) == 1
    assert tp.main([str(package), "--require", "A"]) == 0
    assert "PACKAGE_MEMBER_ABSENT: set B, drc" in capsys.readouterr().out

    _edit_manifest(package, lambda m: m.update(schema_version="2.0"))
    assert tp.main([str(package), "--report", str(out)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("PACKAGE_SCHEMA_VERSION:")
    assert json.loads(out.read_text())["refused"]["message_id"] == "PACKAGE_SCHEMA_VERSION"


@pytest.mark.parametrize("require", ["", "A,D"])
def test_command_refuses_a_requirement_it_cannot_judge(package, require):
    with pytest.raises(SystemExit) as caught:
        tp.main([str(package), "--require", require])
    assert caught.value.code == 2


def test_command_prints_a_higher_minor_warning_once(package, capsys):
    _edit_manifest(package, lambda m: m.update(schema_version="1.1"))
    assert tp.main([str(package)]) == 0
    assert capsys.readouterr().err.count("newer than the supported") == 1


def test_command_counts_what_it_did_not_check(measured, tmp_path, capsys):
    package, registry = measured
    out = tmp_path / "report.json"
    assert tp.main([str(package), "--require", "A", "--report", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "PACKAGE_PITCH_NOT_CHECKED: note, set A, escape_capacity[0]" in printed
    assert "set A: pass, 1 not checked" in printed
    report = json.loads(out.read_text())
    assert report["sets"]["A"]["not_checked"] == 1 and report["refused"] is None
    assert [n["message_id"] for n in report["notes"]] == ["PACKAGE_PITCH_NOT_CHECKED"]
    # Control: with the registry the pitch is checked and the pass is plain.
    assert tp.main([str(package), "--require", "A", "--methods", str(registry)]) == 0
    printed = capsys.readouterr().out
    assert "PACKAGE_PITCH_NOT_CHECKED" not in printed and "set A: pass\n" in printed


def test_command_refuses_an_unreadable_registry(measured, tmp_path, capsys):
    package, registry = measured
    registry.write_text("[]")
    out = tmp_path / "report.json"
    assert tp.main([str(package), "--methods", str(registry), "--report", str(out)]) == 2
    assert capsys.readouterr().err.startswith("PACKAGE_METHODS_UNREADABLE:")
    assert json.loads(out.read_text())["refused"]["message_id"] == "PACKAGE_METHODS_UNREADABLE"


def test_a_validator_defect_is_not_a_failing_set(package, tmp_path, monkeypatch, capsys):
    """Exit 3, and the report says so: a stale passing report must not survive."""
    out = tmp_path / "report.json"
    assert tp.main([str(package), "--report", str(out)]) == 0

    def broken(*args, **kwargs):
        raise RuntimeError("planted")

    monkeypatch.setattr(tp, "check_package", broken)
    assert tp.main([str(package), "--report", str(out)]) == 3
    assert "PACKAGE_VALIDATOR_FAILED: RuntimeError: planted" in capsys.readouterr().err
    assert json.loads(out.read_text())["refused"]["message_id"] == "PACKAGE_VALIDATOR_FAILED"
