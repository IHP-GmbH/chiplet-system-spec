# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Connectivity form 1.2: the classic sheet's shape, and the older forms unchanged.

Hermetic: every form is built here, except the frozen 1.0 forms the real KiCad
front end wrote (``tests/fixtures/kicad_form``). Only :func:`parse_form` is
exercised; what the values mean is the import's, tested with it. Tests assert
codes, never messages.
"""
from __future__ import annotations

import copy
import hashlib
import json
import warnings

import pytest

from chiplet_system_description.connectivity import (
    BLANK, CLASSIC, GENERATED, GROUND, POWER, VERSION, Rail, SchematicImportRefused,
    parse_form)
from tests.conftest import FIXTURES_DIR

KICAD_FORMS = sorted((FIXTURES_DIR / "kicad_form").glob("*/kicad/*/connectivity.json"))
SHA = hashlib.sha256(b"x").hexdigest()
FRONT_END = {"tool": "kicad", "tool_version": "9.99", "reader": "test 1", "inputs": []}
NETS = [{"name": "d2d_f0_n", "auto_named": False, "endpoints": [["U1", "1"], ["U2", "1"]]},
        {"name": "VDD", "auto_named": False, "endpoints": [["U1", "2"], ["U2", "2"]]},
        {"name": "GND", "auto_named": False, "endpoints": [["U1", "3"], ["U2", "3"]]}]
U1_KEY = "/11111111-1111-4111-8111-111111111111"
U2_KEY = "/22222222-2222-4222-8222-222222222222"


def classic():
    """A classic form for two dies, one supply and one ground, one pin left open."""
    return {
        "format": "chiplet_system.connectivity", "version": "1.2", "sheet": "classic",
        "base_sha256": None, "generator_report": None,
        "library": {"path": "classic/chiplet_dies.kicad_sym", "sha256": SHA},
        "system_library": {"path": "classic/chiplet_system.kicad_sym", "sha256": SHA},
        "front_end": dict(FRONT_END),
        "symbols": [
            {"ref": "U1", "identity": None, "part": {"lib": "chiplet_dies", "name": "DRIVER"},
             "key": U1_KEY, "value": "DRIVER", "footprint": "chiplet_dies:DRIVER",
             "fields": {"Die": "driver", "Method": "flip_chip", "Thickness_um": "200"},
             "pins": ["1", "2", "3", "4"]},
            {"ref": "U2", "identity": None, "part": {"lib": "chiplet_dies", "name": "ECHO"},
             "key": U2_KEY, "value": "~", "footprint": "chiplet_dies:ECHO",
             "fields": {"Die": "echo"},
             "pins": ["1", "2", "3"]},
        ],
        "nets": copy.deepcopy(NETS),
        "rails": [{"name": "VDD", "class": "power", "volts": "1.2"},
                  {"name": "GND", "class": "ground", "volts": None}],
        "open": [["U1", "4"]],
        "refusals": [],
    }


def blank(version="1.1"):
    doc = classic()
    for key in ("system_library", "rails", "open"):
        del doc[key]
    for sym in doc["symbols"]:
        for key in ("fields", "key", "value", "footprint"):
            del sym[key]
    return dict(doc, version=version, sheet="blank")


def generated(version="1.0"):
    doc = blank(version)
    del doc["library"]
    for sym in doc["symbols"]:
        sym["identity"], sym["part"] = sym["ref"], None
    return dict(doc, sheet="generated", base_sha256=SHA,
                generator_report={"path": "report.json", "sha256": SHA})


KINDS = {GENERATED: generated, BLANK: blank, CLASSIC: lambda version: dict(classic(),
                                                                         version=version)}


def codes(doc):
    with pytest.raises(SchematicImportRefused) as info:
        parse_form(doc)
    return info.value.codes


def test_the_reader_reads_version_1_3():
    assert VERSION == "1.3"


# --- which kind reads at which version ----------------------------------------------------
@pytest.mark.parametrize("kind,version,reads", [
    (GENERATED, "1.0", True), (GENERATED, "1.1", True), (GENERATED, "1.2", True),
    (BLANK, "1.0", False), (BLANK, "1.1", True), (BLANK, "1.2", True),
    (CLASSIC, "1.0", False), (CLASSIC, "1.1", False), (CLASSIC, "1.2", True),
    (GENERATED, "1.3", True), (BLANK, "1.3", True), (CLASSIC, "1.3", True),
])
def test_each_kind_reads_from_its_version_on(kind, version, reads):
    doc = KINDS[kind](version)
    if reads:
        form = parse_form(doc)
        assert form.sheet == kind
        assert (form.generated, form.blank, form.classic) == (
            kind == GENERATED, kind == BLANK, kind == CLASSIC)
    else:
        assert codes(doc) == ["malformed_form"]


def test_a_classic_form_at_a_newer_minor_reads_with_the_policys_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert parse_form(dict(classic(), version="1.4")).classic


def test_a_classic_form_at_1_3_reads_its_units_and_jumpers_unread_and_warns_nothing():
    """gds_to_kicad's classic reader stamps 1.3 for the per-symbol units and
    jumpers the board export reads; the import reads the form as at 1.2."""
    doc = classic()
    for symbol in doc["symbols"]:
        symbol["units"] = [{"name": "A", "pins": list(symbol["pins"])}]
        symbol["jumpers"] = False
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        at_13 = parse_form(dict(doc, version="1.3"))
    assert at_13 == parse_form(classic())


@pytest.mark.parametrize("sheet", ["Classic", "placed", None, ["classic"]])
def test_an_unknown_sheet_kind_is_refused(sheet):
    assert codes(dict(classic(), sheet=sheet)) == ["malformed_form"]


# --- a classic form as read ---------------------------------------------------------------
def test_a_classic_form_reads_every_part():
    form = parse_form(classic())
    assert (form.base_sha256, form.generator_report) == (None, None)
    assert (form.library.name, form.system_library.name) == ("chiplet_dies", "chiplet_system")
    assert form.system_library.path == "classic/chiplet_system.kicad_sym"
    assert [(s.ref, s.identity, s.part.name, s.key, s.value, s.footprint) for s in form.symbols] == [
        ("U1", None, "DRIVER", U1_KEY, "DRIVER", "chiplet_dies:DRIVER"),
        ("U2", None, "ECHO", U2_KEY, "~", "chiplet_dies:ECHO")]
    assert dict(form.symbols[0].fields) == {
        "Die": "driver", "Method": "flip_chip", "Thickness_um": "200"}
    assert dict(form.symbols[1].fields) == {"Die": "echo"}
    assert hash(form.symbols[0]) == hash(form.symbols[0])
    assert form.rails == (Rail("VDD", POWER, "1.2"), Rail("GND", GROUND, None))
    assert form.open == (("U1", "4"),)
    assert [n.name for n in form.nets] == ["d2d_f0_n", "VDD", "GND"]


def test_a_system_library_is_named_as_given():
    doc = classic()
    doc["system_library"]["name"] = "my_system"
    assert parse_form(doc).system_library.name == "my_system"


def test_a_field_map_is_kept_verbatim():
    doc = classic()
    doc["symbols"][0]["fields"] = {"Die": "no such die", "Method": "", "Value": "~",
                                   "Footprint": "chiplet_dies:DRIVER"}
    doc["symbols"][0]["value"] = "~"
    form = parse_form(doc)
    assert form.symbols[0].value == "~"
    assert dict(form.symbols[0].fields) == {"Die": "no such die", "Method": "", "Value": "~",
                                            "Footprint": "chiplet_dies:DRIVER"}


def test_a_net_name_is_kept_verbatim():
    doc = classic()
    doc["nets"] = [{"name": "unconnected-(U1-PadNC)", "auto_named": True,
                    "endpoints": [["U1", "4"]]},
                   {"name": "{slash}", "auto_named": False,
                    "endpoints": [["U1", "1"], ["U2", "1"]]}]
    assert [n.name for n in parse_form(doc).nets] == ["unconnected-(U1-PadNC)", "{slash}"]


def test_a_power_rail_may_leave_its_volts_null():
    doc = classic()
    doc["rails"][0]["volts"] = None
    assert parse_form(doc).rails[0] == Rail("VDD", POWER, None)


@pytest.mark.parametrize("volts", ["1.2", "3.3", "0.9", "5", "12.50", "03.3"])
def test_a_plain_decimal_above_zero_is_a_volts(volts):
    doc = classic()
    doc["rails"][0]["volts"] = volts
    assert parse_form(doc).rails[0].volts == volts


def test_a_sheet_with_no_rails_and_nothing_open_reads():
    form = parse_form(dict(classic(), rails=[], open=[]))
    assert (form.rails, form.open) == ((), ())


def test_two_symbols_sharing_a_designator_are_left_to_the_import():
    doc = classic()
    doc["symbols"][1]["ref"] = "U1"
    assert [s.ref for s in parse_form(doc).symbols] == ["U1", "U1"]


# --- a classic form refused ----------------------------------------------------------------
def _without(key):
    def edit(doc):
        del doc[key]
    return edit


def _set(key, value):
    def edit(doc):
        doc[key] = value
    return edit


def _symbol(key, value, index=0):
    def edit(doc):
        if value is _DELETE:
            del doc["symbols"][index][key]
        else:
            doc["symbols"][index][key] = value
    return edit


def _field(key, value):
    def edit(doc):
        doc["symbols"][0]["fields"][key] = value
    return edit


def _rail(key, value, index=0):
    def edit(doc):
        if value is _DELETE:
            del doc["rails"][index][key]
        else:
            doc["rails"][index][key] = value
    return edit


_DELETE = object()

MALFORMED = {
    "base_sha256_set": _set("base_sha256", SHA),
    "generator_report_set": _set("generator_report", {"path": "r.json", "sha256": SHA}),
    "library_missing": _without("library"),
    "library_no_sha": _set("library", {"path": "chiplet_dies.kicad_sym"}),
    "system_library_missing": _without("system_library"),
    "system_library_null": _set("system_library", None),
    "system_library_no_path": _set("system_library", {"sha256": SHA}),
    "system_library_empty_sha": _set("system_library", {"path": "s.kicad_sym", "sha256": ""}),
    "identity_set": _symbol("identity", "U1"),
    "part_missing": _symbol("part", _DELETE),
    "part_null": _symbol("part", None),
    "part_no_name": _symbol("part", {"lib": "chiplet_dies"}),
    "fields_missing": _symbol("fields", _DELETE),
    "fields_a_list": _symbol("fields", ["flip_chip"]),
    "fields_a_string": _symbol("fields", "flip_chip"),
    "fields_null": _symbol("fields", None),
    "field_a_number": _field("Thickness_um", 200),
    "field_a_bool": _field("Edge", True),
    "key_missing": _symbol("key", _DELETE),
    "key_null": _symbol("key", None),
    "key_not_a_path": _symbol("key", "11111111-1111-4111-8111-111111111111"),
    "key_with_root": _symbol("key", U1_KEY + "/" + U2_KEY[1:]),
    "value_missing": _symbol("value", _DELETE),
    "value_a_number": _symbol("value", 1),
    "footprint_missing": _symbol("footprint", _DELETE),
    "duplicate_key": lambda doc: doc["symbols"][1].__setitem__("key", doc["symbols"][0]["key"]),
    "rails_missing": _without("rails"),
    "rails_null": _set("rails", None),
    "rails_an_object": _set("rails", {"VDD": "1.2"}),
    "rail_a_string": _set("rails", ["VDD"]),
    "rail_no_name": _rail("name", _DELETE),
    "rail_empty_name": _rail("name", ""),
    "rail_no_class": _rail("class", _DELETE),
    "rail_bad_class": _rail("class", "signal"),
    "rail_no_volts_key": _rail("volts", _DELETE),
    "volts_unit": _rail("volts", "3V3"),
    "volts_spaced": _rail("volts", "3.3 V"),
    "volts_zero": _rail("volts", "0"),
    "volts_zero_point": _rail("volts", "0.00"),
    "volts_negative": _rail("volts", "-1.2"),
    "volts_trailing_dot": _rail("volts", "1."),
    "volts_leading_dot": _rail("volts", ".9"),
    "volts_exponent": _rail("volts", "1e0"),
    "volts_empty": _rail("volts", ""),
    "volts_a_number": _rail("volts", 1.2),
    "ground_with_volts": _rail("volts", "0.0", index=1),
    "ground_with_positive_volts": _rail("volts", "1.2", index=1),
    "rail_twice": _set("rails", [{"name": "VDD", "class": "power", "volts": "1.2"},
                                 {"name": "VDD", "class": "power", "volts": "1.2"}]),
    "open_missing": _without("open"),
    "open_null": _set("open", None),
    "open_flat": _set("open", ["U1", "4"]),
    "open_three": _set("open", [["U1", "4", "x"]]),
    "open_empty_terminal": _set("open", [["U1", ""]]),
    "open_a_number": _set("open", [["U1", 4]]),
    "open_twice": _set("open", [["U1", "4"], ["U1", "4"]]),
}


@pytest.mark.parametrize("edit", MALFORMED.values(), ids=MALFORMED.keys())
def test_a_malformed_classic_form_is_refused(edit):
    doc = classic()
    edit(doc)
    assert codes(doc) == ["malformed_form"]


def test_every_defect_of_a_classic_form_is_listed():
    doc = classic()
    for name in ("system_library_missing", "identity_set", "volts_unit", "open_flat"):
        MALFORMED[name](doc)
    with pytest.raises(SchematicImportRefused) as info:
        parse_form(doc)
    assert len(info.value.refusals) == 4


# --- the classic keys on any other sheet ---------------------------------------------------
CLASSIC_ONLY = {
    "system_library": lambda doc: doc.update(system_library=classic()["system_library"]),
    "rails": lambda doc: doc.update(rails=[]),
    "open": lambda doc: doc.update(open=[]),
    "rails_null": lambda doc: doc.update(rails=None),
    "fields": lambda doc: doc["symbols"][0].update(fields={"method": "flip_chip"}),
    "fields_null": lambda doc: doc["symbols"][0].update(fields=None),
}


@pytest.mark.parametrize("kind,version", [(GENERATED, "1.0"), (GENERATED, "1.1"),
                                          (GENERATED, "1.2"), (BLANK, "1.1"),
                                          (BLANK, "1.2")])
@pytest.mark.parametrize("edit", CLASSIC_ONLY.values(), ids=CLASSIC_ONLY.keys())
def test_a_classic_key_on_another_sheet_is_refused(kind, version, edit):
    doc = KINDS[kind](version)
    parse_form(doc)
    edit(doc)
    assert codes(doc) == ["malformed_form"]


# --- the older forms read as before --------------------------------------------------------
def _as_rows(form):
    return ([{"ref": s.ref, "identity": s.identity,
              "part": None if s.part is None else {"lib": s.part.lib, "name": s.part.name},
              "pins": list(s.pins)} for s in form.symbols],
            [{"name": n.name, "auto_named": n.auto_named,
              "endpoints": [list(ep) for ep in n.endpoints]} for n in form.nets])


def _unchanged(form, doc):
    assert (form.system_library, form.rails, form.open) == (None, (), ())
    assert all(s.key is None and s.value is None and s.footprint is None and s.fields is None
               for s in form.symbols)
    symbols = [{**{"part": None}, **s} for s in doc["symbols"]]
    assert _as_rows(form) == (symbols, doc["nets"])
    assert form.front_end == doc["front_end"]
    assert list(form.refusals) == doc.get("refusals", [])


@pytest.mark.parametrize("path", KICAD_FORMS, ids=lambda p: f"{p.parts[-4]}-{p.parts[-2]}")
def test_the_kicad_readers_1_0_forms_read_as_before(path):
    doc = json.loads(path.read_text())
    assert doc["version"] == "1.0" and "sheet" not in doc
    form = parse_form(doc)
    assert form.generated and not form.blank and not form.classic
    assert form.base_sha256 == doc["base_sha256"]
    assert (form.generator_report.path, form.generator_report.sha256) == (
        doc["generator_report"]["path"], doc["generator_report"]["sha256"])
    assert form.library is None
    _unchanged(form, doc)


def test_a_1_1_blank_form_reads_as_before():
    doc = blank("1.1")
    form = parse_form(doc)
    assert form.blank and (form.base_sha256, form.generator_report) == (None, None)
    assert (form.library.name, form.library.path) == (
        "chiplet_dies", "classic/chiplet_dies.kicad_sym")
    _unchanged(form, doc)


def test_a_blank_symbol_may_still_carry_an_identity():
    doc = blank("1.1")
    doc["symbols"][0]["identity"] = "U1"
    assert parse_form(doc).symbols[0].identity == "U1"


@pytest.mark.parametrize("kind", [BLANK, GENERATED])
def test_a_1_2_sheet_may_carry_the_symbol_identity(kind):
    doc = KINDS[kind]("1.2")
    doc["symbols"][0].update(key=U1_KEY, value="~", footprint="chiplet_dies:DRIVER",
                             fields={"Die": "driver"})
    sym = parse_form(doc).symbols[0]
    assert (sym.key, sym.value, sym.footprint, dict(sym.fields)) == (
        U1_KEY, "~", "chiplet_dies:DRIVER", {"Die": "driver"})


@pytest.mark.parametrize("kind,version", [(GENERATED, "1.0"), (BLANK, "1.1")])
def test_an_older_form_ignores_key_value_and_footprint(kind, version):
    doc = KINDS[kind](version)
    doc["symbols"][0].update(key="not-a-path", value=1, footprint=None)
    sym = parse_form(doc).symbols[0]
    assert (sym.key, sym.value, sym.footprint, sym.fields) == (None, None, None, None)


@pytest.mark.parametrize("drop", ["key", "value", "footprint", "fields"])
@pytest.mark.parametrize("kind", [BLANK, GENERATED])
def test_a_partial_identity_at_1_2_is_malformed(kind, drop):
    doc = KINDS[kind]("1.2")
    doc["symbols"][0].update(key=U1_KEY, value="~", footprint="chiplet_dies:DRIVER",
                             fields={"Die": "driver"})
    del doc["symbols"][0][drop]
    assert codes(doc) == ["malformed_form"]
