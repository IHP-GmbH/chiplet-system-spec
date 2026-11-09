# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the die output contract reader.

Every refusal here is paired with a control that PASSES on the same fixture with
one thing changed back. That pairing is the point of the file rather than a
courtesy: a check that only ever refuses passes any test that only asks it to
refuse, so a refusal test alone cannot tell a working check from a broken one
that always says no. The same discipline applies to the three-value attestation
answer, where all three values are shown reachable.

The fixture uses DISTINCT pad names throughout, matching the real vehicles
(driver.pins.json is 48 pads with 48 distinct names, echo.pins.json 36 with 36),
because the consumer that places bumps selects same-name pads by occurrence
order and would ignore any selector the assembly carried. The repeated-name case
is here as a refusal, not as a resolution.
"""

import copy
import hashlib
import json
import sys
from pathlib import Path

import jsonschema
import pytest
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

from chiplet_format_io import die_contract as dc  # noqa: E402

SCHEMAS = ROOT / "schemas"
BOX_UM = [0.0, 0.0, 500.0, 400.0]
# pins.json declares die_bbox_dbu an OBJECT. It was a list here, which is the
# shape the code under test expected, so fixture and code agreed with each other
# and neither agreed with the format. Every fixture is now validated against the
# shipped schema for exactly that reason.
BOX_DBU = {"x_min": 0, "y_min": 0, "x_max": 500000, "y_max": 400000}


def test_strict_admission_capability_is_explicit_and_separate_from_document_version():
    assert "STRICT_ADMISSION_API_VERSION" in dc.__all__
    assert type(dc.STRICT_ADMISSION_API_VERSION) is int
    assert dc.STRICT_ADMISSION_API_VERSION == 4
    assert dc.SUPPORTED_DIE_CONTRACT_VERSION == "1.2"
    # A writer that stamps what its own reader refuses is a policy nobody can
    # follow, and the document that describes the revision is part of it.
    assert dc.SUPPORTED_DIE_CONTRACT_VERSION in dc.ACCEPTED_DIE_CONTRACT_VERSIONS
    described = (ROOT / "docs" / "STRICT_ADMISSION_API.md").read_text(encoding="utf-8")
    assert f"is the integer `{dc.STRICT_ADMISSION_API_VERSION}`" in described


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2) + "\n")
    return Path(path)


def _schema(name):
    return json.loads((SCHEMAS / name).read_text())


def _validate(instance, name):
    """A fixture that the shipped schema would reject is not evidence of anything."""
    jsonschema.validate(instance, _schema(name))
    return instance


def _pad(name, index, *, size=True, x=None):
    pad = {"name": name, "pad_index": index,
           "center_x_dbu": x if x is not None else 1000 * (index + 1),
           "center_y_dbu": 2000}
    if size:
        pad["width_dbu"] = 40000
        pad["height_dbu"] = 40000
    return pad


def _pins(tmp, pads, *, box=None):
    body = {"version": "1.0", "source_type": "hand_authored", "dbu_um": 0.001,
            "die_bbox_dbu": dict(BOX_DBU) if box is None else box, "pins": pads}
    if box is None:
        _validate(body, "pins.schema.json")
    return _write(tmp / "pins.json", body)


def _opening():
    return {"purpose": "aperture", "gds_layer": 9, "gds_datatype": 0, "shape": "octagon"}


def _terminal(name, index, **kw):
    term = {"name": name, "pad_index": index, "role": "signal", "opening": _opening()}
    term.update(kw)
    return term


def _contract(tmp, *, pins_path, layout, build, terminals, attestation=None):
    body = {
        "schema": "die-output-contract",
        "version": "1.0",
        "die": {"name": "driver", "layout": layout.name, "layout_sha256": _sha(layout),
                "top_cell": "DRIVER", "dbu_um": 0.001, "bbox_um": list(BOX_UM),
                "frame": "die_local_gds_origin"},
        "process": {"pdk": "ihp-sg13g2", "pdk_version": "dev-0de0a17"},
        "build_record": {"path": build.name, "sha256": _sha(build)},
        "inventory": {"kind": "pins_json", "path": Path(pins_path).name,
                      "sha256": _sha(pins_path)},
        "terminals": terminals,
    }
    if attestation is not None:
        body["attestation"] = attestation
    _validate(body, "die_contract.schema.json")
    return _write(tmp / "die_contract.json", body)


def _rebind_inventory(contract, pins):
    body = json.loads(Path(contract).read_text())
    body["inventory"]["sha256"] = _sha(pins)
    return _write(contract, body)


@pytest.fixture
def tree(tmp_path):
    """A minimal, internally consistent die: layout, build record, inventory, contract."""
    layout = tmp_path / "driver.gds"
    layout.write_bytes(b"not a real gds, but real bytes")
    build = _write(tmp_path / "build_record.json",
                   {"template": "chiplet-full", "inputs": [], "outputs": ["driver.gds"]})
    pins = _pins(tmp_path, [_pad("d2d_fclk", 0), _pad("vss_0", 0), _pad("vss_1", 0)])
    contract = _contract(
        tmp_path, pins_path=pins, layout=layout, build=build,
        terminals=[_terminal("d2d_fclk", 0, direction="input"),
                   _terminal("vss_0", 0, role="ground", supply_domain="vss"),
                   _terminal("vss_1", 0, role="ground", supply_domain="vss")])
    return tmp_path, contract, pins, layout, build


# --------------------------------------------------------------------------
# the real vehicles, which are what makes the unique-name profile viable
# --------------------------------------------------------------------------

def test_the_shipped_vehicle_pin_lists_carry_no_repeated_physical_name():
    """The premise of the whole unique-name route, checked rather than assumed."""
    flow = ROOT.parent / "chiplet-flow" / "vehicle" / "plans"
    lists = [flow / "driver.pins.json", flow / "echo.pins.json"]
    if not all(p.is_file() for p in lists):
        pytest.skip("chiplet-flow vehicle plans not present beside this checkout")
    for path in lists:
        pins = json.loads(path.read_text())["pins"]
        names = [p["name"] for p in pins]
        assert len(names) == len(set(names)), f"{path.name} repeats a physical pad name"


# --------------------------------------------------------------------------
# the schema itself
# --------------------------------------------------------------------------

def test_the_die_contract_schema_is_well_formed_and_accepts_a_real_document(tree):
    _, contract, _, _, _ = tree
    schema = json.loads((SCHEMAS / "die_contract.schema.json").read_text())
    validator_for(schema).check_schema(schema)
    jsonschema.validate(json.loads(contract.read_text()), schema)


@pytest.mark.parametrize("mutate,why", [
    (lambda d: d.pop("inventory"), "inventory is required"),
    (lambda d: d.pop("process"), "process is required"),
    (lambda d: d.pop("build_record"), "build_record is required"),
    (lambda d: d["die"].pop("bbox_um"), "the die box is required"),
    (lambda d: d["die"].update(frame="assembly"), "the frame is a const"),
    (lambda d: d.update(schema="something-else"), "the discriminator is a const"),
    (lambda d: d["terminals"][0].update(role="vdd"), "role is a closed vocabulary"),
    (lambda d: d["terminals"][0].update(surprise=1), "terminal keys are closed"),
    (lambda d: d["terminals"][0]["opening"].pop("purpose"), "an opening states its purpose"),
    (lambda d: d["terminals"][0]["opening"].update(gds_layer="Passiv"), "the layer is a number"),
    (lambda d: d["inventory"].update(kind="csv"), "inventory kind is a closed set"),
    (lambda d: d["die"].update(layout_sha256="nothex"), "a digest is 64 hex characters"),
])
def test_the_schema_refuses_each_shape_it_says_it_refuses(tree, mutate, why):
    _, contract, _, _, _ = tree
    schema = json.loads((SCHEMAS / "die_contract.schema.json").read_text())
    doc = json.loads(contract.read_text())
    mutate(doc)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema)


def test_the_chiplet_schema_carries_no_endpoint_selector():
    """A selector the bump consumer would ignore must not ship as an addition."""
    chiplet = json.loads((SCHEMAS / "chiplet.schema.json").read_text())
    conn = (chiplet["definitions"]["netlist"]["properties"]["nets"]["items"]
            ["properties"]["connections"]["items"]["properties"])
    assert "pad_index" not in conn


# --------------------------------------------------------------------------
# binding: the digest is what makes a statement about bytes
# --------------------------------------------------------------------------

def test_a_consistent_tree_binds(tree):
    _, contract, pins, _, _ = tree
    bound = dc.bind(contract)
    assert bound.kind == "pins_json"
    assert bound.inventory_path == pins
    assert set(bound.identities()) == {("d2d_fclk", 0), ("vss_0", 0), ("vss_1", 0)}


def test_a_regenerated_inventory_is_refused_and_binds_once_rebound(tree):
    tmp, contract, pins, _, _ = tree
    doc = json.loads(pins.read_text())
    doc["pins"].append(_pad("spare", 0))
    _write(pins, doc)                        # same path, different bytes
    with pytest.raises(dc.DieContractError, match="hashes to"):
        dc.bind(contract)
    _rebind_inventory(contract, pins)        # the control
    assert dc.bind(contract).identities()


def test_a_changed_layout_is_refused_and_accepted_once_rehashed(tree):
    _, contract, _, layout, _ = tree
    layout.write_bytes(b"different bytes entirely")
    with pytest.raises(dc.DieContractError, match="die.layout"):
        dc.bind(contract)
    body = json.loads(contract.read_text())
    body["die"]["layout_sha256"] = _sha(layout)
    _write(contract, body)
    assert dc.bind(contract)


def test_a_changed_build_record_is_refused_and_accepted_once_rehashed(tree):
    _, contract, _, _, build = tree
    _write(build, {"template": "something-else"})
    with pytest.raises(dc.DieContractError, match="build_record"):
        dc.bind(contract)
    body = json.loads(contract.read_text())
    body["build_record"]["sha256"] = _sha(build)
    _write(contract, body)
    assert dc.bind(contract)


def test_a_missing_inventory_is_refused_rather_than_treated_as_empty(tree):
    _, contract, pins, _, _ = tree
    pins.unlink()
    with pytest.raises(dc.DieContractError, match="does not exist"):
        dc.bind(contract)


@pytest.mark.parametrize("bad,match", [("/etc/passwd", "absolute"),
                                       ("../outside.json", "outside")])
def test_a_path_that_does_not_travel_is_refused(tree, bad, match):
    _, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    body["inventory"]["path"] = bad
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match=match):
        dc.bind(contract)


def test_a_foreign_artifact_is_refused_by_the_discriminator(tree):
    _, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    body["schema"] = "adk-boundary-manifest"
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="different artifact"):
        dc.load_die_contract(contract)


def test_a_different_major_is_refused_and_an_accepted_minor_is_not(tree):
    _, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    body["version"] = "2.0"
    _write(contract, body)
    with pytest.raises(Exception):
        dc.load_die_contract(contract)
    body["version"] = "1.0"
    _write(contract, body)
    assert dc.load_die_contract(contract)["version"] == "1.0"


# --------------------------------------------------------------------------
# the die box and the frame: terminals and outline must describe one die
# --------------------------------------------------------------------------

def test_a_box_that_disagrees_with_the_inventory_is_refused_and_agreeing_binds(tree):
    _, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    body["die"]["bbox_um"] = [0.0, 0.0, 900.0, 400.0]
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="different dies"):
        dc.bind(contract)
    body["die"]["bbox_um"] = list(BOX_UM)
    _write(contract, body)
    assert dc.bind(contract)


def test_an_inventory_without_a_die_box_is_refused(tree):
    _, contract, pins, _, _ = tree
    doc = json.loads(pins.read_text())
    del doc["die_bbox_dbu"]
    _write(pins, doc)
    _rebind_inventory(contract, pins)
    with pytest.raises(dc.DieContractError, match="die_bbox_dbu"):
        dc.bind(contract)


def test_an_unstated_frame_is_refused(tree):
    _, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    body["die"]["frame"] = "assembly_canonical"
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="frame"):
        dc.bind(contract)


# --------------------------------------------------------------------------
# identity, set equality, and the repeated-name refusal
# --------------------------------------------------------------------------

def test_a_terminal_the_die_does_not_have_is_refused_and_a_real_one_is_not(tree):
    _, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    keep = copy.deepcopy(body["terminals"])
    body["terminals"].append(_terminal("nosuch", 0))
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="not in the bound inventory"):
        dc.check_terminals(dc.bind(contract))
    body["terminals"] = keep
    _write(contract, body)
    dc.check_terminals(dc.bind(contract))


def test_a_pad_no_terminal_declares_is_refused_under_equality_and_allowed_without_it(tree):
    _, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    dropped = body["terminals"].pop()
    _write(contract, body)
    dc.check_terminals(dc.bind(contract))                       # identity only: fine
    with pytest.raises(dc.DieContractError, match="no terminal declares"):
        dc.check_terminals(dc.bind(contract), require_equality=True)
    body["terminals"].append(dropped)
    _write(contract, body)
    dc.check_terminals(dc.bind(contract), require_equality=True)


def test_the_same_identity_twice_is_refused(tree):
    _, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    body["terminals"].append(_terminal("vss_1", 0, role="ground"))
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="declared twice"):
        dc.check_terminals(dc.bind(contract))


def test_repeated_physical_names_are_refused_while_distinct_ones_pass(tree):
    tmp, contract, pins, _, _ = tree
    doc = json.loads(pins.read_text())
    doc["pins"] = [_pad("d2d_fclk", 0), _pad("vss", 0), _pad("vss", 1)]
    _write(pins, doc)
    body = json.loads(contract.read_text())
    body["inventory"]["sha256"] = _sha(pins)
    body["terminals"] = [_terminal("d2d_fclk", 0),
                         _terminal("vss", 0, role="ground", supply_domain="vss"),
                         _terminal("vss", 1, role="ground", supply_domain="vss")]
    _write(contract, body)
    bound = dc.bind(contract)
    dc.check_terminals(bound, require_equality=True)             # identity resolves
    with pytest.raises(dc.DieContractError, match="occurrence order"):
        dc.check_terminals(bound, require_unique_names=True)     # the profile refuses
    with pytest.raises(dc.DieContractError, match="undecidable"):
        dc.resolve_endpoint(bound, "vss")
    assert dc.resolve_endpoint(bound, "d2d_fclk")["name"] == "d2d_fclk"


def test_a_pin_list_pad_without_an_index_is_refused(tree):
    _, contract, pins, _, _ = tree
    doc = json.loads(pins.read_text())
    del doc["pins"][0]["pad_index"]
    _write(pins, doc)
    _rebind_inventory(contract, pins)
    with pytest.raises(dc.DieContractError, match="pad_index"):
        dc.bind(contract).identities()


def test_a_repeated_name_in_a_padmap_is_refused_and_distinct_names_resolve():
    repeated = {"die": {"bbox_um": list(BOX_UM)},
                "pads": [{"name": "gnd", "x_um": 1, "y_um": 1},
                         {"name": "gnd", "x_um": 9, "y_um": 1}]}
    with pytest.raises(dc.DieContractError, match="undecidable"):
        dc.inventory_identities(repeated, "blackbox_padmap")
    distinct = copy.deepcopy(repeated)
    distinct["pads"][1]["name"] = "gnd_1"
    assert set(dc.inventory_identities(distinct, "blackbox_padmap")) == {
        ("gnd", 0), ("gnd_1", 0)}


# --------------------------------------------------------------------------
# geometry sanity and the strict profile
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field,value,match", [
    ("center_x_dbu", None, "not a finite number"),
    ("center_x_dbu", "left", "not a finite number"),
    ("width_dbu", 0, "non-positive extent"),
    ("width_dbu", -5, "non-positive extent"),
])
def test_a_coordinate_or_extent_that_is_not_one_is_refused(tree, field, value, match):
    _, contract, pins, _, _ = tree
    doc = json.loads(pins.read_text())
    doc["pins"][0][field] = value
    _write(pins, doc)
    _rebind_inventory(contract, pins)
    with pytest.raises(dc.DieContractError, match=match):
        dc.check_terminals(dc.bind(contract))
    doc["pins"][0][field] = 40000                                # the control
    _write(pins, doc)
    _rebind_inventory(contract, pins)
    dc.check_terminals(dc.bind(contract))


def test_a_center_without_a_size_is_refused_under_strict_and_passes_with_one(tree):
    _, contract, pins, _, _ = tree
    doc = json.loads(pins.read_text())
    for pad in doc["pins"]:
        pad.pop("width_dbu"), pad.pop("height_dbu")
    _write(pins, doc)
    _rebind_inventory(contract, pins)
    with pytest.raises(dc.DieContractError, match="center but no size"):
        dc.check_terminals(dc.bind(contract), require_size=True)
    for pad in doc["pins"]:
        pad["width_dbu"] = pad["height_dbu"] = 40000
    _write(pins, doc)
    _rebind_inventory(contract, pins)
    dc.check_terminals(dc.bind(contract), require_size=True)


def test_an_absent_opening_is_refused_under_strict_and_a_present_one_passes(tree):
    """The schema leaves opening optional; strict admission does not."""
    _, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    body["terminals"][0].pop("opening")
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="opening layer and shape"):
        dc.check_terminals(dc.bind(contract), require_opening=True)
    body["terminals"][0]["opening"] = _opening()
    _write(contract, body)
    dc.check_terminals(dc.bind(contract), require_opening=True)


@pytest.mark.parametrize("mutate", [
    lambda t: t["opening"].update(purpose="whatever"),
    lambda t: t["opening"].update(gds_layer="Passiv"),
    lambda t: t["opening"].pop("shape"),
    lambda t: t["opening"].update(shape="hexagon"),
])
def test_an_opening_the_schema_rejects_never_reaches_admission(tree, mutate):
    """Refused while loading, so no later check has to be the one that catches it."""
    _, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    mutate(body["terminals"][0])
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="shipped schema"):
        dc.bind(contract)
    body["terminals"][0]["opening"] = _opening()
    _write(contract, body)
    assert dc.bind(contract).kind == "pins_json"


@pytest.mark.parametrize("shape", ["hexagon", "RECTANGLE", 1, {"kind": "octagon"}])
def test_the_terminal_guard_still_refuses_a_shape_that_never_saw_the_schema(tree, shape):
    """Defence in depth for a caller that hands over a dict instead of a document.

    The schema guards documents. A consumer that assembles a contract in memory
    and calls check_terminals directly never passes one, and truthiness was the
    only test standing between it and a shape nothing can draw.
    """
    _, contract, pins, _, _ = tree
    body = json.loads(contract.read_text())
    body["terminals"][0]["opening"]["shape"] = shape
    bound = dc.BoundContract(body, contract, json.loads(pins.read_text()), pins)
    with pytest.raises(dc.DieContractError, match="shape"):
        dc.check_terminals(bound, require_opening=True)
    body["terminals"][0]["opening"]["shape"] = "octagon"
    assert dc.check_terminals(
        dc.BoundContract(body, contract, json.loads(pins.read_text()), pins),
        require_opening=True) is None


# --------------------------------------------------------------------------
# attestation: a record, not an authorization, and three outcomes reachable
# --------------------------------------------------------------------------

@pytest.fixture
def attested(tree):
    """A fully bound entry over a real evidence file, and nothing more.

    Fully bound is deliberately NOT enough on its own: every check up to the
    evaluator is the entry talking about itself.
    """
    tmp, contract, _, layout, _ = tree
    report = tmp / "drc.rdb"
    report.write_bytes(b"<report-database/>")
    entry = {"check": "drc", "result": "pass", "execution": "completed",
             "tool": "klayout 0.30.5", "layout_sha256": _sha(layout),
             "deck": "sg13g2_maximal", "deck_sha256": "b" * 64,
             "evidence": report.name, "evidence_sha256": _sha(report)}
    return tmp, entry, report, _sha(layout)


def _evaluator(verdict, *, calls=None):
    """A stand-in for a qualified, tool-specific reader of THIS evidence."""
    def read(path, entry):
        if calls is not None:
            calls.append(path)
        return verdict
    return read


def test_a_fully_bound_entry_with_no_evaluator_is_unresolved(attested):
    """The default is not knowing, and a well-formed record does not change it."""
    tmp, entry, _, layout_sha = attested
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha) == "unresolved"


def test_a_fully_bound_pass_a_qualified_evaluator_confirms_is_a_pass(attested):
    tmp, entry, _, layout_sha = attested
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("pass")) == "pass"


def test_a_fully_bound_fail_is_a_fail_so_the_answer_is_not_stuck(attested):
    tmp, entry, _, layout_sha = attested
    entry["result"] = "fail"
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("fail")) == "fail"


def test_an_entry_the_evidence_contradicts_is_unresolved_not_a_pass(attested):
    """Record says pass, the qualified reader of the evidence says fail."""
    tmp, entry, _, layout_sha = attested
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("fail")) == "unresolved"
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("pass")) == "pass"


@pytest.mark.parametrize("verdict", [None, "", "maybe", True, "unresolved"])
def test_an_evaluator_that_cannot_say_leaves_it_unresolved(attested, verdict):
    tmp, entry, _, layout_sha = attested
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator(verdict)) == "unresolved"
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("pass")) == "pass"


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(execution="not_run"),
    lambda e: e.update(execution="interrupted"),
    lambda e: e.update(deck=""),
    lambda e: e.update(deck_sha256=""),
    lambda e: e.update(layout_sha256="c" * 64),
])
def test_a_pass_that_is_not_fully_bound_is_unresolved(attested, mutate):
    """Each mutation is refused with a CONFIRMING evaluator supplied, so the
    refusal is attributable to the binding rather than to the default."""
    tmp, entry, _, layout_sha = attested
    calls = []
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("pass")) == "pass"
    mutate(entry)
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("pass", calls=calls)) == "unresolved"
    assert calls == [], "an unbound entry must not reach the evaluator at all"


def test_a_pass_whose_evidence_is_gone_is_unresolved_not_a_pass(attested):
    tmp, entry, report, layout_sha = attested
    calls = []
    report.unlink()
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("pass", calls=calls)) == "unresolved"
    assert calls == []


def test_a_pass_whose_evidence_changed_is_unresolved_not_a_pass(attested):
    tmp, entry, report, layout_sha = attested
    calls = []
    report.write_bytes(b"<report-database>edited</report-database>")
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("pass", calls=calls)) == "unresolved"
    assert calls == []
    entry["evidence_sha256"] = _sha(report)
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("pass")) == "pass"


def test_an_evidence_path_that_leaves_the_tree_is_refused_not_unresolved(attested):
    """Malformed and unavailable are different facts, so they get different answers."""
    tmp, entry, _, layout_sha = attested
    for escaping in ("/etc/hostname", "../outside.json"):
        entry["evidence"] = escaping
        with pytest.raises(dc.DieContractError, match="attestation evidence"):
            dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                 evaluator=_evaluator("pass"))


def test_unresolved_is_distinct_from_fail_on_the_same_entry(attested):
    tmp, entry, report, layout_sha = attested
    entry["result"] = "fail"
    resolvable = dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                      evaluator=_evaluator("fail"))
    report.unlink()
    unresolvable = dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                        evaluator=_evaluator("fail"))
    assert (resolvable, unresolvable) == ("fail", "unresolved")


# --------------------------------------------------------------------------
# admission
# --------------------------------------------------------------------------

def _component(contract, art, **kw):
    body = {"id": "driver", "type": "chiplet", "layout": art.name, "top_cell": "DRIVER",
            "die_contract": {"path": contract.name, "sha256": _sha(contract)}}
    body.update(kw)
    return body


def test_strict_admission_refuses_a_component_with_no_contract_and_admits_one_with(tree):
    tmp, contract, _, layout, _ = tree
    bare = _component(contract, layout)
    del bare["die_contract"]
    with pytest.raises(dc.DieContractError, match="declares none"):
        dc.admit_component(bare, base_dir=tmp)
    assert dc.admit_component(_component(contract, layout), base_dir=tmp).kind == "pins_json"


def test_a_stale_reference_digest_is_refused_and_the_refreshed_one_admits(tree):
    tmp, contract, _, layout, _ = tree
    stale = _component(contract, layout)
    stale["die_contract"]["sha256"] = "0" * 64
    with pytest.raises(dc.DieContractError, match="die_contract"):
        dc.admit_component(stale, base_dir=tmp)
    stale["die_contract"]["sha256"] = _sha(contract)
    assert dc.admit_component(stale, base_dir=tmp)


def test_a_valid_contract_for_different_artwork_is_refused(tree):
    """The gap a self-consistent contract cannot close on its own."""
    tmp, contract, _, layout, _ = tree
    other = tmp / "someone_elses.gds"
    other.write_bytes(b"a different die entirely, and internally beyond reproach")
    wrong = _component(contract, layout, layout=other.name)
    with pytest.raises(dc.DieContractError, match="different artwork"):
        dc.admit_component(wrong, base_dir=tmp)
    assert dc.admit_component(_component(contract, layout), base_dir=tmp)


def test_a_component_placing_another_cell_of_the_same_layout_is_refused(tree):
    tmp, contract, _, layout, _ = tree
    wrong = _component(contract, layout, top_cell="DRIVER_TESTBENCH")
    with pytest.raises(dc.DieContractError, match="top cell"):
        dc.admit_component(wrong, base_dir=tmp)
    assert dc.admit_component(_component(contract, layout), base_dir=tmp)


@pytest.mark.parametrize("drop", ["layout", "top_cell"])
def test_strict_admission_requires_the_component_to_state_its_own_artwork(tree, drop):
    tmp, contract, _, layout, _ = tree
    bare = _component(contract, layout)
    del bare[drop]
    with pytest.raises(dc.DieContractError, match=drop.replace("_", "[ _]")):
        dc.admit_component(bare, base_dir=tmp)
    assert dc.admit_component(_component(contract, layout), base_dir=tmp)


def test_a_sidecar_in_a_nested_bundle_resolves_against_itself(tree):
    """A bundle that moves as a unit keeps working; that is what makes it a bundle."""
    tmp, contract, pins, layout, build = tree
    bundle = tmp / "dies" / "driver"
    bundle.mkdir(parents=True)
    for f in (contract, pins, layout, build):
        f.rename(bundle / f.name)
    moved = bundle / contract.name
    component = {"id": "driver", "type": "chiplet",
                 "layout": f"dies/driver/{layout.name}", "top_cell": "DRIVER",
                 "die_contract": {"path": f"dies/driver/{moved.name}",
                                  "sha256": _sha(moved)}}
    assert dc.admit_component(component, base_dir=tmp).kind == "pins_json"


def test_an_endpoint_naming_a_terminal_that_does_not_exist_is_refused(tree):
    _, contract, _, _, _ = tree
    bound = dc.bind(contract)
    with pytest.raises(dc.DieContractError, match="does not declare"):
        dc.resolve_endpoint(bound, "vdd")
    assert dc.resolve_endpoint(bound, "vss_0")["role"] == "ground"


# --------------------------------------------------------------------------
# the fixture-versus-format gap, closed with the format as the authority
# --------------------------------------------------------------------------

def test_the_inventory_box_is_the_object_pins_json_declares_not_four_numbers(tree):
    """The defect this section exists for: fixture and code agreed, the format did not."""
    tmp, contract, pins, _, _ = tree
    positional = _pins(tmp, json.loads(pins.read_text())["pins"],
                       box=[0, 0, 500000, 400000])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(json.loads(positional.read_text()), _schema("pins.schema.json"))
    _rebind_inventory(contract, positional)
    with pytest.raises(dc.DieContractError, match="object"):
        dc.bind(contract)


def test_a_box_missing_a_corner_is_refused_and_the_whole_one_binds(tree):
    tmp, contract, pins, _, _ = tree
    pads = json.loads(pins.read_text())["pins"]
    partial = dict(BOX_DBU)
    del partial["y_max"]
    _rebind_inventory(contract, _pins(tmp, pads, box=partial))
    with pytest.raises(dc.DieContractError, match="y_max"):
        dc.bind(contract)
    _rebind_inventory(contract, _pins(tmp, pads))
    assert dc.bind(contract).kind == "pins_json"


def test_the_box_corners_are_read_by_name_not_by_position(tree):
    """Key order is not a contract; reading in file order would pass this silently."""
    tmp, contract, pins, _, _ = tree
    pads = json.loads(pins.read_text())["pins"]
    shuffled = {"y_max": 400000, "x_min": 0, "x_max": 500000, "y_min": 0}
    _rebind_inventory(contract, _pins(tmp, pads, box=shuffled))
    assert dc.bind(contract).kind == "pins_json"


def test_the_shipped_vehicle_inventory_seam_binds_with_placeholder_artwork(tmp_path):
    """The real INVENTORY seam, and deliberately nothing more.

    The pin list is the shipped one; the layout and the build record are
    placeholder bytes. So this exercises the real producer's inventory shape
    against the binder and is NOT a positive result about a built die: no
    artwork was made, nothing was run over it, and nothing here says the
    vehicle would admit."""
    plans = ROOT.parent / "chiplet-flow" / "vehicle" / "plans"
    source = plans / "driver.pins.json"
    if not source.is_file():
        pytest.skip("chiplet-flow vehicle plans not present beside this checkout")
    real = json.loads(source.read_text())
    jsonschema.validate(real, _schema("pins.schema.json"))
    box = real["die_bbox_dbu"]
    dbu = real["dbu_um"]
    layout = tmp_path / "driver.gds"
    layout.write_bytes(b"stand-in bytes for the vehicle layout")
    build = _write(tmp_path / "build_record.json", {"template": "chiplet-full"})
    pins = _write(tmp_path / "pins.json", real)
    terminals = [_terminal(pad["name"], pad["pad_index"]) for pad in real["pins"]]
    contract = _contract(tmp_path, pins_path=pins, layout=layout, build=build,
                         terminals=terminals)
    body = json.loads(contract.read_text())
    body["die"]["bbox_um"] = [box["x_min"] * dbu, box["y_min"] * dbu,
                              box["x_max"] * dbu, box["y_max"] * dbu]
    _write(contract, body)
    assert dc.bind(contract).kind == "pins_json"


# --------------------------------------------------------------------------
# prerequisites the schema states and the loader now actually checks
# --------------------------------------------------------------------------

def test_a_contract_that_names_no_process_is_refused(tree):
    tmp, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    del body["process"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, _schema("die_contract.schema.json"))
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="process"):
        dc.load_die_contract(contract)


@pytest.mark.parametrize("process", [
    {"pdk": "ihp-sg13g2"},
    {"pdk_version": "dev-0de0a17"},
    {"pdk": "", "pdk_version": "dev-0de0a17"},
])
def test_a_half_stated_process_is_refused_and_the_stated_one_loads(tree, process):
    tmp, contract, _, _, _ = tree
    body = json.loads(contract.read_text())
    whole = body["process"]
    body["process"] = process
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="process"):
        dc.load_die_contract(contract)
    body["process"] = whole
    _write(contract, body)
    assert dc.load_die_contract(contract)["process"] == whole


# --------------------------------------------------------------------------
# the shipped schema, invoked rather than shipped
# --------------------------------------------------------------------------

def test_the_validator_reads_the_schema_that_ships(tree):
    """One source of truth: a copy inside the validator drifts in silence."""
    located = dc.die_contract_schema()
    shipped = json.loads((SCHEMAS / "die_contract.schema.json").read_text())
    assert located == shipped


@pytest.mark.parametrize("mutate,where", [
    (lambda b: b["die"].pop("dbu_um"), "die"),
    (lambda b: b["die"].pop("name"), "die"),
    (lambda b: b["terminals"][0].update(direction="teleport"), "terminals"),
    (lambda b: b["terminals"][0].update(role="mystery"), "terminals"),
    (lambda b: b["die"].update(frame="wafer_centre"), "die"),
    (lambda b: b["die"].update(bbox_um=[0.0, 0.0, 500.0]), "die"),
    (lambda b: b["inventory"].update(kind="whatever"), "inventory"),
    (lambda b: b.update(unexpected_key=True), "(document)"),
])
def test_a_document_the_schema_rejects_is_refused_at_load(tree, mutate, where):
    """Each of these used to admit, because the checks written by hand covered
    the fields somebody thought of and the schema covered the rest of them."""
    _, contract, _, _, _ = tree
    whole = json.loads(contract.read_text())
    body = json.loads(contract.read_text())
    mutate(body)
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="shipped schema"):
        dc.load_die_contract(contract)
    _write(contract, whole)
    assert dc.load_die_contract(contract)["schema"] == "die-output-contract"


def test_a_contract_and_an_inventory_in_different_units_are_refused(tree):
    """Semantic, so no schema can state it: both documents carry a dbu_um."""
    tmp, contract, pins, _, _ = tree
    body = json.loads(contract.read_text())
    body["die"]["dbu_um"] = 1.0
    _write(contract, body)
    with pytest.raises(dc.DieContractError, match="different units"):
        dc.bind(contract)
    body["die"]["dbu_um"] = 0.001
    _write(contract, body)
    assert dc.bind(contract).kind == "pins_json"


def test_strict_admission_refuses_rather_than_degrades_without_jsonschema(tree, monkeypatch):
    """A validator that quietly becomes weaker is worse than one that stops."""
    import builtins
    real = builtins.__import__

    def blocked(name, *args, **kw):
        if name == "jsonschema":
            raise ImportError("blocked for this test")
        return real(name, *args, **kw)

    _, contract, _, _, _ = tree
    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(dc.DieContractError, match="jsonschema is required"):
        dc.load_die_contract(contract)
    monkeypatch.undo()
    assert dc.load_die_contract(contract)["schema"] == "die-output-contract"


FOREIGN_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                  "title": "somebody else's documents", "type": "object"}


def _fake_installation(tmp, *, packaged):
    """A believable installed layout, with an unrelated schemas/ dir beside it.

    Not a mock of the loader: the real package is copied to where pip would put
    it, and the ambient directory the old fallback walked up into is filled with
    a plausible foreign schema. Mocking the loader to None asserts the assertion;
    this asks what the code does when the file is genuinely gone and something
    else that parses is sitting exactly where it used to look.
    """
    import shutil

    site = tmp / "venv" / "lib" / "python3.11" / "site-packages"
    package = site / "chiplet_format_io"
    package.parent.mkdir(parents=True)
    shutil.copytree(Path(dc.__file__).resolve().parent, package,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(package / "schemas", ignore_errors=True)
    if packaged:
        (package / "schemas").mkdir()
        shutil.copyfile(SCHEMAS / "die_contract.schema.json",
                        package / "schemas" / "die_contract.schema.json")
    ambient = site.parent.parent / "schemas"      # <venv>/lib/schemas
    ambient.mkdir()
    (ambient / "die_contract.schema.json").write_text(json.dumps(FOREIGN_SCHEMA))
    return site


def _ask_the_installation(site):
    """Run the question in a real interpreter that can only see that install."""
    import os
    import subprocess

    script = (
        "import json\n"
        "from chiplet_format_io import die_contract as dc\n"
        "answer = {'text': dc._read_packaged_schema('die_contract.schema.json')}\n"
        "try:\n"
        "    dc.die_contract_schema()\n"
        "    answer['refusal'] = None\n"
        "except dc.DieContractError as exc:\n"
        "    answer['refusal'] = str(exc)\n"
        "print(json.dumps(answer))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(site), PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-c", script], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_an_installation_without_the_schema_refuses_rather_than_admitting(tmp_path):
    """An install that lost the schema must stop, not read the neighbours."""
    answer = _ask_the_installation(_fake_installation(tmp_path, packaged=False))
    assert answer["text"] is None, (
        "the installation read a schema it does not ship; the only candidate on "
        "that disk is the unrelated document beside it")
    assert "not present in this installation" in answer["refusal"]


def test_an_installation_that_ships_the_schema_reads_its_own(tmp_path):
    """The control: same tree, same foreign neighbour, schema back in the package."""
    answer = _ask_the_installation(_fake_installation(tmp_path, packaged=True))
    assert answer["refusal"] is None
    assert json.loads(answer["text"])["title"] != FOREIGN_SCHEMA["title"]
    assert json.loads(answer["text"]) == json.loads(
        (SCHEMAS / "die_contract.schema.json").read_text())


def test_the_checkout_fallback_only_fires_inside_the_checkout(tmp_path):
    """Three parents up from an installed module is somebody else's directory."""
    assert dc._canonical_schema_dir(Path(dc.__file__).resolve().parent) == SCHEMAS

    installed = tmp_path / "venv" / "lib" / "python3.11" / "site-packages" / "chiplet_format_io"
    installed.mkdir(parents=True)
    (tmp_path / "venv" / "lib" / "schemas").mkdir()
    assert dc._canonical_schema_dir(installed) is None

    # Right directory names, no project metadata: still not this checkout.
    lookalike = tmp_path / "elsewhere" / "reference" / "python" / "chiplet_format_io"
    lookalike.mkdir(parents=True)
    (tmp_path / "elsewhere" / "schemas").mkdir()
    assert dc._canonical_schema_dir(lookalike) is None


