# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The reference carrier, IntM4TM2, through the technology package validator.

The fixture is a copy of the IntM4TM2 manifest and the members the validator
reads, at the release its PROVENANCE.md names. It passes sets A and B as it
ships, and fails set C on what the carrier does not describe yet: EM limits and
the resistances of its routing layers and vias. Each refusal changes one thing
in a copy, and the copy with nothing changed is the control. The DRC and major
version cases repeat synthetic carrier cases on purpose: the contract names this
carrier for them.
"""

import hashlib
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

from chiplet_format_io import technology_package as tp  # noqa: E402

FIXTURE = ROOT / "conformance" / "fixtures" / "technology_package" / "intm4tm2"
VARIANTS = ("routing.variants.base", "routing.variants.rdl2")
UNRESISTED = [f"{name} has no positive resistance_ohm_per_sq"
              for name in ("Metal4", "Metal5", "TopMetal1", "TopMetal2")] + [
             f"{name} has no positive resistance_ohm_per_cut"
              for name in ("Via4", "TopVia1", "TopVia2")]


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "intm4tm2"
    shutil.copytree(FIXTURE, root)
    return root


def _manifest(root):
    return json.loads(tp.manifest_path(root).read_text())


def _edit_manifest(root, edit):
    manifest = _manifest(root)
    edit(manifest)
    tp.manifest_path(root).write_text(json.dumps(manifest, indent=2) + "\n")


def _edit_descriptor(root, variant, edit):
    """Edit one variant's descriptor and record its new sha256."""
    path = root / _manifest(root)["routing"]["variants"][variant]["path"]
    descriptor = json.loads(path.read_text())
    edit(descriptor)
    path.write_text(json.dumps(descriptor, indent=2) + "\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _edit_manifest(root, lambda m: m["routing"]["variants"][variant].update(sha256=digest))


def _ids(report, set_name):
    return [(f.message_id, f.where) for f in report.findings[set_name]]


def _pair(text):
    return tuple(int(v) for v in text.split("/"))


def test_the_copy_is_the_bytes_its_provenance_names():
    receipt = json.loads((FIXTURE / "provenance.json").read_text())
    copied = {path: hashlib.sha256((FIXTURE / path).read_bytes()).hexdigest()
              for path in receipt["copied"]}
    assert copied == receipt["copied"]
    assert _manifest(FIXTURE)["revision"] == receipt["release"]
    assert all((FIXTURE / path).stat().st_size == 0 for path in receipt["placeholders"])
    listed = set(receipt["copied"]) | set(receipt["placeholders"])
    found = {str(p.relative_to(FIXTURE)) for p in (FIXTURE / "libs.tech").rglob("*") if p.is_file()}
    assert found == listed


def test_intm4tm2_passes_sets_a_and_b():
    report = tp.validate_package(FIXTURE, require=("A", "B"))
    assert report.id == "intm4tm2"


def test_intm4tm2_fails_set_c_on_what_it_does_not_describe_yet():
    report = tp.check_package(FIXTURE)
    found = [(f.message_id, f.where, f.text) for f in report.findings["C"]]
    assert found == [("PACKAGE_MEMBER_ABSENT", "em_limits", "the manifest does not declare it")] + [
        ("PACKAGE_RESISTANCE_MISSING", variant, text) for variant in VARIANTS for text in UNRESISTED]


def test_intm4tm2_occupied_pairs_are_the_layer_sheets():
    """The pairs are the sheet's sources, and every pair a descriptor writes is
    one of them. measured_by names the interposer's own test, which measures the
    decks too; this one holds the copy to the sheet that came with it."""
    sheet = ET.parse(FIXTURE / _manifest(FIXTURE)["layer_properties"]["lyp"])
    sources = {_pair(p.findtext("source")) for p in sheet.getroot().iter("properties")}
    written = set()
    for variant in _manifest(FIXTURE)["routing"]["variants"].values():
        descriptor = json.loads((FIXTURE / variant["path"]).read_text())
        written |= {_pair(pair) for purposes in descriptor["gds_layermap"].values()
                    for pair in purposes.values()}
    assert written <= sources
    assert [tuple(p) for p in _manifest(FIXTURE)["occupied_pairs"]["pairs"]] == sorted(sources)


def test_intm4tm2_without_its_drc_entry_is_refused_naming_it(package):
    _edit_manifest(package, lambda m: m.pop("drc"))
    assert _ids(tp.check_package(package), "B") == [("PACKAGE_MEMBER_ABSENT", "drc")]
    with pytest.raises(tp.TechnologyPackageError) as caught:
        tp.validate_package(package, require=("A", "B"))
    assert caught.value.message_id == "PACKAGE_SET_INCOMPLETE"
    assert tp.validate_package(package, require=("A",)).passes("A")


def test_intm4tm2_manifest_with_an_unknown_major_is_refused(package):
    _edit_manifest(package, lambda m: m.update(schema_version="2.0"))
    with pytest.raises(tp.TechnologyPackageError) as caught:
        tp.check_package(package)
    assert caught.value.message_id == "PACKAGE_SCHEMA_VERSION"


def test_intm4tm2_descriptor_requiring_a_newer_reader_fails_set_a(package):
    _edit_descriptor(package, "rdl2", lambda d: d.update(requires_reader="2.0"))
    assert _ids(tp.check_package(package), "A") == [("PACKAGE_DESCRIPTOR_READER", VARIANTS[1])]


def test_intm4tm2_descriptors_outside_the_accepted_range_fail_set_a(package):
    _edit_manifest(package, lambda m: m["routing"]["accepted"].update(min_minor=4))
    report = tp.check_package(package)
    assert _ids(report, "A") == [("PACKAGE_DESCRIPTOR_VERSION", variant) for variant in VARIANTS]
