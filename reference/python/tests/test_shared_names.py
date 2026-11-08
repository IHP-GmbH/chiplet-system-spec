# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The names moved here from the die flow keep the values they had there.

Each value is a wire string or a closed vocabulary that some producer writes and
some reader matches, so a change here is a change in all of them. The table was
generated from the die flow's own modules at the commit the names moved from; release 1.10.0 added the gf180mcuD rows and keyed
the check options by PDK, and release 1.11.0 added checks version 1.1 with its
not-checked value and table. Release 1.12.0 added the transistor-level kinds,
the read tuple, the ihp-sg13cmos5l row and the transistor_view module, whose
scanner patterns are pinned with the rest because they move every deck text
sha256. ready_die_names now also exports the run-check rule its two readers
share, ``run_check_not_counted``. Release 1.13.0 added the agreement module: its
device table, refusal words and amendment receipt words are pinned, and so are
the patterns its CDL reader and value compare read with. Release 1.19.0 added
the ihp-sg13cmos5l rows of the KLayout check tables, read off its decks at
IHP-Open-PDK 22f43352. Release 1.21.0 added the sheet_names module, the rule a
KiCad sheet's net, pin and pad names are written and read back with. A value
that moves fails here, at any depth and by type as well as by value, and so
does a public name added or removed.
"""
import importlib
import inspect
import re
from types import MappingProxyType

import pytest

from chiplet_format_io import die_contract, pins

GOLDEN = {
    "ready_die_names": {
        "BUILD_RECORD_STATUS": "status",
        "BUILD_RECORD_QUALIFIED": "qualified",
        "BUILD_RECORD_FACTS": ("status", "qualified"),
        "BUILT_NOT_QUALIFIED": "built-not-qualified",
        "EMITTED_RECORD_SCHEMA": "chiplet-flow-librelane-build-record/v1",
        "CHIPLET_FULL_RECORD_SCHEMA": "chiplet-full-build-record/v1",
        "PRODUCER_RECORD_SCHEMAS": ("chiplet-flow-librelane-build-record/v1",
                                    "chiplet-full-build-record/v1"),
        "LAYOUT": "layout.gds",
        "INVENTORY": "pins.json",
        "PADMAP": "padmap.json",
        "BUILD_RECORD": "build-record.json",
        "CONTRACT": "die-contract.json",
        "CHECKS_VERSION": "1.0",
        "LVS_RUN_CHECKS": ("deck_log", "options", "deck_input", "ports", "view_receipt", "view_rehash",
                           "view_xor", "design_cdl", "deck_tree_at_build"),
        "CHECKS_VERSION_NOT_CHECKED": "1.1",
        "CHECKS_VERSIONS": ("1.0", "1.1"),
        "PASS": "pass",
        "NOT_NEEDED": "not_needed",
        "FAIL": "fail",
        "NOT_CHECKED": "not_checked",
        "XOR_DIFFERS": "differs",
        "XOR_TIMEOUT": "timeout",
        "XOR_KLAYOUT_ABSENT": "klayout_absent",
        "XOR_ERROR": "error",
        "LVS_DECK_TREES": MappingProxyType({
            "ihp-sg13g2": (("873c60eb53ffd5f5a7ab3e9fd755b31af21c9e38269588ed5454bf81f6c3f28b",
                            "sg13g2.lvs"),),
            "gf180mcuD": (("e49f13a03fc3a0b53a3eb554b28c4206ec31a48de10d67b149c86b851f8e21b0",
                           "gf180mcu.lvs"),),
        }),
        "NO_PORT_LABEL_CHECK": "no_port_label_check",
        "PORTS_NOT_CHECKED": MappingProxyType({
            "e49f13a03fc3a0b53a3eb554b28c4206ec31a48de10d67b149c86b851f8e21b0": "no_port_label_check",
        }),
        "DRC_PARTS": ("drc", "density", "antenna"),
        "DRC_RUNSETS": MappingProxyType({
            "ihp-sg13g2": MappingProxyType({
                "drc": "ihp-sg13g2/libs.tech/klayout/tech/drc/ihp-sg13g2.drc",
                "density": "ihp-sg13g2/libs.tech/klayout/tech/drc/rule_decks/density.drc",
                "antenna": "ihp-sg13g2/libs.tech/klayout/tech/drc/rule_decks/antenna.drc",
            }),
            "gf180mcuD": MappingProxyType({
                "drc": "gf180mcuD/libs.tech/klayout/tech/drc/gf180mcu.drc",
                "density": "gf180mcuD/libs.tech/klayout/tech/drc/rule_decks/density.drc",
                "antenna": "gf180mcuD/libs.tech/klayout/tech/drc/rule_decks/antenna.drc",
            }),
            "ihp-sg13cmos5l": MappingProxyType({
                "drc": "ihp-sg13cmos5l/libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc",
                "density": "ihp-sg13cmos5l/libs.tech/klayout/tech/drc/rule_decks/density.drc",
                "antenna": "ihp-sg13cmos5l/libs.tech/klayout/tech/drc/rule_decks/antenna.drc",
            }),
        }),
        "DRC_THREADS": MappingProxyType({"ihp-sg13g2": "threads", "gf180mcuD": "thr",
                                         "ihp-sg13cmos5l": "threads"}),
        "DRC_OPTIONS": MappingProxyType({
            "ihp-sg13g2": MappingProxyType({
                "drc": MappingProxyType({"no_recommended": (True,), "run_mode": ("deep", "flat")}),
                "density": MappingProxyType({}),
                "antenna": MappingProxyType({}),
            }),
            "gf180mcuD": MappingProxyType({
                "drc": MappingProxyType({"feol": (True,), "beol": (True,), "dummy": (True,),
                                         "dummy_no_sub_prev": (True,), "offgrid": (True,),
                                         "conn_drc": (True,), "wedge": (True,), "run_mode": ("deep",)}),
                "density": MappingProxyType({"run_mode": ("tiling",)}),
                "antenna": MappingProxyType({"run_mode": ("deep",)}),
            }),
            "ihp-sg13cmos5l": MappingProxyType({
                "drc": MappingProxyType({"no_recommended": (True,), "run_mode": ("deep", "flat")}),
                "density": MappingProxyType({}),
                "antenna": MappingProxyType({}),
            }),
        }),
        "DRC_REQUIRED": MappingProxyType({
            "ihp-sg13g2": MappingProxyType({"drc": (), "density": (), "antenna": ()}),
            "gf180mcuD": MappingProxyType({
                "drc": ("feol", "beol", "dummy", "dummy_no_sub_prev", "offgrid", "conn_drc", "wedge",
                        "run_mode"),
                "density": ("run_mode",),
                "antenna": ("run_mode",),
            }),
            "ihp-sg13cmos5l": MappingProxyType({"drc": ("run_mode",), "density": (), "antenna": ()}),
        }),
        "LEVEL1_FACT": "level1",
        "LEVEL1_DETAIL_FACT": "level1_detail",
        "LEVEL1_MET": "met",
        "LEVEL1_NOT_VERIFIED": "not_verified",
        "LEVEL1_NOT_CLAIMED": "not_claimed",
        "LEVEL1_THIRD_PARTY": "third_party",
        "NO_CHECKS_RECORDED": "no_checks_recorded",
        "LEVEL1_ABSENT": "level1_absent",
        "FIELD_ABSENT": "field_absent",
        "CHECK_NOT_ON_LAYOUT": "check_not_on_layout",
        "CHECK_NOT_OFFICIAL": "check_not_official",
        "CHECK_NOT_ZERO": "check_not_zero",
        "LVS_NOT_RUN_TOKEN": "lvs_not_run",
        "LVS_UNREADABLE_TOKEN": "lvs_unreadable",
        "LVS_VIEW_NOT_COUNTED": "lvs_view_not_counted",
        "LVS_DECK_NOT_OFFICIAL": "lvs_deck_not_official",
        "LVS_RUN_CHECK": "lvs_run_check",
        "LVS_DIFFERS": "lvs_differs",
        "LEVEL1_TOKENS": ("no_checks_recorded", "level1_absent", "field_absent", "check_not_on_layout",
                          "check_not_official", "check_not_zero", "lvs_not_run", "lvs_unreadable",
                          "lvs_view_not_counted", "lvs_deck_not_official", "lvs_run_check",
                          "lvs_differs"),
        "NOT_CHECKED_TOKEN": "not_checked",
        "LVS_FACT": "lvs",
        "LVS_SOURCE_FACT": "lvs_source",
        "LVS_VIEW_FACT": "lvs_view",
        "LVS_MATCHED": "0",
        "LVS_DIFFERED": "1",
        "LVS_UNREADABLE": "unreadable",
        "LVS_NOT_RUN": "not_run",
        "LVS_VIEWS": ("fill_flattened", "fill_flattened+masters_unified", "masters_unified",
                      "state_gds"),
        "LVS_VIEW_UNRECORDED": "unrecorded",
    },
    "technology": {
        "TECHNOLOGY_OF_PDK": {"ihp-sg13g2": "sg13g2", "ihp-sg13cmos5l": "sg13cmos5l",
                              "gf180mcuD": "gf180mcu"},
    },
    "pins": {
        "PINS_VERSION": "1.0",
        "USE_BY_ROLE": {"POWER": "POWER", "GROUND": "GROUND", "SIGNAL": "SIGNAL"},
        "ROLE": {"SIGNAL": "signal", "POWER": "power", "GROUND": "ground"},
        "DIRECTION": {"INPUT": "input", "OUTPUT": "output", "INOUT": "bidir"},
    },
    "eda_views": {
        "SCHEMA": "chiplet-flow-eda-views/v1",
        "RECORD": "eda-views.json",
        "KINDS": ("def", "tech_lef", "lef", "liberty", "sdc", "pins", "spef", "sim_model", "netlist",
                  "cdl", "spice", "transistor_view"),
        "READ_KINDS": ("def", "tech_lef", "lef", "liberty", "sdc", "pins", "spef", "sim_model", "netlist"),
        "REQUIRED": ("def", "tech_lef", "lef", "liberty", "sdc", "pins"),
        "SINGLE": ("def", "tech_lef", "sdc", "pins"),
        "RECORDED": "recorded",
        "UNQUALIFIED": "unqualified",
        "GATE_VIEW_NOT_RECORDED": "gate_view_not_recorded",
        "PAD_MODEL": "module {macro} (\n    inout {pin}\n);\nendmodule\n",
    },
    "transistor_view": {
        "VIEW_FORMAT": "chiplet-eda-views-transistor/v1",
        "BLOCK_CARDS": (".subckt", ".ends", ".end"),
        "ALWAYS_GLOBAL": (".include", ".lib", ".option", ".options", ".temp", ".global", ".control",
                          ".endc", ".tran", ".ac", ".dc", ".op", ".noise", ".save", ".csparam", ".func"),
        "REASONS": ("view_shape", "selector", "dialect", "digest", "encoding", "top_form", "subckt_absent",
                    "continuation", "comment_in_card", "include_card", "device_card", "block_global",
                    "nesting", "card_syntax", "name_clash", "view_differs"),
    },
    "agreement": {
        "DEVICES": {
            "sg13g2": {
                "sg13_lv_nmos": ("M", 4, "sized"), "sg13_lv_pmos": ("M", 4, "sized"),
                "sg13_hv_nmos": ("M", 4, "sized"), "sg13_hv_pmos": ("M", 4, "sized"),
                "rsil": ("R", 3, "resistor"), "rppd": ("R", 3, "resistor"), "rhigh": ("R", 3, "resistor"),
                "cap_cmim": ("C", 2, "sized"),
                "dantenna": ("D", 2, "diode"), "dpantenna": ("D", 2, "diode"),
                "ntap1": ("R", 2, "tap"), "ptap1": ("R", 2, "tap"),
            },
        },
        "SIM_ONLY": ("mm_ok",),
        "NOT_CLAIMED": ("tap and antenna-diode geometry: not compared",
                        "resistor CDL value: derived value, not compared",
                        "the layout and its LVS: no claim"),
        "AMENDMENT_FORMAT": "cdl-reference-amendment/1",
        "AMENDMENT_KINDS": ("bulk", "instance_pin", "subckt_pin"),
        "REASONS": ("dialect", "technology", "cdl_files", "digest", "encoding", "cdl_syntax", "cdl_top",
                    "amendment"),
    },
    "verilog_closure": {
        "GATES": frozenset(("and buf bufif0 bufif1 cmos nand nmos nor not notif0 notif1 or "
                            "pmos pulldown pullup rcmos rnmos rpmos rtran rtranif0 rtranif1 "
                            "tran tranif0 tranif1 xnor xor").split()),
        "DECLARATIONS": frozenset(("assign event genvar inout input integer localparam "
                                   "output parameter real realtime reg specparam supply0 "
                                   "supply1 time tri tri0 tri1 triand trior trireg wand wire "
                                   "wor").split()),
        "REFUSED": frozenset(("always always_comb always_ff always_latch begin case defparam "
                              "final for fork function generate if initial task").split()),
        "HARMLESS": frozenset("celldefine endcelldefine resetall timescale".split()),
        "ENDS": {"module": "endmodule", "macromodule": "endmodule", "primitive": "endprimitive"},
        "TOKEN": re.compile(r"\\\S+|\$?[A-Za-z_][A-Za-z0-9_$]*|\d[\w.']*|\S"),
        "UNIT": re.compile(r"\b(module|macromodule|primitive)\b(.*?)\b(endmodule|endprimitive)\b", re.S),
    },
    "sheet_names": {
        "SHEET_NAME_RULE": re.compile(r"[A-Za-z0-9_.+-](?:[A-Za-z0-9_.+-]|\[[0-9]+\])*"),
        "SHEET_NAME_WORDS": "plain [A-Za-z0-9_.+-] name, with [N] subscripts allowed after its first character",
    },
}

#: The patterns the public tokenizer reads with. They are private, and pinned
#: all the same: the public names could keep their values while the tokens they
#: produce move.
PRIVATE = {
    "transistor_view": {
        "_FOLD": str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"),
        "_WHITE": " \t\r\x0b\x0c",
        "_SPLIT": re.compile(r"[ \t\r\x0b\x0c]+"),
        "_INLINE": re.compile(r"[ \t][$;][ \t]"),
        "_TERMINATOR": re.compile(r";|//|(?:^|[ \t,])\$"),
        "_ELEMENTS": "xkviefghb",
        "_VALUED": "rcl",
        "_VALUE": re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:e[+-]?[0-9]+)?(?:t|g|meg|k|mil|m|u|n|p|f)?"
                             r"|\{[^ ]*\}", re.IGNORECASE | re.ASCII),
        "_SUBCKT_DOT_CARDS": (".param",),
        "_UNMAPPED": re.compile(r"[^A-Za-z0-9_]"),
        "_BY_PREFIX": ((".inc", ".include"), (".lib", ".lib"), (".endl", ".endl"),
                       (".control", ".control"), (".endc", ".endc")),
    },
    "agreement": {
        "_GEOMETRY": {"tap": ("a", "p", "w", "l", "r"), "diode": ("a", "p", "w", "l")},
        "_SUFFIX": {"t": 12, "g": 9, "meg": 6, "k": 3, "m": -3, "u": -6, "n": -9, "p": -12, "f": -15},
        "_NUMBER": re.compile(r"([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+))(?:e([-+]?[0-9]+))?(meg|[tgkmunpf])?", re.ASCII),
        "_DOLLAR_MODEL": re.compile(r"\$\[([^\]]+)\]"),
    },
    "verilog_closure": {
        "_LEX": re.compile(r'\\\S+|/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"', re.S),
        "_DIRECTIVE": re.compile(r"`(\w+)"),
        "_NAME": re.compile(r"\\\S+|[A-Za-z_][A-Za-z0-9_$]*"),
        "_TIMESCALE": re.compile(r"[^`\n]*"),
    },
}

#: Constants a module imports rather than defines, pinned too: the record reader
#: checks a die contract against them. They are not in the module's __all__.
IMPORTED = {
    "eda_views": {
        "CONTRACT": "die-contract.json",
        "CONTRACT_SCHEMA": "die-output-contract",
        "CONTRACT_VERSION": "1.2",
    },
}

#: The functions and classes each module exports, beside the constants of GOLDEN.
CALLABLES = {
    "ready_die_names": ("run_check_not_counted",),
    "technology": (),
    "pins": (),
    "eda_views": ("EdaViewsRefused", "check_bound", "read", "gate_view"),
    "transistor_view": ("TransistorViewRefused", "DeckText", "TransistorView", "cards", "card_word",
                        "subckt_ports", "instance_subckt", "deck_text", "derive", "transistor_view"),
    "agreement": ("AgreementRefused", "Agreement", "agree", "agree_record"),
    "verilog_closure": ("VerilogClosureError", "strip", "active_text", "split_statements", "is_name",
                        "units", "closure"),
    "sheet_names": ("is_sheet_name",),
}


def _constants(module):
    return {name for name in vars(module) if name.isupper() and not name.startswith("_")}


def _defined(module):
    """The public names a module defines itself: its constants, less those it
    imports, and its own functions and classes."""
    own = {name for name, value in vars(module).items()
           if not name.startswith("_") and (inspect.isfunction(value) or inspect.isclass(value))
           and value.__module__ == module.__name__}
    return own | (_constants(module) - set(IMPORTED.get(module.__name__.rpartition(".")[2], ())))


def _same(a, b):
    """Equal and of the same type at every depth: a mapping proxy is not a dict,
    a tuple is not a list, ``True`` is not ``1``, and a pattern is its text and
    its flags. Mappings are compared in order."""
    if type(a) is not type(b):
        return False
    if isinstance(a, re.Pattern):
        return (a.pattern, a.flags) == (b.pattern, b.flags)
    if isinstance(a, (dict, MappingProxyType)):
        return len(a) == len(b) and all(_same(ka, kb) and _same(va, vb)
                                        for (ka, va), (kb, vb) in zip(a.items(), b.items()))
    if isinstance(a, (tuple, list)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, frozenset):
        return _same(sorted(a, key=repr), sorted(b, key=repr))
    return a == b


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_a_module_ships_exactly_the_names_and_values_it_moved_with(name):
    module = importlib.import_module(f"chiplet_format_io.{name}")
    assert _constants(module) - set(IMPORTED.get(name, ())) == set(GOLDEN[name])
    pinned = {**GOLDEN[name], **PRIVATE.get(name, {}), **IMPORTED.get(name, {})}
    moved = [key for key, value in pinned.items() if not _same(getattr(module, key), value)]
    assert not moved, f"{name} changed {moved}"


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_a_module_exports_exactly_what_it_defines(name):
    module = importlib.import_module(f"chiplet_format_io.{name}")
    exported = list(module.__all__)
    assert len(exported) == len(set(exported)), f"{name}.__all__ names something twice"
    assert set(exported) == set(GOLDEN[name]) | set(CALLABLES[name])
    assert set(exported) == _defined(module), "a public name is defined and not exported, or the reverse"


def test_the_drift_guard_tells_types_apart_at_every_depth():
    assert not _same(MappingProxyType({"a": MappingProxyType({})}), MappingProxyType({"a": {}}))
    assert not _same({"a": (True,)}, {"a": (1,)})
    assert not _same(("a",), ["a"])
    assert not _same(frozenset({1}), frozenset({True}))
    assert not _same(re.compile("a", re.S), re.compile("a"))
    assert not _same({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert _same(GOLDEN, GOLDEN)


def test_the_pin_tables_map_into_the_contracts_closed_vocabulary():
    assert set(pins.ROLE.values()) <= set(die_contract.TERMINAL_ROLES)
    assert set(pins.DIRECTION.values()) <= set(die_contract.TERMINAL_DIRECTIONS)


def test_every_pdk_with_runsets_has_its_thread_key_options_and_required_keys():
    from chiplet_format_io import ready_die_names as names
    pdks = set(names.DRC_RUNSETS)
    assert set(names.DRC_THREADS) == set(names.DRC_OPTIONS) == set(names.DRC_REQUIRED) == pdks
    for pdk in pdks:
        assert tuple(names.DRC_RUNSETS[pdk]) == names.DRC_PARTS
        assert tuple(names.DRC_OPTIONS[pdk]) == tuple(names.DRC_REQUIRED[pdk]) == names.DRC_PARTS
        for part in names.DRC_PARTS:
            options = names.DRC_OPTIONS[pdk][part]
            assert set(names.DRC_REQUIRED[pdk][part]) <= set(options)
            assert names.DRC_THREADS[pdk] not in options
            assert all(options[key] for key in options), "a key no value is allowed for"


def test_every_runset_sits_under_its_own_pdk_and_the_pdk_has_a_technology():
    from chiplet_format_io import ready_die_names as names
    from chiplet_format_io.technology import TECHNOLOGY_OF_PDK
    for pdk, runsets in names.DRC_RUNSETS.items():
        assert pdk in TECHNOLOGY_OF_PDK
        assert {path.split("/")[0] for path in runsets.values()} == {pdk}


def test_a_not_checked_ports_row_names_an_official_deck_tree_and_is_no_level1_reason():
    from chiplet_format_io import ready_die_names as names
    official = {digest for rows in names.LVS_DECK_TREES.values() for digest, _ in rows}
    assert set(names.PORTS_NOT_CHECKED) <= official
    assert set(names.PORTS_NOT_CHECKED.values()) == {names.NO_PORT_LABEL_CHECK}
    assert names.CHECKS_VERSIONS[0] == names.CHECKS_VERSION
    assert names.CHECKS_VERSION_NOT_CHECKED in names.CHECKS_VERSIONS
    assert names.NOT_CHECKED not in (names.PASS, names.NOT_NEEDED, names.FAIL)
    assert names.NOT_CHECKED_TOKEN not in names.LEVEL1_TOKENS