def test_the_schema_is_reachable_as_a_package_resource(tree):
    """What the wheel will carry, read the way an installed package reads it."""
    text = dc._read_packaged_schema("die_contract.schema.json")
    assert text is not None, "the package cannot see its own schema"
    assert json.loads(text) == json.loads((SCHEMAS / "die_contract.schema.json").read_text())


# --------------------------------------------------------------------------
# a newer minor: refused element by element, never document by document
# --------------------------------------------------------------------------

def _at(contract, version, mutate=None):
    body = json.loads(Path(contract).read_text())
    body["version"] = version
    if mutate is not None:
        mutate(body)
    return _write(contract, body)


def _admit(tmp, contract, layout):
    said = []
    bound = dc.admit_component(_component(contract, layout), base_dir=tmp, on_warn=said.append)
    return bound, said


def _domains(domains, mutate=None):
    """The tree's contract at 1.2 declaring ``domains``, after ``mutate``."""
    def change(d):
        d["supply_domains"] = domains
        if mutate is not None:
            mutate(d)
    return change


def test_supply_domains_read_at_1_2_and_are_refused_below_it(tree):
    tmp, contract, _, layout, _ = tree
    _at(contract, "1.2", _domains({"vss": {"volts": 0}}))
    bound, said = _admit(tmp, contract, layout)
    assert bound.contract["supply_domains"] == {"vss": {"volts": 0}} and not said
    _at(contract, "1.1", _domains({"vss": {"volts": 0}}))
    with pytest.raises(dc.DieContractError, match="'supply_domains' was unexpected"):
        _admit(tmp, contract, layout)


