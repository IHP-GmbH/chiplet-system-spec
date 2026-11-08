# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The names a ready die travels under: its four files, its build record's words,
the die LVS verdict the record carries, and the level-1 words admission judges
its checks with.

The flow that builds a die writes a ready die and the flow that assembles dies
admits it, so both read these names from here. Every value is a wire string: a
rename is a change in every producer and reader or in none. One rule over them
lives here too, for the same reason: which value of an LVS run check counts
(:func:`run_check_not_counted`).
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Optional, Tuple

__all__ = [
    "BUILD_RECORD_STATUS",
    "BUILD_RECORD_QUALIFIED",
    "BUILD_RECORD_FACTS",
    "BUILT_NOT_QUALIFIED",
    "EMITTED_RECORD_SCHEMA",
    "CHIPLET_FULL_RECORD_SCHEMA",
    "PRODUCER_RECORD_SCHEMAS",
    "LAYOUT",
    "INVENTORY",
    "PADMAP",
    "BUILD_RECORD",
    "CONTRACT",
    "CHECKS_VERSION",
    "LVS_RUN_CHECKS",
    "CHECKS_VERSION_NOT_CHECKED",
    "CHECKS_VERSIONS",
    "PASS",
    "NOT_NEEDED",
    "FAIL",
    "NOT_CHECKED",
    "XOR_DIFFERS",
    "XOR_TIMEOUT",
    "XOR_KLAYOUT_ABSENT",
    "XOR_ERROR",
    "LVS_DECK_TREES",
    "NO_PORT_LABEL_CHECK",
    "PORTS_NOT_CHECKED",
    "run_check_not_counted",
    "DRC_PARTS",
    "DRC_RUNSETS",
    "DRC_THREADS",
    "DRC_OPTIONS",
    "DRC_REQUIRED",
    "LEVEL1_FACT",
    "LEVEL1_DETAIL_FACT",
    "LEVEL1_MET",
    "LEVEL1_NOT_VERIFIED",
    "LEVEL1_NOT_CLAIMED",
    "LEVEL1_THIRD_PARTY",
    "NO_CHECKS_RECORDED",
    "LEVEL1_ABSENT",
    "FIELD_ABSENT",
    "CHECK_NOT_ON_LAYOUT",
    "CHECK_NOT_OFFICIAL",
    "CHECK_NOT_ZERO",
    "LVS_NOT_RUN_TOKEN",
    "LVS_UNREADABLE_TOKEN",
    "LVS_VIEW_NOT_COUNTED",
    "LVS_DECK_NOT_OFFICIAL",
    "LVS_RUN_CHECK",
    "LVS_DIFFERS",
    "LEVEL1_TOKENS",
    "NOT_CHECKED_TOKEN",
    "LVS_FACT",
    "LVS_SOURCE_FACT",
    "LVS_VIEW_FACT",
    "LVS_MATCHED",
    "LVS_DIFFERED",
    "LVS_UNREADABLE",
    "LVS_NOT_RUN",
    "LVS_VIEWS",
    "LVS_VIEW_UNRECORDED",
]

#: What admission reads off a die's build record and records as stage facts:
#: the two keys, by name, and the one status the die flow's producers write for
#: a die they built and did not qualify.
BUILD_RECORD_STATUS = "status"
BUILD_RECORD_QUALIFIED = "qualified"
BUILD_RECORD_FACTS = (BUILD_RECORD_STATUS, BUILD_RECORD_QUALIFIED)
BUILT_NOT_QUALIFIED = "built-not-qualified"

#: The build-record formats the die flow's producers write, by the record's
#: ``schema``: the record emitted over a LibreLane run and the packaged one. A die
#: is the die flow's own only when its record is one of these formats AND says
#: :data:`BUILT_NOT_QUALIFIED`; a foreign format that uses the same word is a die
#: built elsewhere. This classifies a record format; it authenticates nothing.
EMITTED_RECORD_SCHEMA = "chiplet-flow-librelane-build-record/v1"
CHIPLET_FULL_RECORD_SCHEMA = "chiplet-full-build-record/v1"
PRODUCER_RECORD_SCHEMAS = (EMITTED_RECORD_SCHEMA, CHIPLET_FULL_RECORD_SCHEMA)

