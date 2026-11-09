# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""``run_check_not_counted`` is the run-check rule its two readers each wrote.

Two copies of the rule exist before it moves here, and each is copied below as
it stands, as the oracle:

* admission of ready dies in the packaging flow (``ready_die.py``): the
  run-check loop of ``_lvs_token`` and ``_not_performed``;
* the die flow's CDL gate (``eda_views.py``): the run-check loop of
  ``_lvs_gate``.

Over every case below, the function with ``ports_may_go_unchecked`` true,
mapped to admission's tokens, equals admission's loop. With it false, mapped to
the gate's words, it equals the gate's loop: zero cases move. With it true the
gate would move on exactly one kind of case, which the CDL gate left out on
purpose: ``ports`` reading ``not_checked`` under checks 1.1 over a deck tree
that does not check port labels. That difference is pinned too, so the move is
known before the keyword is lifted.
"""
import re
from collections.abc import Mapping

import pytest

from chiplet_format_io.ready_die_names import (CHECKS_VERSION, CHECKS_VERSION_NOT_CHECKED,
                                               FAIL, FIELD_ABSENT, LVS_DECK_TREES,
                                               LVS_RUN_CHECK, LVS_RUN_CHECKS, NOT_CHECKED,
                                               NOT_NEEDED, PASS, PORTS_NOT_CHECKED, XOR_DIFFERS,
                                               XOR_TIMEOUT, run_check_not_counted)

# --- The oracles, as their readers wrote them --------------------------------

_TOKEN_DATA = re.compile(r"[^A-Za-z0-9_.-]")


def _token(prefix, data):
    return f"{prefix}:{_TOKEN_DATA.sub('_', str(data))}"


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _not_performed(check, version, deck_tree):
    return (check == "ports" and version == CHECKS_VERSION_NOT_CHECKED
            and deck_tree in PORTS_NOT_CHECKED)


def _admission_loop(lvs, version):
    """``_lvs_token`` from its run-check loop on. Admission reaches it only with
    a counted view and a ``deck_tree_sha256`` that is a string."""
    view = lvs.get("view")
    run_checks = _mapping(lvs.get("run_checks"))
    for check in LVS_RUN_CHECKS:
        if check not in run_checks:
            return _token(FIELD_ABSENT, f"run_checks.{check}")
        passing = NOT_NEEDED if (check, view) == ("view_xor", "state_gds") else PASS
        if (run_checks[check] == NOT_CHECKED
                and _not_performed(check, version, lvs["deck_tree_sha256"])):
            continue
        if run_checks[check] != passing:
            return _token(LVS_RUN_CHECK, check)
    other = sorted(str(key) for key in run_checks if key not in LVS_RUN_CHECKS)
    if other:
        return _token(LVS_RUN_CHECK, other[0])
    return None


def _gate_loop(lvs):
    """``_lvs_gate`` from its run-check loop on, up to the reference."""
    run_checks = lvs.get("run_checks")
    run_checks = run_checks if isinstance(run_checks, Mapping) else {}
    for check in LVS_RUN_CHECKS:
        passing = NOT_NEEDED if (check, lvs.get("view")) == ("view_xor", "state_gds") else PASS
        if run_checks.get(check) != passing:
            return f"lvs_run_check {check}"
    other = sorted(str(check) for check in run_checks if check not in LVS_RUN_CHECKS)
    if other:
        return f"lvs_run_check {other[0]}"
    return None


# --- Each reader's words over the function -----------------------------------

def _as_admission(lvs, version):
    found = run_check_not_counted(lvs, version, ports_may_go_unchecked=True)
    if found is None:
        return None
    check, absent = found
    return _token(FIELD_ABSENT, f"run_checks.{check}") if absent else _token(LVS_RUN_CHECK, check)


def _as_gate(lvs, version, ports_may_go_unchecked=False):
    found = run_check_not_counted(lvs, version, ports_may_go_unchecked=ports_may_go_unchecked)
    return None if found is None else f"lvs_run_check {found[0]}"


# --- The cases -----------------------------------------------------------------

GF180_TREE = LVS_DECK_TREES["gf180mcuD"][0][0]
SG13G2_TREE = LVS_DECK_TREES["ihp-sg13g2"][0][0]
ABSENT = object()
VALUES = (PASS, NOT_NEEDED, FAIL, NOT_CHECKED, XOR_DIFFERS, XOR_TIMEOUT, None, True, 1,
          "PASS", "pass ", ABSENT)
VERSIONS = (CHECKS_VERSION, CHECKS_VERSION_NOT_CHECKED, None, "1.2", 1.1)
COUNTED_VIEWS = ("state_gds", "fill_flattened")
OTHER_VIEWS = ("masters_unified", None)
TREES = (GF180_TREE, SG13G2_TREE, "0" * 64)


def _passing(view):
    return {check: NOT_NEEDED if (check, view) == ("view_xor", "state_gds") else PASS
            for check in LVS_RUN_CHECKS}


def _set(run_checks, check, value):
    out = dict(run_checks)
    if value is ABSENT:
        del out[check]
    else:
        out[check] = value
    return out


def _run_checks(view):
    """Every run-check block the cases try over ``view``: all counting; one
    check at each value; two checks not counting, to fix the order; keys that
    are no run check; and blocks that are no mapping."""
    base = _passing(view)
    yield base
    for check in LVS_RUN_CHECKS:
        for value in VALUES:
            yield _set(base, check, value)
    for i, first in enumerate(LVS_RUN_CHECKS):
        for second in LVS_RUN_CHECKS[i + 1:]:
            for a, b in ((FAIL, FAIL), (ABSENT, FAIL), (NOT_CHECKED, ABSENT), (NOT_CHECKED, FAIL)):
                yield _set(_set(base, first, a), second, b)
    yield {**base, "zeta": PASS}
    yield {**base, "beta": FAIL, "alpha": PASS}
    yield {**_set(base, "ports", NOT_CHECKED), "alpha": PASS}
    yield {**_set(base, "design_cdl", FAIL), "alpha": PASS}
    yield {check: NOT_CHECKED for check in LVS_RUN_CHECKS}
    yield {}
    for not_a_mapping in (None, [], list(LVS_RUN_CHECKS), "pass", ABSENT):
        yield not_a_mapping


def _block(view, tree, run_checks):
    lvs = {"view": view, "deck_tree_sha256": tree}
    if view is ABSENT:
        del lvs["view"]
    if tree is ABSENT:
        del lvs["deck_tree_sha256"]
    if run_checks is not ABSENT:
        lvs["run_checks"] = run_checks
    return lvs


def _admission_cases():
    for view in COUNTED_VIEWS:
        for tree in TREES:
            for run_checks in _run_checks(view):
                for version in VERSIONS:
                    yield _block(view, tree, run_checks), version


def _gate_cases():
    for view in COUNTED_VIEWS + OTHER_VIEWS + (ABSENT,):
        for tree in TREES + (None, ["list"], ABSENT):
            for run_checks in _run_checks(view if isinstance(view, str) else None):
                for version in VERSIONS:
                    yield _block(view, tree, run_checks), version


def _gate_lets_go(lvs, version):
    """The one kind of case the function counts and the CDL gate does not."""
    run_checks = lvs.get("run_checks")
    return (isinstance(run_checks, dict) and run_checks.get("ports") == NOT_CHECKED
            and version == CHECKS_VERSION_NOT_CHECKED
            and isinstance(lvs.get("deck_tree_sha256"), str)
            and lvs["deck_tree_sha256"] in PORTS_NOT_CHECKED)


# --- The tests -------------------------------------------------------------------

def test_admission_reads_the_function_as_it_read_its_own_loop():
    cases = list(_admission_cases())
    assert len(cases) > 5000
    moved = [(lvs, version) for lvs, version in cases
             if _as_admission(lvs, version) != _admission_loop(lvs, version)]
    assert not moved, f"{len(moved)} cases moved, first {moved[0]}"


def test_the_gate_reads_the_function_with_ports_held_as_it_read_its_own_loop():
    cases = list(_gate_cases())
    assert len(cases) > 5000
    moved = [(lvs, version) for lvs, version in cases if _as_gate(lvs, version) != _gate_loop(lvs)]
    assert not moved, f"{len(moved)} cases moved, first {moved[0]}"


def test_letting_ports_go_moves_the_gate_only_where_admission_lets_them_go():
    moved = []
    for lvs, version in _gate_cases():
        new, old = _as_gate(lvs, version, ports_may_go_unchecked=True), _gate_loop(lvs)
        if new == old:
            continue
        assert _gate_lets_go(lvs, version), (lvs, version, new, old)
        assert old == "lvs_run_check ports", (lvs, version, new, old)
        moved.append((lvs, version, new))
    assert moved, "no case tells the gate's move apart"
    # Where ports goes unchecked, the gate reads on: the next check that does
    # not count, a key that is no run check, or nothing.
    assert {new for _, _, new in moved} >= {None, "lvs_run_check view_receipt",
                                            "lvs_run_check alpha"}


@pytest.mark.parametrize("version,tree,counts", [
    (CHECKS_VERSION_NOT_CHECKED, GF180_TREE, True),
    (CHECKS_VERSION, GF180_TREE, False),
    (CHECKS_VERSION_NOT_CHECKED, SG13G2_TREE, False),
    (CHECKS_VERSION_NOT_CHECKED, None, False),
    (CHECKS_VERSION_NOT_CHECKED, ["list"], False),
])
def test_not_checked_counts_only_for_ports_under_1_1_over_a_listed_tree(version, tree, counts):
    for view in COUNTED_VIEWS:
        for may in (True, False):
            lvs = {"view": view, "deck_tree_sha256": tree,
                   "run_checks": _set(_passing(view), "ports", NOT_CHECKED)}
            assert run_check_not_counted(lvs, version, ports_may_go_unchecked=may) == (
                None if counts and may else ("ports", False))
            for check in LVS_RUN_CHECKS:
                if check != "ports":
                    lvs["run_checks"] = _set(_passing(view), check, NOT_CHECKED)
                    assert run_check_not_counted(lvs, version, ports_may_go_unchecked=may) == (
                        check, False)


def test_view_xor_passes_on_not_needed_only_over_the_state_layout():
    for view, passing, other in (("state_gds", NOT_NEEDED, PASS),
                                 ("fill_flattened", PASS, NOT_NEEDED)):
        for may in (True, False):
            lvs = {"view": view, "run_checks": _passing(view)}
            assert lvs["run_checks"]["view_xor"] == passing
            assert run_check_not_counted(lvs, CHECKS_VERSION, ports_may_go_unchecked=may) is None
            lvs["run_checks"] = _set(_passing(view), "view_xor", other)
            assert run_check_not_counted(lvs, CHECKS_VERSION, ports_may_go_unchecked=may) == (
                "view_xor", False)


def test_an_absent_check_is_told_from_one_that_holds_another_value():
    for may in (True, False):
        lvs = {"view": "state_gds", "run_checks": _set(_passing("state_gds"), "options", ABSENT)}
        assert run_check_not_counted(lvs, CHECKS_VERSION, ports_may_go_unchecked=may) == (
            "options", True)
        lvs["run_checks"]["options"] = None
        assert run_check_not_counted(lvs, CHECKS_VERSION, ports_may_go_unchecked=may) == (
            "options", False)
        for block in ({}, {"run_checks": None}, {"run_checks": []}, None, "lvs"):
            assert run_check_not_counted(block, CHECKS_VERSION, ports_may_go_unchecked=may) == (
                LVS_RUN_CHECKS[0], True)


def test_ports_may_go_unchecked_is_a_bool_named_by_every_caller():
    lvs = {"view": "state_gds", "deck_tree_sha256": GF180_TREE,
           "run_checks": _set(_passing("state_gds"), "ports", NOT_CHECKED)}
    with pytest.raises(TypeError):
        run_check_not_counted(lvs, CHECKS_VERSION_NOT_CHECKED)
    with pytest.raises(TypeError):
        run_check_not_counted(lvs, CHECKS_VERSION_NOT_CHECKED, True)
    for not_a_bool in (1, 0, "true", "", None, [True]):
        with pytest.raises(TypeError, match="must be a bool"):
            run_check_not_counted(lvs, CHECKS_VERSION_NOT_CHECKED, ports_may_go_unchecked=not_a_bool)