def _vdd(d):
    d["terminals"][0].update(role="power", supply_domain="vdd", direction="none")


@pytest.mark.parametrize("domains,mutate,needle", [
    ({}, None, "should be non-empty|does not have enough properties"),
    ({"vss": {"volts": -1}}, None, "less than the minimum"),
    ({"vss": {"volts": 0}, "vdd": {"volts": 1.2}}, None,
     "vdd named by no power or ground terminal"),
    ({"vss": {"volts": 0}}, _vdd, "no volts for vdd"),
    ({"vss": {"volts": 0.5}}, None, "'vss' is a ground domain at 0.5 volts"),
    ({"vss": {"volts": 0}, "vdd": {"volts": 0}}, _vdd, "'vdd' is a power domain at 0 volts"),
    ({"vss": {"volts": 0}}, lambda d: d["terminals"][0].update(
        role="power", supply_domain="vss", direction="none"),
     "'vss' is named by both a power and a ground terminal"),
])
def test_supply_domains_the_terminals_contradict_refuse_the_document(tree, domains, mutate,
                                                                    needle):
    tmp, contract, _, layout, _ = tree
    _at(contract, "1.2", _domains(domains, mutate))
    with pytest.raises(dc.DieContractError, match=needle) as refused:
        _admit(tmp, contract, layout)
    assert not isinstance(refused.value, dc.DieContractIncomplete)