#: The four files a ready die's directory consists of, under the names both
#: producers write: the packager relocates a contract that was already admitted,
#: and the emitter writes a fresh one over a LibreLane run. The two outputs are
#: the same shape on disk, and admission reads either. A packaged die whose
#: inventory is a black-box padmap carries ``padmap.json`` in place of
#: ``pins.json``.
LAYOUT = "layout.gds"
INVENTORY = "pins.json"
PADMAP = "padmap.json"
BUILD_RECORD = "build-record.json"
CONTRACT = "die-contract.json"

#: The ``checks`` block the emitter writes into its build record, beside
#: ``view``: its version, and the LVS run checks of that version, in the order
#: admission reports the first failing one. A check set that changes is a new
#: version, never an edit of this one.
CHECKS_VERSION = "1.0"
LVS_RUN_CHECKS = ("deck_log", "options", "deck_input", "ports", "view_receipt",
                  "view_rehash", "view_xor", "design_cdl", "deck_tree_at_build")
#: Version 1.1 is 1.0 with one more value, :data:`NOT_CHECKED`. The emitter
#: writes it only for a block in which some run check reads that value, so every
#: other block stays 1.0. Admission reads the versions of :data:`CHECKS_VERSIONS`.
CHECKS_VERSION_NOT_CHECKED = "1.1"
CHECKS_VERSIONS = (CHECKS_VERSION, CHECKS_VERSION_NOT_CHECKED)
#: The values a run check reads: :data:`PASS` or :data:`FAIL`, and
#: :data:`NOT_NEEDED` for ``view_xor`` over the state layout, where no view file
#: was written. ``view_xor`` over a view file reads the XOR script's ``pass`` or
#: :data:`XOR_DIFFERS`, :data:`XOR_TIMEOUT`, :data:`XOR_KLAYOUT_ABSENT`,
#: :data:`XOR_ERROR` for a run that printed no one value, or :data:`FAIL` when
#: the step dir holds no view file to compare. ``ports`` reads
#: :data:`NOT_CHECKED` over a deck tree of :data:`PORTS_NOT_CHECKED` when the
#: deck's log states no port comparison: the check applies and the deck does not
#: perform it.
PASS = "pass"
NOT_NEEDED = "not_needed"
FAIL = "fail"
NOT_CHECKED = "not_checked"
XOR_DIFFERS = "differs"
XOR_TIMEOUT = "timeout"
XOR_KLAYOUT_ABSENT = "klayout_absent"
XOR_ERROR = "error"

#: The LVS deck trees whose verdicts count, per PDK name: each row is the digest
#: of the directory holding the deck script, as the LVS step records it in
#: ``deck-tree.json``, and the script's file name in it. A row is added only when
#: a PDK release changes the deck.
LVS_DECK_TREES = MappingProxyType({
    "ihp-sg13g2": (("873c60eb53ffd5f5a7ab3e9fd755b31af21c9e38269588ed5454bf81f6c3f28b",
                    "sg13g2.lvs"),),
    "gf180mcuD": (("e49f13a03fc3a0b53a3eb554b28c4206ec31a48de10d67b149c86b851f8e21b0",
                   "gf180mcu.lvs"),),
})
#: The deck trees of :data:`LVS_DECK_TREES` whose deck does not check the
#: top-level port labels, by digest, each with its reason. The build record
#: carries the digest, never the reason: this table is the one copy. The
#: gf180mcuD deck compares without flagging missing ports.
NO_PORT_LABEL_CHECK = "no_port_label_check"
PORTS_NOT_CHECKED = MappingProxyType({
    "e49f13a03fc3a0b53a3eb554b28c4206ec31a48de10d67b149c86b851f8e21b0": NO_PORT_LABEL_CHECK,
})


