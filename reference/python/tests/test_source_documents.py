# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Source documents (format 1.1): a top-level `system` block is not an assembly.

A reader written before 1.1 would read a source as an assembly with no components
and carry on. These are the guarantees that it cannot happen through this module:
every assembly read path refuses a source whatever its other options say, and only
allow_source=True, which chiplet_system_description.source passes, reads one.
"""
import copy
import pathlib
import re
import warnings

import pytest

import chiplet_format_io as cfio

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "conformance" / "fixtures"
SOURCE = (FIXTURES / "v1_1_system_source.chiplet").read_text(encoding="utf-8")
SOURCE_1_0 = (FIXTURES / "v1_0_system_block.chiplet").read_text(encoding="utf-8")


def _source():
    return cfio.loads(SOURCE, allow_source=True)


def test_public_api_exports_the_source_names():
    for name in ("SOURCE_FORMAT_VERSION", "SOURCE_KEY_SINCE", "DERIVED_BLOCKS",
                 "SourceDocumentError", "SystemBlockError", "UNPLACED_SINCE",
                 "below_minor", "parse_format_version", "unplaced_refusal"):
        assert name in cfio.__all__
    assert issubclass(cfio.SourceDocumentError, cfio.ChipletFormatError)
    assert issubclass(cfio.SystemBlockError, cfio.ChipletFormatError)
    # The stamp reads silently: at or below the accepted floor of its major.
    events = []
    cfio.check_format_version(cfio.SOURCE_FORMAT_VERSION, on_warn=events.append)
    assert events == []


@pytest.mark.parametrize("kwargs", [
    {},
    {"allow_intermediate": True},
    {"validate": False},
    {"allow_intermediate": True, "validate": False},
])
def test_every_assembly_read_path_refuses_a_source(kwargs):
    with pytest.raises(cfio.SourceDocumentError, match="source .chiplet"):
        cfio.loads(SOURCE, **kwargs)


def test_load_refuses_a_source_too(tmp_path):
    path = tmp_path / "system.chiplet"
    path.write_text(SOURCE, encoding="utf-8")
    with pytest.raises(cfio.SourceDocumentError):
        cfio.load(path, allow_intermediate=True)
    assert cfio.load(path, allow_source=True)["system"]["dies"]


def test_validate_refuses_a_source_dict():
    with pytest.raises(cfio.SourceDocumentError):
        cfio.validate(_source(), allow_intermediate=True)


def test_the_refusal_comes_before_the_version_check():
    # A 1.0-stamped source is still named as a source on the assembly path, so
    # the message says what the file is rather than what its stamp is.
    with pytest.raises(cfio.SourceDocumentError):
        cfio.loads(SOURCE_1_0, allow_intermediate=True)


def test_allow_source_reads_the_fixture_silently():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        data = _source()
    assert data["format_version"] == "1.1"
    assert [d["id"] for d in data["system"]["dies"]] == ["U1", "U2"]


def test_a_source_below_1_1_is_refused_by_a_source_reader():
    with pytest.raises(cfio.ChipletFormatError, match="format 1.1"):
        cfio.loads(SOURCE_1_0, allow_source=True)


@pytest.mark.parametrize("block", cfio.DERIVED_BLOCKS)
def test_a_source_with_a_derived_block_is_refused(block):
    data = _source()
    data[block] = [] if block in ("technologies", "components", "interfaces") else {}
    with pytest.raises(cfio.ChipletFormatError, match=block):
        cfio.validate(data, allow_source=True)


def test_a_source_must_be_marked_unfinished():
    data = _source()
    data["_metadata"]["finalize_required"] = False
    with pytest.raises(cfio.ChipletFormatError, match="finalize_required"):
        cfio.validate(data, allow_source=True)
    del data["_metadata"]
    with pytest.raises(cfio.ChipletFormatError, match="finalize_required"):
        cfio.validate(data, allow_source=True)


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(carrier="interposer"),
    lambda s: s.update(dies=[]),
    lambda s: s.update(dies=[{"die": "dies/a"}]),
    lambda s: s.pop("carrier"),
])
def test_a_system_block_needs_a_carrier_and_identified_dies(mutate):
    data = _source()
    mutate(data["system"])
    with pytest.raises(cfio.ChipletFormatError):
        cfio.validate(data, allow_source=True)


def test_a_non_mapping_system_block_is_refused():
    data = _source()
    data["system"] = ["U1"]
    with pytest.raises(cfio.ChipletFormatError):
        cfio.validate(data, allow_source=True)


def test_dumps_writes_a_source_with_the_source_stamp():
    data = _source()
    del data["format_version"]
    out = cfio.dumps(data, validate=False)
    assert cfio.loads(out, allow_source=True)["format_version"] == "1.1"
    assert cfio.loads(out, allow_source=True)["system"] == _source()["system"]


def test_dumps_round_trips_the_fixture():
    data = _source()
    again = cfio.loads(cfio.dumps(copy.deepcopy(data)), allow_source=True)
    assert again == data


def test_an_assembly_still_gets_the_assembly_stamp():
    out = cfio.dumps({"assembly": {"name": "a"}}, validate=False)
    assert cfio.loads(out)["format_version"] == cfio.SUPPORTED_FORMAT_VERSION


def _with_volts(data):
    data["system"]["rails"][0]["volts"] = 1.2
    return data


def test_a_rail_s_volts_is_format_1_2():
    """``volts`` is the first source key after 1.1 (SOURCE_KEY_SINCE): a
    source stamped 1.1 that writes one is out of contract, and the writer
    stamps a source that writes one 1.2, and one that writes none 1.1."""
    assert cfio.SOURCE_KEY_SINCE["system.rails[].volts"] == "1.2"
    data = _with_volts(_source())
    with pytest.raises(cfio.ChipletFormatError, match=r"system\.rails\[\]\.volts is format "
                                                      r"1\.2; this source is stamped '1\.1'"):
        cfio.validate(data, allow_source=True)
    assert cfio.content_format_floor(data) == "1.2"
    out = cfio.dumps(data, validate=False)
    again = cfio.loads(out, allow_source=True)
    assert again["format_version"] == "1.2" and again["system"]["rails"][0]["volts"] == 1.2
    assert cfio.content_format_floor(_source()) is None


# --- format 1.3: the unplaced source ---------------------------------------
def _stamped(data, version):
    data["format_version"] = version
    return data


def _no_dies(data):
    del data["system"]["dies"]


def _empty_dies(data):
    data["system"]["dies"] = []


def _die_not_placed(data):
    del data["system"]["dies"][0]["at"], data["system"]["dies"][0]["rotation"]


def _no_size(data):
    del data["system"]["carrier"]["size"]


def _no_edges(data):
    del data["system"]["external"]["edges"]


def _unplaced_pins(data):
    data["unplaced"] = {"pins": ["U2.STATUS"]}
    data["system"].pop("alone")


#: Each thing a 1.3 source may leave to placement, and what the refusal names.
LEFT_OPEN = [
    (_no_dies, "a source with no dies"),
    (_empty_dies, "a source with no dies"),
    (_die_not_placed, r"system\.dies\[0\]: U1 with no at"),
    (_no_size, "a carrier with no size"),
    (_no_edges, "an external block with no edges"),
    (_unplaced_pins, "unplaced is format 1.3"),
]


@pytest.mark.parametrize("mutate, says", LEFT_OPEN)
def test_a_source_may_leave_its_placement_open_from_1_3(mutate, says):
    """What 1.3 allows, refused below it with the minor named, read silently at
    1.3, and read above it with the higher-minor warning only."""
    for version in ("1.1", "1.2"):
        data = _stamped(_source(), version)
        mutate(data)
        with pytest.raises(cfio.ChipletFormatError, match=says) as refused:
            cfio.validate(data, allow_source=True)
        assert f"this source is stamped '{version}'" in str(refused.value)
    data = _stamped(_source(), "1.3")
    mutate(data)
    events = []
    cfio.validate(data, allow_source=True, on_warn=events.append)
    assert events == []
    data = _stamped(_source(), "1.6")
    mutate(data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfio.validate(data, allow_source=True, on_warn=events.append)
    assert len(events) == 1 and "1.6" in events[0]


@pytest.mark.parametrize("mutate, says", LEFT_OPEN)
def test_the_writer_stamps_a_source_that_leaves_anything_open_1_3(mutate, says):
    data = _source()
    mutate(data)
    assert cfio.content_format_floor(data) == "1.3"
    out = cfio.dumps(data, validate=False)
    assert cfio.loads(out, allow_source=True)["format_version"] == "1.3"


def test_a_placed_source_keeps_its_stamp():
    assert cfio.SOURCE_KEY_SINCE["unplaced"] == "1.3"
    out = cfio.dumps(_source())
    assert cfio.loads(out, allow_source=True)["format_version"] == "1.1"
    assert cfio.content_format_floor({"assembly": {"name": "a"}}) is None


def test_a_rotation_without_at_is_refused_at_any_minor():
    """With that reason below 1.3 too: stamping 1.3 would not fix it."""
    for version in ("1.1", "1.2", "1.3", "1.4", "1.5", "1.6"):
        data = _stamped(_source(), version)
        del data["system"]["dies"][0]["at"]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(cfio.SystemBlockError, match="U1 has a rotation and no at"):
                cfio.validate(data, allow_source=True)


@pytest.mark.parametrize("unplaced", [
    None, [], "U2.STATUS", {}, {"pins": []}, {"pins": "U2.STATUS"}, {"pins": [""]},
    {"pins": [3]},
])
def test_unplaced_holds_a_non_empty_list_of_pins(unplaced):
    data = _stamped(_source(), "1.3")
    _unplaced_pins(data)
    data["unplaced"] = unplaced
    with pytest.raises(cfio.ChipletFormatError, match="unplaced is a mapping holding pins"):
        cfio.validate(data, allow_source=True)


def test_unplaced_needs_an_external_block():
    data = _stamped(_source(), "1.3")
    _unplaced_pins(data)
    del data["system"]["external"]
    with pytest.raises(cfio.ChipletFormatError, match="external block naming the profile"):
        cfio.validate(data, allow_source=True)


def test_dies_that_are_not_a_list_are_refused_at_any_minor():
    data = _stamped(_source(), "1.3")
    data["system"]["dies"] = {"U1": {}}
    with pytest.raises(cfio.ChipletFormatError, match="system.dies must be a list"):
        cfio.validate(data, allow_source=True)


def _finalized(data):
    data["_metadata"]["finalize_required"] = False


def _no_assembly(data):
    del data["assembly"]


def _system_scalar(data):
    data["system"] = 5


@pytest.mark.parametrize("version", ["1.1", "1.2"])
@pytest.mark.parametrize("document, says", [
    (_finalized, "finalize_required: true"),
    (_no_assembly, "'assembly' section"),
    (_system_scalar, "'system' must be a mapping"),
])
def test_the_document_level_is_refused_before_the_system_block(version, document, says):
    """A source with no dies below 1.3 and a document-level defect is refused
    for the defect, as a plain ChipletFormatError: a SystemBlockError comes
    only once the rest of the document holds."""
    data = _stamped(_source(), version)
    _no_dies(data)
    document(data)
    with pytest.raises(cfio.ChipletFormatError, match=says) as refused:
        cfio.validate(data, allow_source=True)
    assert type(refused.value) is cfio.ChipletFormatError


@pytest.mark.parametrize("kwargs", [{}, {"allow_intermediate": True}])
def test_an_assembly_never_carries_unplaced(kwargs):
    """The key is a source's: its pins go on the system's external edges."""
    text = (FIXTURES / "v1_3_unplaced_assembly.chiplet").read_text(encoding="utf-8")
    with pytest.raises(cfio.ChipletFormatError, match="belongs to a source") as refused:
        cfio.loads(text, **kwargs)
    assert type(refused.value) is cfio.ChipletFormatError
    assert cfio.loads(text, validate=False)["unplaced"] == {"pins": ["U1.CLK"]}


# --- format 1.4: a die's admission ----------------------------------------
def _admitted(data):
    data["system"]["dies"][0]["admission"] = "admission.json"
    return data


def test_a_die_s_admission_is_format_1_4():
    """A die row may name its admission file from 1.4: refused below it with
    the minor named, read silently at it, and the writer stamps a source that
    writes one 1.4."""
    assert cfio.SOURCE_KEY_SINCE["system.dies[].admission"] == "1.4"
    for version in ("1.1", "1.2", "1.3"):
        data = _admitted(_stamped(_source(), version))
        with pytest.raises(cfio.SystemBlockError,
                           match=r"system\.dies\[\]\.admission is format 1\.4; this source "
                                 f"is stamped '{version}'"):
            cfio.validate(data, allow_source=True)
    data = _admitted(_stamped(_source(), "1.4"))
    events = []
    cfio.validate(data, allow_source=True, on_warn=events.append)
    assert events == []
    data = _admitted(_source())
    assert cfio.content_format_floor(data) == "1.4"
    again = cfio.loads(cfio.dumps(data, validate=False), allow_source=True)
    assert again["format_version"] == "1.4"
    assert again["system"]["dies"][0]["admission"] == "admission.json"


@pytest.mark.parametrize("value", [
    "", 3, None, ["admission.json"], "admission.yaml", "/abs/admission.json",
    "../admission.json", "dies/../admission.json", "${HOME}/admission.json",
])
def test_an_admission_names_a_relative_json_file(value):
    data = _stamped(_source(), "1.4")
    data["system"]["dies"][0]["admission"] = value
    with pytest.raises(cfio.SystemBlockError, match="admission names a .json file"):
        cfio.validate(data, allow_source=True)


@pytest.mark.parametrize("value", [
    "admissions/U1.json", "C:/admission.json", "dies\\admission.json", "x:a.json",
])
def test_an_admission_holds_the_schema_s_path_rule_no_stricter(value):
    """A subfolder is fine; drive letters and backslashes are the schema's to
    allow and a source reader's to refuse, not this reader's."""
    data = _stamped(_source(), "1.4")
    data["system"]["dies"][0]["admission"] = value
    cfio.validate(data, allow_source=True)


# --- format 1.5: a rail's routing requirement ------------------------------
RAIL_KEYS = ("width_um", "amps", "current_density_ma_per_um")
#: The two forms a rail states its requirement in, on the power rail (index 1).
STATED = [{"width_um": 20}, {"amps": 1.5, "current_density_ma_per_um": 75}]


def _required(data, requirement, rail=1):
    data["system"]["rails"][rail].update(requirement)
    return data


@pytest.mark.parametrize("requirement", STATED, ids=["width", "current"])
def test_a_rail_s_requirement_is_format_1_5(requirement):
    """A rail may state its routing requirement from 1.5: refused below it with
    the minor named, read silently at it, and the writer stamps a source that
    states one 1.5."""
    assert {key: cfio.SOURCE_KEY_SINCE[f"system.rails[].{key}"] for key in RAIL_KEYS} == \
        dict.fromkeys(RAIL_KEYS, "1.5")
    first = next(iter(requirement))
    for version in ("1.1", "1.2", "1.3", "1.4"):
        data = _required(_stamped(_source(), version), requirement)
        with pytest.raises(cfio.SystemBlockError,
                           match=rf"system\.rails\[\]\.{first} is format 1\.5; this source "
                                 f"is stamped '{version}'"):
            cfio.validate(data, allow_source=True)
    data = _required(_stamped(_source(), "1.5"), requirement)
    events = []
    cfio.validate(data, allow_source=True, on_warn=events.append)
    assert events == []
    data = _required(_source(), requirement)
    assert cfio.content_format_floor(data) == "1.5"
    again = cfio.loads(cfio.dumps(data, validate=False), allow_source=True)
    assert again["format_version"] == "1.5"
    assert {key: again["system"]["rails"][1][key] for key in requirement} == requirement


def test_each_rail_key_floors_the_writer_at_1_5():
    """The stamp check and the writer read one walker; each key moves it."""
    for key in RAIL_KEYS:
        data = _source()
        data["system"]["rails"][1][key] = 1.0
        assert cfio.content_format_floor(data) == "1.5"


def test_a_ground_rail_may_state_a_width():
    data = _required(_stamped(_source(), "1.5"), {"width_um": 40}, rail=0)
    assert data["system"]["rails"][0]["class"] == "ground"
    cfio.validate(data, allow_source=True)


#: What a rail may not state, and what the refusal says. The value cases are
#: tried on each key, with a well-formed partner.
PAIRING = [
    ({"amps": 1.5}, "states amps without current_density_ma_per_um"),
    ({"current_density_ma_per_um": 75}, "states current_density_ma_per_um without amps"),
    ({"width_um": 20, "amps": 1.5}, "states width_um and a current"),
    ({"width_um": 20, "current_density_ma_per_um": 75}, "states width_um and a current"),
    ({"width_um": 20, "amps": 1.5, "current_density_ma_per_um": 75},
     "states width_um and a current"),
]
BAD_VALUES = [0, -1, float("inf"), float("nan"), 10 ** 400, True, "20"]
PARTNER = {"width_um": {}, "amps": {"current_density_ma_per_um": 75},
           "current_density_ma_per_um": {"amps": 1.5}}
REFUSED = PAIRING + [({key: value, **PARTNER[key]},
                      re.escape(f"{key} must be a positive, finite number, not {value!r}"))
                     for key in RAIL_KEYS for value in BAD_VALUES]


@pytest.mark.parametrize("version", ["1.4", "1.5", "1.6"])
@pytest.mark.parametrize("requirement, says", REFUSED)
def test_a_rail_s_requirement_is_refused_at_every_minor(requirement, says, version):
    """Each refusal holds at every minor that has the keys and comes before the
    stamp check, so the reason given is one that fixes the file."""
    data = _required(_stamped(_source(), version), requirement)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(cfio.SystemBlockError, match=r"system\.rails\[1\]: VDD") as refused:
            cfio.validate(data, allow_source=True)
    assert refused.match(says)


def test_a_non_finite_value_read_from_yaml_is_refused():
    """``.inf`` and ``.nan`` are numbers to YAML and to the schema, never to a
    router; the reader refuses them where a schema cannot."""
    for text in (".inf", ".nan"):
        source = SOURCE.replace('{name: "VDD", class: power,',
                                f'{{name: "VDD", class: power, width_um: {text},')
        source = source.replace('format_version: "1.1"', 'format_version: "1.5"')
        with pytest.raises(cfio.SystemBlockError, match="width_um must be a positive, finite"):
            cfio.loads(source, allow_source=True)