def test_a_newer_minor_s_key_on_a_domain_is_ignored_and_its_volts_still_read(tree):
    tmp, contract, _, layout, _ = tree
    _at(contract, "1.3", _domains({"vss": {"volts": 0, "tolerance": 0.05}}))
    bound, said = _admit(tmp, contract, layout)
    assert [u.pointer for u in bound.unrecognised] == ["supply_domains/vss/tolerance"]
    _at(contract, "1.3", _domains({"vss": {"volts": 0.5, "tolerance": 0.05}}))
    with pytest.raises(dc.DieContractError, match="a ground domain is 0"):
        _admit(tmp, contract, layout)


def test_a_domain_only_a_newer_minor_s_role_names_refuses_that_terminal_not_the_document(tree):
    """Above the floor a terminal whose role this reader does not know leaves
    the die incomplete, as it does with no supply_domains; the domain it names
    is a key no known terminal names, and refusing the document for it would
    refuse more than the terminal."""
    tmp, contract, _, layout, _ = tree

    def analog(d):
        d["terminals"][0].update(role="analog_power", supply_domain="avdd", direction="none")
    _at(contract, "1.3", _domains({"vss": {"volts": 0}, "avdd": {"volts": 3.3}}, analog))
    with pytest.raises(dc.DieContractIncomplete, match="terminals/0/role='analog_power'"):
        _admit(tmp, contract, layout)
    _at(contract, "1.3", _domains({"vss": {"volts": 0}, "vdd": {"volts": 1.2}}, _vdd))
    _admit(tmp, contract, layout)
    _at(contract, "1.3", _domains({"vss": {"volts": 0}}, _vdd))
    with pytest.raises(dc.DieContractError, match="no volts for vdd"):
        _admit(tmp, contract, layout)