def run_check_not_counted(lvs: Any, version: Any, *,
                          ports_may_go_unchecked: bool) -> Optional[Tuple[str, bool]]:
    """The first run check of the LVS block ``lvs`` whose value does not count,
    as ``(check, absent)``, or None when every one counts. ``version`` is the
    ``checks`` block's own ``version``.

    In the order of :data:`LVS_RUN_CHECKS`, each check must be present, or it
    is the one, with ``absent`` true. Its value counts when it is the one
    passing value: :data:`NOT_NEEDED` for ``view_xor`` when the block's
    ``view`` is the state layout, where no view file was written, and
    :data:`PASS` for every other check. When ``ports_may_go_unchecked`` is
    true, :data:`NOT_CHECKED` counts too, but only for ``ports``, only under
    checks :data:`CHECKS_VERSION_NOT_CHECKED`, and only when the block's
    ``deck_tree_sha256`` is a deck tree :data:`PORTS_NOT_CHECKED` lists. Any
    other value is the one, with ``absent`` false. Then a key that is not a run
    check is the one, the first in sorted order, with ``absent`` false.

    ``ports_may_go_unchecked`` has no default and must be a bool. Admission of
    ready dies passes true. The die flow's CDL gate passes false, because its
    rows cannot yet say that a die's ports went unchecked: the record would
    need ``not_checked:ports`` beside its cdl rows.

    This is the rule alone. Whether the verdict, the view and the deck count,
    and the words each reader writes for the check it gets back, stay the
    reader's own. A ``run_checks`` that is not a mapping reads as an empty
    one.
    """
    if not isinstance(ports_may_go_unchecked, bool):
        raise TypeError("ports_may_go_unchecked must be a bool, not "
                        f"{type(ports_may_go_unchecked).__name__}")
    lvs = lvs if isinstance(lvs, Mapping) else {}
    run_checks = lvs.get("run_checks")
    run_checks = run_checks if isinstance(run_checks, Mapping) else {}
    deck_tree = lvs.get("deck_tree_sha256")
    for check in LVS_RUN_CHECKS:
        if check not in run_checks:
            return check, True
        value = run_checks[check]
        if (ports_may_go_unchecked and value == NOT_CHECKED and check == "ports"
                and version == CHECKS_VERSION_NOT_CHECKED
                and isinstance(deck_tree, str) and deck_tree in PORTS_NOT_CHECKED):
            continue
        if value != (NOT_NEEDED if (check, lvs.get("view")) == ("view_xor", "state_gds") else PASS):
            return check, False
    other = sorted(str(key) for key in run_checks if key not in LVS_RUN_CHECKS)
    return (other[0], False) if other else None

#: The KLayout checks whose counts level 1 reads, in the order it reports them,
#: and per PDK name the runset each must have run, relative to the run's PDK
#: root. The deck content is named here, not digested. The ihp-sg13cmos5l rows
#: of these tables were read off its decks at IHP-Open-PDK 22f43352; they are its
#: own, not the ihp-sg13g2 rows, and move with its decks alone.
DRC_PARTS = ("drc", "density", "antenna")
DRC_RUNSETS = MappingProxyType({
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
})
#: The options each check may run with, per PDK name: the thread key of
#: :data:`DRC_THREADS` at a positive int, and per check the other keys it may
#: take, each with the values it may take. A key not listed, or a value not
#: allowed, is a run the counts do not speak for.
DRC_THREADS = MappingProxyType({"ihp-sg13g2": "threads", "gf180mcuD": "thr",
                                "ihp-sg13cmos5l": "threads"})
DRC_OPTIONS = MappingProxyType({
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
})
#: Per PDK name and check, the keys of :data:`DRC_OPTIONS` a run must carry. A
#: deck that reads an absent key as a setting of its own needs every one: the
#: gf180mcuD decks read an absent switch as off and an absent ``run_mode`` as
#: flat, so their rows are exact. The ihp-sg13g2 main deck reads an absent
#: ``run_mode`` as deep, which its row allows. The ihp-sg13cmos5l main deck
#: leaves an absent one unset: the engine runs deep, but every layer is read
#: merged as in flat, a setting its row does not allow, so it needs the key; its
#: density deck sets deep itself and its antenna deck reads an absent one as
#: deep. Both main decks read an absent ``no_recommended`` as the recommended
#: rules run, which neither row allows, and neither requires the key all the
#: same: that run checks every rule the allowed one does and more, so its zero
#: count speaks for the allowed run.
DRC_REQUIRED = MappingProxyType({
    "ihp-sg13g2": MappingProxyType({"drc": (), "density": (), "antenna": ()}),
    "gf180mcuD": MappingProxyType({
        "drc": ("feol", "beol", "dummy", "dummy_no_sub_prev", "offgrid", "conn_drc", "wedge",
                "run_mode"),
        "density": ("run_mode",),
        "antenna": ("run_mode",),
    }),
    "ihp-sg13cmos5l": MappingProxyType({"drc": ("run_mode",), "density": (), "antenna": ()}),
})

#: Level 1 as admission records it on the row it passes: the two facts, their
#: three results, and the detail of a die built elsewhere.
LEVEL1_FACT = "level1"
LEVEL1_DETAIL_FACT = "level1_detail"
LEVEL1_MET = "met"
LEVEL1_NOT_VERIFIED = "not_verified"
LEVEL1_NOT_CLAIMED = "not_claimed"
LEVEL1_THIRD_PARTY = "third_party"
#: The tokens of a die of the die flow's own whose level 1 is not verified. A
#: token with a prefix below is ``<prefix>:<data>``; the data keeps only
#: ``[A-Za-z0-9_.-]``.
NO_CHECKS_RECORDED = "no_checks_recorded"
LEVEL1_ABSENT = "level1_absent"
FIELD_ABSENT = "field_absent"
CHECK_NOT_ON_LAYOUT = "check_not_on_layout"
CHECK_NOT_OFFICIAL = "check_not_official"
CHECK_NOT_ZERO = "check_not_zero"
LVS_NOT_RUN_TOKEN = "lvs_not_run"
LVS_UNREADABLE_TOKEN = "lvs_unreadable"
LVS_VIEW_NOT_COUNTED = "lvs_view_not_counted"
LVS_DECK_NOT_OFFICIAL = "lvs_deck_not_official"
LVS_RUN_CHECK = "lvs_run_check"
LVS_DIFFERS = "lvs_differs"
#: All of them, for a reader that keys on a token.
LEVEL1_TOKENS = (NO_CHECKS_RECORDED, LEVEL1_ABSENT, FIELD_ABSENT, CHECK_NOT_ON_LAYOUT,
                 CHECK_NOT_OFFICIAL, CHECK_NOT_ZERO, LVS_NOT_RUN_TOKEN, LVS_UNREADABLE_TOKEN,
                 LVS_VIEW_NOT_COUNTED, LVS_DECK_NOT_OFFICIAL, LVS_RUN_CHECK, LVS_DIFFERS)
#: The gap a met level 1 carries after its view, ``<prefix>:<check>``, for each
#: run check that read :data:`NOT_CHECKED`. A gap is not a reason level 1 is not
#: verified, so it is not in :data:`LEVEL1_TOKENS`.
NOT_CHECKED_TOKEN = "not_checked"

# The die LVS verdict as the build row records it: the die's own layout against
# its own netlist, from the build that made it. No assembly check reads as one.
LVS_FACT = "lvs"
LVS_SOURCE_FACT = "lvs_source"
#: The comparison view as a fact of its own, read from the same receipt the
#: provenance field names.
LVS_VIEW_FACT = "lvs_view"
#: The deck compared the layout with the netlist and they matched.
LVS_MATCHED = "0"
#: The deck compared them and they differed.
LVS_DIFFERED = "1"
#: Nothing could be read: no metric, no log, no sentinel line, or the two
#: disagreeing. A broken deck must never read as a broken die.
LVS_UNREADABLE = "unreadable"
#: The step was not asked for. Not a verdict, and like a matched verdict it
#: leaves green as it was.
LVS_NOT_RUN = "not_run"
#: Every comparison view that normalization can prove, named as its receipt
#: names it: the fill expansion, the unification of the duplicate masters a
#: stream-out name collision left, both passes, and the state layout itself when
#: neither changed anything. The vocabulary is closed on purpose: a verdict over
#: a view nobody here knows is not a verdict this format can read.
LVS_VIEWS = ("fill_flattened", "fill_flattened+masters_unified",
             "masters_unified", "state_gds")
LVS_VIEW_UNRECORDED = "unrecorded"