@pytest.mark.parametrize("mutate,pointer", [
    (lambda d: d["terminals"][0].update(reserved_for_tests=1), "terminals/0/reserved_for_tests"),
    (lambda d: d["terminals"][0]["opening"].update(taper_um=2.0), "terminals/0/opening/taper_um"),
    (lambda d: d.update(emitted_by="chiplet-full"), "emitted_by"),
])
def test_an_unknown_key_refuses_the_document_at_the_floor_and_is_reported_above_it(tree, mutate, pointer):
    tmp, contract, _, layout, _ = tree
    _at(contract, "1.1", mutate)
    with pytest.raises(dc.DieContractError, match="shipped schema") as refused:
        _admit(tmp, contract, layout)
    assert not isinstance(refused.value, dc.DieContractIncomplete)
    _at(contract, "1.3")
    bound, said = _admit(tmp, contract, layout)
    assert [u.pointer for u in bound.unrecognised] == [pointer]
    assert any(pointer in line and "ignored" in line for line in said)
    assert pointer.rsplit("/", 1)[-1] not in json.dumps(bound.terminals)  # not readable by accident
    assert bound.contract["terminals"] == json.loads(contract.read_text())["terminals"]


@pytest.mark.parametrize("mutate,known", [
    (lambda t, v: t.update(role=v), ("test_access", "signal")),
    (lambda t, v: t.update(direction=v), ("differential", "output")),
    (lambda t, v: t["opening"].update(shape=v), ("hexagon", "circle")),
    (lambda t, v: t["opening"].update(purpose=v), ("sense", "conductor")),
])
def test_an_unknown_value_above_the_floor_is_incomplete_and_the_known_one_admits(tree, mutate, known):
    tmp, contract, _, layout, _ = tree
    unknown, control = known
    _at(contract, "1.3", lambda d: mutate(d["terminals"][0], unknown))
    with pytest.raises(dc.DieContractIncomplete) as incomplete:
        _admit(tmp, contract, layout)
    assert [u.value for u in incomplete.value.unrecognised] == [unknown]
    _at(contract, "1.3", lambda d: mutate(d["terminals"][0], control))
    assert _admit(tmp, contract, layout)[0].unrecognised == ()


@pytest.mark.parametrize("mutate", [
    lambda t: t.update(role="test_access"),
    lambda t: t.update(direction="differential"),
    lambda t: t["opening"].update(shape="hexagon"),
])
def test_an_endpoint_whose_terminal_is_refused_cannot_be_resolved(tree, mutate):
    _, contract, _, _, _ = tree
    _at(contract, "1.3", lambda d: mutate(d["terminals"][0]))
    with pytest.raises(dc.DieContractIncomplete):
        dc.resolve_endpoint(dc.bind(contract), "d2d_fclk")
    assert dc.resolve_endpoint(dc.bind(contract), "vss_0")["role"] == "ground"


def test_a_resolver_locates_what_it_could_not_read_the_way_the_load_does(tree):
    """One pointer space, so a finding reads the same wherever it came from.

    A pointer says WHERE in the document the element is, and ``terminals`` is a
    list, so the index is the locator; the pad name is not one at all on a die
    that repeats a name, which is a die this reader admits. A resolver reporting
    names would hand a consumer two spaces through one field, and nothing in the
    finding would say which it was holding.
    """
    _, contract, _, _, _ = tree
    _at(contract, "1.3", lambda d: d["terminals"][2].update(role="test_access"))
    bound = dc.bind(contract, on_warn=lambda _: None)
    name = bound.contract["terminals"][2]["name"]
    with pytest.raises(dc.DieContractIncomplete) as by_name:
        dc.resolve_endpoint(bound, name)
    assert [u.pointer for u in by_name.value.unrecognised] == ["terminals/2/role"]
    _ported(contract, version="1.3",
            mutate=lambda d: d["terminals"][2].update(role="test_access"))
    ported = dc.bind(contract, on_warn=lambda _: None)
    with pytest.raises(dc.DieContractIncomplete) as by_lane:
        dc.resolve_port(ported, "vss", 1)
    assert [u.pointer for u in by_lane.value.unrecognised] == ["terminals/2/role"]


@pytest.mark.parametrize("extra,match", [
    (_terminal("d2d_fclk", 0, role="test_access"), "declared twice"),
    (_terminal("nosuch", 0, role="test_access"), "not in the bound inventory"),
])
def test_a_refused_terminal_keeps_its_identity(tree, extra, match):
    _, contract, _, _, _ = tree
    _at(contract, "1.3", lambda d: d["terminals"].append(extra))
    with pytest.raises(dc.DieContractError, match=match) as refused:
        dc.check_terminals(dc.bind(contract))
    assert not isinstance(refused.value, dc.DieContractIncomplete)


def test_a_pad_declared_only_by_a_refused_terminal_is_incomplete_not_undeclared(tree):
    _, contract, _, _, _ = tree
    _at(contract, "1.3", lambda d: d["terminals"][2].update(role="test_access"))
    with pytest.raises(dc.DieContractIncomplete):
        dc.check_terminals(dc.bind(contract), require_equality=True)
    _at(contract, "1.3", lambda d: d["terminals"].pop())         # the control
    with pytest.raises(dc.DieContractError, match="no terminal declares"):
        dc.check_terminals(dc.bind(contract), require_equality=True)


@pytest.mark.parametrize("mutate", [
    lambda d: d["terminals"][0].update(pad_index="0"),
    lambda d: d["terminals"][0].update(role=""),
    lambda d: d["terminals"][0].update(role=5),
    lambda d: d["terminals"][0].pop("name"),
    lambda d: d["terminals"][0]["opening"].update(shape=""),
])
def test_bytes_with_no_conforming_reading_are_still_refused_above_the_floor(tree, mutate):
    _, contract, _, _, _ = tree
    _at(contract, "1.3", mutate)
    with pytest.raises(dc.DieContractError) as refused:
        dc.load_die_contract(contract, on_warn=lambda _: None)
    assert not isinstance(refused.value, dc.DieContractIncomplete)


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(result="not_applicable"),
    lambda e: e.update(check="lvs_top_only"),
    lambda e: e.update(execution="partial"),
    lambda e: e.update(scope="top_cell_only"),
])
def test_an_attestation_entry_a_newer_minor_extends_is_unresolved_unevaluated(attested, mutate):
    tmp, entry, _, layout_sha = attested
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha,
                                evaluator=_evaluator("pass")) == "pass"
    mutate(entry)
    calls = []
    assert dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha, version="1.3",
                                evaluator=_evaluator("pass", calls=calls)) == "unresolved"
    assert calls == []


def test_an_unknown_attestation_result_is_malformed_below_a_newer_minor(attested):
    tmp, entry, _, layout_sha = attested
    entry["result"] = "not_applicable"
    for version in (None, "1.1"):
        with pytest.raises(dc.DieContractError, match="not one of pass, fail, unresolved"):
            dc.attestation_state(entry, base_dir=tmp, layout_sha256=layout_sha, version=version)
    assert dc.attestation_state(entry, base_dir=tmp, version="1.3") == "unresolved"


def test_an_unknown_inventory_kind_or_frame_is_incomplete_only_after_the_digests(tree):
    _, contract, pins, layout, _ = tree
    for mutate in (lambda d: d["inventory"].update(kind="gds_labels"),
                   lambda d: d["die"].update(frame="die_center")):
        _at(contract, "1.3", mutate)
        with pytest.raises(dc.DieContractIncomplete):
            dc.bind(contract)
        good = layout.read_bytes()
        layout.write_bytes(b"stale artwork")
        with pytest.raises(dc.DieContractError, match="die.layout") as refused:
            dc.bind(contract)
        assert not isinstance(refused.value, dc.DieContractIncomplete)
        layout.write_bytes(good)
        _at(contract, "1.3", lambda d: (d["inventory"].update(kind="pins_json"),
                                        d["die"].update(frame="die_local_gds_origin")))
        assert dc.bind(contract).unrecognised == ()


def test_every_closed_node_of_the_shipped_schema_has_one_disposition():
    schema = dc.die_contract_schema()
    nodes = dc.closed_nodes(schema)
    assert len(nodes) == len(set(nodes)) == 19
    assert set(nodes) == set(dc.DISPOSITIONS)
    # Classification reads an error at the closed node itself, so none may sit
    # under a combinator list or close by unevaluatedProperties.
    assert not [n for n in nodes if n[1] == "unevaluatedProperties"
                or any(s.isdigit() for s in n[0])]
    # Only the discriminator stays closed when reading a newer minor.
    assert dc.closed_nodes(dc._reader_schema()) == [(("properties", "schema"), "const")]
    # The walk sees a closed node wherever the schema can put one.
    hidden = {"properties": {"a": {"anyOf": [{"enum": ["x"]}, {"type": "null"}]},
                             "description": {"type": "object", "additionalProperties": False}},
              "unevaluatedProperties": False}
    assert sorted(dc.closed_nodes(hidden)) == [
        ((), "unevaluatedProperties"),
        (("properties", "a", "anyOf", "0"), "enum"),
        (("properties", "description"), "additionalProperties")]
    props = schema["properties"]
    term = props["terminals"]["items"]["properties"]
    assert dc.TERMINAL_ROLES == tuple(term["role"]["enum"])
    assert dc.TERMINAL_DIRECTIONS == tuple(term["direction"]["enum"])
    assert dc.OPENING_PURPOSES == tuple(term["opening"]["properties"]["purpose"]["enum"])
    assert dc.OPENING_SHAPES == tuple(term["opening"]["properties"]["shape"]["enum"])
    assert dc.INVENTORY_KINDS == tuple(props["inventory"]["properties"]["kind"]["enum"])
    assert dc.DIE_FRAME == props["die"]["properties"]["frame"]["const"]


def test_a_closed_node_with_no_disposition_is_read_as_incomplete(tree, monkeypatch):
    """A schema that grew a vocabulary nobody decided on: stop, do not admit."""
    _, contract, _, _, _ = tree
    grown = copy.deepcopy(dc.die_contract_schema())
    grown["properties"]["process"]["properties"]["pdk"]["enum"] = ["gf180mcu"]
    # No hand reset of the derived caches: replacing the schema invalidates them
    # by identity, and a test that had to remember is a trap for the next one.
    monkeypatch.setattr(dc, "_SCHEMA_CACHE", grown)
    _at(contract, "1.3")
    with pytest.raises(dc.DieContractIncomplete, match="no disposition"):
        dc.load_die_contract(contract, on_warn=lambda _: None)


def _refusal(name, tmp, body, pins):
    """Apply one refusal that an unread element must not hide; return the component overrides."""
    if name == "artwork":
        (tmp / "other.gds").write_bytes(b"another die's artwork")
        return {"layout": "other.gds"}
    if name == "top cell":
        return {"top_cell": "OTHER"}
    if name == "units":
        body["die"]["dbu_um"] = 1.0
    elif name == "declared twice":
        body["terminals"].append(_terminal("vss_1", 0, role="ground"))
    elif name == "repeated names":
        pins["pins"].append(_pad("vss_1", 1))
        body["terminals"].append(_terminal("vss_1", 1, role="ground"))
    elif name == "undeclared":
        pins["pins"].append(_pad("spare", 0))
    elif name == "size":
        del pins["pins"][2]["width_dbu"]
    elif name == "opening":
        body["terminals"][-1].pop("opening")
    return {}


UNREAD = {
    "role": lambda d: d["terminals"][-1].update(role="test_access"),
    "direction": lambda d: d["terminals"][-1].update(direction="differential"),
    "frame": lambda d: d["die"].update(frame="die_center"),
    "kind": lambda d: d["inventory"].update(kind="gds_labels"),
}
NEEDS_INVENTORY = ("units", "undeclared", "size")


@pytest.mark.parametrize("unread,refusal", [
    (u, r) for u in UNREAD
    for r in (None, "artwork", "top cell", "units", "declared twice", "repeated names",
              "undeclared", "size", "opening")
    if not (u == "kind" and r in NEEDS_INVENTORY)])
def test_a_refusal_wins_over_anything_unread(tree, unread, refusal):
    """The same die with the refusal left out is the control: incomplete, not admitted."""
    tmp, contract, pins, layout, _ = tree
    body, inv = json.loads(contract.read_text()), json.loads(pins.read_text())
    overrides = _refusal(refusal, tmp, body, inv) if refusal else {}
    _write(pins, inv)
    body["inventory"]["sha256"] = _sha(pins)
    UNREAD[unread](body)
    body["version"] = "1.3"
    _write(contract, body)
    component = _component(contract, layout, **overrides)
    with pytest.raises(dc.DieContractError) as raised:
        dc.admit_component(component, base_dir=tmp, on_warn=lambda _: None)
    assert isinstance(raised.value, dc.DieContractIncomplete) == (refusal is None), raised.value


# --------------------------------------------------------------------------
# 1.1: the port identity, and resolving a lane through it
# --------------------------------------------------------------------------

PORTS = (("d2d_fclk", 0), ("vss", 0), ("vss", 1))


def _ported(contract, *, version="1.1", ports=PORTS, mutate=None):
    """The tree's die at ``version``, with a port identity on every terminal."""
    body = json.loads(Path(contract).read_text())
    body["version"] = version
    for term, (port, ordinal) in zip(body["terminals"], ports):
        term["port"], term["ordinal"] = port, ordinal
    if mutate is not None:
        mutate(body)
    return _write(contract, body)


def _bound(tmp, contract, layout):
    return dc.admit_component(_component(contract, layout), base_dir=tmp,
                              on_warn=lambda _: None)


def test_a_port_identity_below_its_minor_is_out_of_contract(tree):
    """The key exists at 1.1; a 1.0 document carrying it has no conforming reading."""
    tmp, contract, _, layout, _ = tree
    _ported(contract, version="1.0")
    with pytest.raises(dc.DieContractError, match="shipped schema") as refused:
        _bound(tmp, contract, layout)
    assert not isinstance(refused.value, dc.DieContractIncomplete)
    _ported(contract)
    assert dc.resolve_port(_bound(tmp, contract, layout), "vss", 1)["name"] == "vss_1"


@pytest.mark.parametrize("drop", ["ordinal", "port"])
def test_half_a_port_identity_is_refused_and_the_pair_admits(tree, drop):
    tmp, contract, _, layout, _ = tree
    _ported(contract, mutate=lambda d: d["terminals"][0].pop(drop))
    with pytest.raises(dc.DieContractError, match="shipped schema"):
        _bound(tmp, contract, layout)
    _ported(contract)
    assert _bound(tmp, contract, layout).kind == "pins_json"


def test_a_contract_with_no_port_identity_says_so_rather_than_resolving(tree):
    tmp, contract, _, layout, _ = tree
    _at(contract, "1.1")
    with pytest.raises(dc.DieContractError, match="carries no port identity"):
        dc.resolve_port(_bound(tmp, contract, layout), "vss", 0)


@pytest.mark.parametrize("port,ordinal,message", [
    ("clk", 0, "does not declare the port"),
    ("vss", 2, "lane"),
    ("", 0, "a port name is required"),
    # A bool is an int in Python and JSON true would resolve lane 1; a negative
    # ordinal would index the bus from the end. Both name a pad nobody asked for.
    ("vss", True, "non-negative integer"),
    ("vss", -1, "non-negative integer"),
])
def test_resolve_port_refuses_what_it_cannot_resolve(tree, port, ordinal, message):
    """The control is the line above: every declared lane resolves on this die."""
    tmp, contract, _, layout, _ = tree
    bound = _bound(tmp, contract, _ported(contract) and layout)
    assert [dc.resolve_port(bound, "vss", i)["name"] for i in (0, 1)] == ["vss_0", "vss_1"]
    with pytest.raises(dc.DieContractError, match=message):
        dc.resolve_port(bound, port, ordinal)


def test_a_bus_with_a_hole_is_refused_and_the_contiguous_one_reads(tree):
    tmp, contract, _, layout, _ = tree
    for ports in ((("d2d_fclk", 0), ("vss", 0), ("vss", 2)),     # a gap
                  (("d2d_fclk", 0), ("vss", 1), ("vss", 2)),     # not zero-based
                  (("d2d_fclk", 0), ("vss", 0), ("vss", 0))):    # repeated
        _ported(contract, ports=ports)
        with pytest.raises(dc.DieContractError, match="ordinals"):
            dc.port_terminals(_bound(tmp, contract, layout), "vss")
    _ported(contract)
    lanes = dc.port_terminals(_bound(tmp, contract, layout), "vss")
    assert [t["ordinal"] for t in lanes] == [0, 1]


def test_require_role_gives_three_answers_on_one_terminal(tree):
    tmp, contract, _, layout, _ = tree
    bound = _bound(tmp, contract, _ported(contract) and layout)
    assert dc.resolve_port(bound, "vss", 0, require_role="ground")["name"] == "vss_0"
    assert dc.resolve_port(bound, "vss", 0)["role"] == "ground"
    with pytest.raises(dc.DieContractError, match="requires 'signal'"):
        dc.resolve_port(bound, "vss", 0, require_role="signal")
    with pytest.raises(dc.DieContractError, match="is not one of"):
        dc.resolve_port(bound, "vss", 0, require_role="analogue")


def test_a_role_requirement_waits_for_the_role_and_for_nothing_else(tree):
    """It waits for the one value it is tested against, not for any unread one.

    A refusal that holds under every reading of the unread element may not wait
    behind it: the role here is readable and is not the one the caller asked
    for, whatever a later minor means by the direction beside it.
    """
    tmp, contract, _, layout, _ = tree
    _ported(contract, version="1.3",
            mutate=lambda d: d["terminals"][2].update(direction="diagonal"))
    bound = dc.bind(contract, on_warn=lambda _: None)
    with pytest.raises(dc.DieContractError, match="requires 'power'") as refused:
        dc.resolve_port(bound, "vss", 1, require_role="power")
    assert not isinstance(refused.value, dc.DieContractIncomplete)
    # And with no role asked for, the same lane is still undecided.
    with pytest.raises(dc.DieContractIncomplete):
        dc.resolve_port(bound, "vss", 1)


def test_a_repeated_pad_name_is_not_resolved_by_port_either(tmp_path):
    """What leaves the reader is a bare name the bump consumer takes by occurrence."""
    layout = tmp_path / "driver.gds"
    layout.write_bytes(b"not a real gds, but real bytes")
    build = _write(tmp_path / "build_record.json", {"template": "chiplet-full"})
    for name, resolves in (("vss", False), ("vss_1", True)):
        pins = _pins(tmp_path, [_pad("vss", 0), _pad(name, 1)])
        contract = _contract(tmp_path, pins_path=pins, layout=layout, build=build,
                             terminals=[_terminal("vss", 0, role="ground"),
                                        _terminal(name, 1, role="ground")])
        _ported(contract, ports=(("vss", 0), ("vss", 1)))
        # bind, not admit: the strict profile refuses a repeated name at
        # admission, and this is about what resolve_port does on its own.
        bound = dc.bind(contract, on_warn=lambda _: None)
        if resolves:
            assert dc.resolve_port(bound, "vss", 1)["name"] == name
        else:
            with pytest.raises(dc.DieContractError, match="occurrence order"):
                dc.resolve_port(bound, "vss", 1)


def _strip_the_port_identity(body):
    for key in ("port", "ordinal"):
        body["terminals"][0].pop(key)


@pytest.mark.parametrize("mutate,message", [
    (_strip_the_port_identity, "carries no port identity"),
    (lambda d: d["terminals"][2].update(ordinal=0), "twice"),
    (lambda d: d["terminals"][2].update(ordinal=3), "0..n-1"),
    (lambda d: d["terminals"][1].update(port="d2d_fclk", ordinal=1), "signal port"),
])
def test_the_strict_port_profile_refuses_the_whole_die_at_once(tree, mutate, message):
    """resolve_port meets these one lane at a time; a producer wants them together."""
    tmp, contract, _, layout, _ = tree
    _ported(contract)
    dc.check_terminals(_bound(tmp, contract, layout), require_port_identity=True)  # the control
    _ported(contract, mutate=mutate)
    with pytest.raises(dc.DieContractError, match=message) as refused:
        dc.check_terminals(_bound(tmp, contract, layout), require_port_identity=True)
    assert not isinstance(refused.value, dc.DieContractIncomplete)


def test_a_port_refusal_wins_over_a_role_this_reader_cannot_read(tree):
    """A refusal may not rest on an unread element, and may not wait behind one.

    Both readings of the unread role give the same verdict, which is what makes
    this a refusal rather than an incompleteness: the two facts it rests on, how
    many lanes the port declares and that one of them IS a signal, are readable
    either way. The second half of the test is that control.
    """
    tmp, contract, _, layout, _ = tree
    for role in ("test_access", "ground"):
        _ported(contract, version="1.3",
                mutate=lambda d, role=role: (d["terminals"][1].update(port="d2d_fclk", ordinal=1),
                                             d["terminals"][2].update(role=role)))
        bound = dc.bind(contract, on_warn=lambda _: None)
        with pytest.raises(dc.DieContractError, match="signal port") as refused:
            dc.check_terminals(bound, require_port_identity=True)
        assert not isinstance(refused.value, dc.DieContractIncomplete)


def test_a_lane_whose_role_this_reader_cannot_read_is_incomplete_not_refused(tree):
    tmp, contract, _, layout, _ = tree
    _ported(contract, version="1.3",
            mutate=lambda d: d["terminals"][2].update(role="test_access"))
    bound = dc.bind(contract, on_warn=lambda _: None)
    assert dc.resolve_port(bound, "vss", 0)["name"] == "vss_0"
    with pytest.raises(dc.DieContractIncomplete) as incomplete:
        dc.resolve_port(bound, "vss", 1)
    assert [u.value for u in incomplete.value.unrecognised] == ["test_access"]
    # And asking for a role does not turn "cannot tell" into "the contract said
    # no": the reader has no way to know whether a later minor's role is the one
    # this caller wanted, so the answer is the same one, not a refusal.
    with pytest.raises(dc.DieContractIncomplete):
        dc.resolve_port(bound, "vss", 1, require_role="ground")


def test_half_an_identity_above_the_floor_reads_the_die_and_not_the_port(tree):
    """A pairing only this minor requires may not refuse a newer minor's document.

    At 1.1 the schema pairs the two keys and the test above pins that. Above the
    floor the pairing is one a later minor may have relaxed, so the bytes have a
    conforming reading: the terminal reads, and what its port identity means is
    what this reader cannot read. A port with such a terminal is undecided,
    never a shorter bus; a terminal that does not even name a port leaves every
    port undecided, because it could be a lane of any of them.
    """
    tmp, contract, _, layout, _ = tree
    _ported(contract, version="1.3", mutate=lambda d: d["terminals"][2].pop("ordinal"))
    bound = dc.bind(contract, on_warn=lambda _: None)
    assert dc.resolve_port(bound, "d2d_fclk", 0)["name"] == "d2d_fclk"   # a port it can read
    for call in (lambda: dc.port_terminals(bound, "vss"),
                 lambda: dc.resolve_port(bound, "vss", 0),
                 lambda: dc.check_terminals(bound, require_port_identity=True)):
        with pytest.raises(dc.DieContractIncomplete) as undecided:
            call()
        # terminals[2] is the one the fixture blinded, and a pointer locates the
        # element: terminals is a list, so the index is the locator and the pad
        # name, which a die may repeat, is not.
        assert [u.pointer for u in undecided.value.unrecognised] == ["terminals/2/ordinal"]
    _ported(contract, version="1.3", mutate=lambda d: d["terminals"][2].pop("port"))
    with pytest.raises(dc.DieContractIncomplete):
        dc.resolve_port(dc.bind(contract, on_warn=lambda _: None), "d2d_fclk", 0)


def test_a_half_identity_is_admitted_and_named_rather_than_passed_over(tree):
    """The terminal reads, so the die is admitted; the unread half is still said.

    Nothing in the schema names this above the floor any more, so without a
    finding of its own a consumer that never asks about ports would hear nothing
    about a document it cannot fully read.
    """
    tmp, contract, _, layout, _ = tree
    _ported(contract, version="1.3", mutate=lambda d: d["terminals"][2].pop("ordinal"))
    said = []
    bound = dc.admit_component(_component(contract, layout), base_dir=tmp, on_warn=said.append)
    assert [(u.pointer, u.disposition) for u in bound.unrecognised] == [
        ("terminals/2/ordinal", dc.PORT_UNREADABLE)]
    assert any("terminals/2/ordinal" in line for line in said)


def test_a_bus_refusal_is_raised_when_no_unread_lane_could_rescue_it(tree):
    """Certain, undecided and unaffected, on the one die shape that tells them apart.

    A terminal whose port identity this reader cannot read could be a lane of
    any port, and it can fill at most one hole. More holes than such terminals
    means no reading of them makes the bus contiguous, which is a refusal; one
    hole and one of them is undecided; and the signal-bus rule is never deferred
    for one, because an extra lane can only push the count further past one.
    """
    tmp, contract, _, layout, _ = tree

    def portless(body):
        body["terminals"][0].pop("port")                       # keeps ordinal 0

    _ported(contract, version="1.3", ports=(("d2d_fclk", 0), ("vss", 0), ("vss", 5)),
            mutate=portless)
    with pytest.raises(dc.DieContractError, match="0..n-1") as refused:
        dc.port_terminals(dc.bind(contract, on_warn=lambda _: None), "vss")
    assert not isinstance(refused.value, dc.DieContractIncomplete)

    _ported(contract, version="1.3", ports=(("d2d_fclk", 0), ("vss", 0), ("vss", 2)),
            mutate=portless)
    with pytest.raises(dc.DieContractIncomplete):           # one hole, one candidate
        dc.port_terminals(dc.bind(contract, on_warn=lambda _: None), "vss")

    _ported(contract, version="1.3", ports=(("d2d_fclk", 0), ("d2d_fclk", 1), ("vss", 0)),
            mutate=lambda d: d["terminals"][2].pop("port"))
    with pytest.raises(dc.DieContractError, match="signal port") as refused:
        dc.check_terminals(dc.bind(contract, on_warn=lambda _: None),
                           require_port_identity=True)
    assert not isinstance(refused.value, dc.DieContractIncomplete)


def test_the_per_minor_cache_is_bounded_by_this_reader_not_by_the_documents(tree):
    """The key comes from the document, so an unbounded dict grows with the reads."""
    _, contract, _, _, _ = tree
    body = json.loads(Path(contract).read_text())
    for minor in range(dc._AS_OF_LIMIT * 3):
        with pytest.raises(dc.DieContractError):
            dc.validate_die_contract(dict(body, version=f"0.{minor}", schema="wrong"))
    assert len(dc._AS_OF_CACHE) <= dc._AS_OF_LIMIT


def test_a_schema_derived_from_a_schema_that_changed_is_rebuilt(tree, monkeypatch):
    """A stale derivative is a wrong verdict nobody would attribute to a cache.

    Every derivative, which is why the second half primes one first: with the
    reader schema already built, a rule added to the schema it came from has to
    reach the next document read, or the verdict depends on call order alone.
    """
    _, contract, _, _, _ = tree
    assert "reserved_for_tests" not in dc._schema_as_of("1.0")["properties"]
    _at(contract, "1.3")
    dc.load_die_contract(contract, on_warn=lambda _: None)
    grown = copy.deepcopy(dc.die_contract_schema())
    grown["properties"]["reserved_for_tests"] = {"type": "string"}
    grown["properties"]["terminals"]["items"]["required"].append("supply_domain")
    monkeypatch.setattr(dc, "_SCHEMA_CACHE", grown)
    assert "reserved_for_tests" in dc._schema_as_of("1.0")["properties"]
    with pytest.raises(dc.DieContractError, match="supply_domain") as refused:
        dc.load_die_contract(contract, on_warn=lambda _: None)
    assert not isinstance(refused.value, dc.DieContractIncomplete)


def test_a_minor_is_adopted_whole_or_not_at_all():
    """What lets a recorded verdict name the capability instead of the release.

    A node introduced ABOVE the supported minor would ship inside the reader
    schema and be recognised for a document stamped at that higher minor, while
    the as-of reconstruction went on removing it for every accepted one. The
    reader would then recognise one value of a minor it has not adopted and
    refuse the rest of that minor, so a verdict on such a document could move
    from one reader release to the next with no version a consumer can see.
    Adding such a row is a deliberate act and lands here.
    """
    supported = dc._parse_contract_version(dc.SUPPORTED_DIE_CONTRACT_VERSION)
    for path, added in dc.INTRODUCED_AT.items():
        assert dc._parse_contract_version(added) <= supported, path


@pytest.mark.parametrize("minor", ["1.0", "1.1", "1.2"])
def test_the_schema_as_of_each_minor_is_the_schema_that_minor_shipped(minor):
    """Parsed JSON, never a digest: a reformat is not a change of meaning.

    A later minor that edits the schema without a row in INTRODUCED_AT lands
    here, which is what keeps the next minor cheap. One baseline per minor and
    not just the oldest, because a row removes a whole node: an edit INSIDE a
    node a later row removes is invisible to the 1.0 baseline and would reach a
    1.1 document as a rule 1.1 never had. Each new minor owes its own file here,
    and refreshing an existing one is a deliberate act, not a rebuild.
    """
    baseline = json.loads((ROOT / "conformance" / "fixtures"
                           / f"die_contract_schema_{minor.replace('.', '_')}.json").read_text())
    assert dc._schema_as_of(minor) == baseline
    assert "reserved_for_tests" not in _schema("die_contract.schema.json")[
        "properties"]["terminals"]["items"]["properties"]
