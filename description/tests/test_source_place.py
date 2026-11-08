# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Placement written into a source: the placement document checked against the
file, the carrier size and the edges by the one rule, and the bytes the
patcher writes, the same every time."""
from __future__ import annotations

import hashlib

import pytest

from chiplet_system_description.source import check_text
from chiplet_system_description.source.patch import EditRefused, SetEntries, SetSize
from chiplet_system_description.source.place import _Die, pin_point, placement_edits
from chiplet_system_description.source.plan import nearest_edge, plan

from tests.source_dies import ECHO, SOURCE, write_die, write_source
from tests.test_source_unplaced import PROJECT, UNPLACED

#: The wire-bond profile's numbers, as packaging-flow resolves them
#: (profiles/intm4tm2_wire_bond_v0.json): a pad band is 100 + 80 + 200 um.
PROFILE = {"pad_size_um": 80, "pitch_um": 150, "edge_clearance_um": 100, "die_keepout_um": 200}
BAND = 380
#: :data:`UNPLACED` with neither die placed.
BOTH = UNPLACED.replace("at: [1000, 1000], rotation: 180, ", "")
#: :data:`UNPLACED` on a stated carrier, U2 placed by the file.
SIZED = UNPLACED.replace("top_cell: CF_CARRIER}", "top_cell: CF_CARRIER, size: [3000, 3200]}")
#: :data:`SOURCE` with neither die placed, no carrier size and no external block.
BARE = (SOURCE.replace('format_version: "1.1"', 'format_version: "1.3"')
        .replace(", size: [2000, 3200]", "")
        .replace("at: [1000, 2200], rotation: 0, ", "")
        .replace("at: [1000, 1000], rotation: 180, ", ""))


def _doc(data, dies, **fields):
    doc = {"format": "interposer_pnr.placement", "version": "1.0",
           "file_sha256": hashlib.sha256(data).hexdigest(),
           "dies": [{"id": i, "at": list(at), "rotation": r} for i, at, r in dies],
           "reasons": [{"seed": ["U1", "U2"]}]}
    doc.update(fields)
    return doc


def _place(tmp_path, text, dies, *, profile=PROFILE, **kw):
    path = write_source(tmp_path, text)
    return placement_edits(path, _doc(path.read_bytes(), dies), profile, **kw)


def _refused(tmp_path, text, dies, **kw):
    with pytest.raises(EditRefused) as exc:
        _place(tmp_path, text, dies, **kw)
    return [(f.code, f.port) for f in exc.value.findings]


def _system(placed, path="system.chiplet"):
    """The placed file read at tier 0, from ``path`` (where its dies resolve)."""
    source, findings = check_text(placed.text.decode(), path)
    assert findings == []
    return source


def _row(placed, kind, subject):
    return next(r for r in placed.reasons if r["kind"] == kind and r["subject"] == subject)


# --- the document -------------------------------------------------------------
def test_a_placement_bound_to_other_bytes_is_stale(tmp_path):
    path = write_source(tmp_path, BOTH)
    doc = _doc(path.read_bytes() + b"# edited\n", [("U1", (0, 0), 0), ("U2", (0, 1200), 0)])
    with pytest.raises(EditRefused) as exc:
        placement_edits(path, doc, PROFILE)
    assert [f.code for f in exc.value.findings] == ["placement_stale"]


@pytest.mark.parametrize("change", [
    lambda d: [d],
    lambda d: {**d, "format": "interposer_pnr.board_revision"},
    lambda d: {**d, "version": 1},
    lambda d: {**d, "units": "mm"},
    lambda d: {**d, "frame": "pcb"},
    lambda d: {**d, "file_sha256": d["file_sha256"].upper()},
    lambda d: {**d, "dies": []},
    lambda d: {**d, "dies": [{"id": "U1", "at": [True, 0], "rotation": 0}]},
    lambda d: {**d, "dies": [{"id": "U1", "at": [0], "rotation": 0}]},
    lambda d: {**d, "dies": [{"id": "U1", "at": [0, float("nan")], "rotation": 0}]},
    lambda d: {**d, "dies": [{"id": "U1", "at": [0, 0]}]},
    lambda d: {**d, "dies": [{"id": 1, "at": [0, 0], "rotation": 0}]},
    lambda d: {**d, "dies": d["dies"] + d["dies"][:1]},
])
def test_a_malformed_placement_is_refused(tmp_path, change):
    path = write_source(tmp_path, BOTH)
    doc = change(_doc(path.read_bytes(), [("U1", (0, 0), 0), ("U2", (0, 1200), 0)]))
    with pytest.raises(EditRefused) as exc:
        placement_edits(path, doc, PROFILE)
    assert [f.code for f in exc.value.findings] == ["placement_malformed"]


def test_the_placement_places_exactly_the_files_dies(tmp_path):
    assert _refused(tmp_path, BOTH, [("U1", (0, 0), 0), ("U9", (0, 1200), 0)]) == [
        ("placement_dies_differ", None), ("placement_dies_differ", None)]


@pytest.mark.parametrize("rotation", [45, True, "90", -90])
def test_a_rotation_off_the_four_is_refused(tmp_path, rotation):
    assert _refused(tmp_path, BOTH, [("U1", (0, 0), rotation), ("U2", (0, 1200), 0)]) == [
        ("placement_bad_rotation", None)]


def test_a_file_with_an_external_block_needs_the_resolved_profile(tmp_path):
    dies = [("U1", (0, 0), 0), ("U2", (0, 1200), 0)]
    assert _refused(tmp_path, BOTH, dies, profile=None) == [("profile_malformed", None)]
    short = {k: v for k, v in PROFILE.items() if k != "pitch_um"}
    assert _refused(tmp_path, BOTH, dies, profile=short) == [("profile_malformed", None)]


def test_a_file_with_no_dies_has_nothing_to_place(tmp_path):
    assert _refused(tmp_path, PROJECT, [("U1", (0, 0), 0)]) == [("not_a_source", None)]


def test_the_placers_reasons_come_back_untouched(tmp_path):
    path = write_source(tmp_path, BOTH)
    doc = _doc(path.read_bytes(), [("U1", (0, 0), 0), ("U2", (0, 1200), 0)])
    assert placement_edits(path, doc, PROFILE).placer_reasons is doc["reasons"]


# --- translation and the carrier size ---------------------------------------
def test_with_no_external_block_the_size_is_the_dies_on_the_grid(tmp_path):
    placed = _place(tmp_path, BARE, [("U1", (-7000, 40), 0), ("U2", (-7000, 1240), 90)],
                    profile=None)
    source = _system(placed, tmp_path / "system.chiplet")
    # The bounding box (1000 x 2200) moves to the origin: no pad, no margin.
    assert source.carrier.size == (1000, 2200) and placed.size == (1000.0, 2200.0)
    assert [(d.at, d.rotation) for d in source.dies] == [((500, 500), 0), ((500, 1700), 90)]
    assert _row(placed, "translation", "dies")["by"] == [7500.0, 460.0]
    assert _row(placed, "carrier_size", "width")["set_by"] == "die_extent"
    # Placed, the file no longer needs 1.3 and passes tier 1 as a placed file.
    assert source.data["format_version"] == "1.1"
    assert plan(source).refusals == ()


def test_the_size_is_on_the_50_um_grid(tmp_path):
    path = write_source(tmp_path, BARE)
    write_die(tmp_path / "dies" / "ECHO", "ECHO", ECHO, box=(1013.5, 1000.0))
    placed = placement_edits(path, _doc(path.read_bytes(),
                                        [("U1", (0, 0), 0), ("U2", (0, 1200), 0)]), None)
    assert placed.size == (1050.0, 2200.0)
    assert _system(placed).dies[1].at == (506.75, 1700)


def test_the_size_holds_the_dies_and_the_pad_bands(tmp_path):
    placed = _place(tmp_path, BOTH, [("U1", (0, 1200), 0), ("U2", (0, 0), 0)])
    source = _system(placed)
    margins = {r["subject"]: r["um"] for r in placed.reasons if r["kind"] == "margin"}
    # Every margin is the clearance or the band, and the dies sit at the west
    # and south ones.
    assert set(margins.values()) <= {100.0, float(BAND)}
    xs = [d.at[0] - 500 for d in source.dies]
    ys = [d.at[1] - 500 for d in source.dies]
    assert min(xs) == margins["west"] and min(ys) == margins["south"]
    width, height = source.carrier.size
    assert width % 50 == 0 and height % 50 == 0
    assert width >= max(xs) + 1000 + margins["east"]
    assert height >= max(ys) + 1000 + margins["north"]
    # Every edge that carries pads has its band.
    for edge in source.external.edges:
        assert margins[edge.edge] == BAND


def _wide(tmp_path, edge, count):
    """One die serving 16 signal outputs, ``count`` of them held on ``edge``
    and the rest alone, with neither the die nor the carrier sized."""
    ports = [(f"S{i}", "OUTPUT", "SIGNAL", f"S{i}", 1) for i in range(16)]
    write_die(tmp_path / "dies" / "WIDE", "WIDE", ports)
    held = ", ".join(f"U1.S{i}" for i in range(count))
    text = (f'format_version: "1.3"\n_metadata:\n  finalize_required: true\n'
            f'  finalizer: "packaging-flow run"\nassembly:\n  name: wide\n'
            f'system:\n  carrier: {{technology: intm4tm2, top_cell: CF_CARRIER}}\n'
            f'  dies:\n    - {{id: U1, die: dies/WIDE, method: cupillar_opt1}}\n'
            f'  alone:\n    - "U1.S[{count}..15]"\n'
            f'  external:\n    profile: intm4tm2_wire_bond_v0\n    edges:\n'
            f'      {edge}: [{held}]\n')
    path = tmp_path / "system.chiplet"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("edge", ["north", "south", "west", "east"])
def test_a_pad_row_longer_than_the_dies_sets_its_axis(tmp_path, edge):
    path = _wide(tmp_path, edge, 12)
    placed = placement_edits(path, _doc(path.read_bytes(), [("U1", (0, 0), 0)]), PROFILE)
    axis = "width" if edge in ("north", "south") else "height"
    row = _row(placed, "carrier_size", axis)
    # 12 pads at a 150 um pitch, 80 um each, 100 um free at each end, on the grid.
    assert (row["set_by"], row["edge"], row["need_um"], row["um"]) == (
        "pad_row", edge, 1930.0, 1950.0)
    other = _row(placed, "carrier_size", "height" if axis == "width" else "width")
    # Across: the die, its band on the padded edge, the clearance on the other.
    assert (other["set_by"], other["um"]) == ("die_extent", 1500.0)
    assert _system(placed).external.edges[0].edge == edge


def test_a_row_with_neighbouring_pads_leaves_a_pitch_more_at_each_end(tmp_path):
    path = _wide(tmp_path, "north", 12)
    text = path.read_text().replace('    - "U1.S[12..15]"\n', '    - "U1.S[13..15]"\n')
    path.write_text(text.replace("      north:", "      west: [U1.S12]\n      north:"))
    placed = placement_edits(path, _doc(path.read_bytes(), [("U1", (0, 0), 0)]), PROFILE)
    row = _row(placed, "carrier_size", "width")
    assert (row["set_by"], row["need_um"], row["um"]) == ("pad_row", 2230.0, 2250.0)


def test_a_stated_size_the_dies_do_not_fit_is_too_small(tmp_path):
    text = SIZED.replace("size: [3000, 3200]", "size: [1500, 1500]")
    assert {code for code, _ in _refused(
        tmp_path, text, [("U1", (1000, 2200), 0), ("U2", (1000, 1000), 180)])} == {
        "carrier_too_small"}


def test_a_stated_size_keeps_positions_as_given(tmp_path):
    placed = _place(tmp_path, SIZED, [("U1", (1000, 2200), 0), ("U2", (1000, 1000), 180)])
    source = _system(placed)
    assert source.carrier.size == (3000, 3200)
    assert [d.at for d in source.dies] == [(1000, 2200), (1000, 1000)]
    assert not any(r["kind"] == "translation" for r in placed.reasons)
    assert _row(placed, "carrier_size", "width")["set_by"] == "stated"


def test_more_pads_than_an_edge_holds_is_edge_full_naming_each_to_move(tmp_path):
    path = _wide(tmp_path, "north", 12)
    text = path.read_text().replace("top_cell: CF_CARRIER}",
                                    "top_cell: CF_CARRIER, size: [1500, 1500]}")
    path.write_text(text)
    with pytest.raises(EditRefused) as exc:
        placement_edits(path, _doc(path.read_bytes(), [("U1", (750, 600), 0)]), PROFILE)
    # (1500 - 80 - 2 * 100) / 150 + 1: nine fit, the last three move.
    assert [(f.code, f.port) for f in exc.value.findings] == [
        ("edge_full", "U1.S9"), ("edge_full", "U1.S10"), ("edge_full", "U1.S11")]


# --- kept and again -------------------------------------------------------------
def test_a_die_the_file_places_keeps_its_place_and_the_reason_says_so(tmp_path):
    placed = _place(tmp_path, SIZED, [("U1", (1000, 2200), 0), ("U2", (2000, 1000), 0)])
    assert [d.at for d in _system(placed).dies] == [(1000, 2200), (1000, 1000)]
    kept = _row(placed, "die", "U2")
    assert (kept["why"], kept["placement_at"], kept["rotation"]) == (
        "kept", [2000.0, 1000.0], 180)
    assert _row(placed, "die", "U1")["why"] == "placed"


def test_again_takes_the_placements_position_for_every_die(tmp_path):
    placed = _place(tmp_path, SIZED, [("U1", (1000, 2200), 0), ("U2", (2000, 1000), 0)],
                    again=True)
    source = _system(placed)
    assert [(d.at, d.rotation) for d in source.dies] == [((1000, 2200), 0), ((2000, 1000), 0)]
    assert _row(placed, "die", "U2")["why"] == "re-placed"


def test_with_no_size_a_kept_die_moves_with_the_others(tmp_path):
    placed = _place(tmp_path, UNPLACED, [("U1", (1000, 2200), 0), ("U2", (1000, 1000), 180)])
    source = _system(placed)
    (x1, y1), (x2, y2) = (d.at for d in source.dies)
    # The arrangement is the file's; the frame is the margins'.
    assert (x1 - x2, y1 - y2) == (0, 1200)
    assert _row(placed, "die", "U2")["why"] == "kept"
    assert _row(placed, "translation", "dies")["by"] == [x2 - 1000, y2 - 1000]


def test_a_kept_die_the_placement_lands_on_is_kept_overlap(tmp_path):
    assert _refused(tmp_path, SIZED, [("U1", (1000, 1500), 0), ("U2", (1000, 1000), 180)]) == [
        ("kept_overlap", None)]


# --- edges ------------------------------------------------------------------------
def test_entries_go_to_the_nearest_edge_in_projection_then_entry_order(tmp_path):
    text = UNPLACED.replace("top_cell: CF_CARRIER}", "top_cell: CF_CARRIER, size: [2000, 3200]}")
    placed = _place(tmp_path, text, [("U1", (1000, 2200), 0), ("U2", (1000, 1000), 180)])
    # Both dies are nearest west (a tie goes west); U2 is lower, so its entry
    # leads, then U1's in the order unplaced.pins writes them.
    assert placed.edits[-2:] == (SetEntries("west", ("U2.STATUS", "U1.CLK", "VDD", "VSS")),
                                 SetEntries("unplaced", ()))
    reordered = text.replace("pins: [U2.STATUS, U1.CLK, VDD, VSS]",
                             "pins: [VSS, U1.CLK, U2.STATUS, VDD]")
    again = _place(tmp_path, reordered, [("U1", (1000, 2200), 0), ("U2", (1000, 1000), 180)])
    assert again.edits[-2:] == (SetEntries("west", ("U2.STATUS", "VSS", "U1.CLK", "VDD")),
                                SetEntries("unplaced", ()))


def test_the_rule_is_the_planners(tmp_path):
    """On a stated carrier the rule reads the carrier; with none it reads the
    dies' bounding box, and only the edges it picks get a band."""
    placed = _place(tmp_path, BOTH, [("U1", (0, 1200), 0), ("U2", (0, 0), 0)])
    source = _system(placed, tmp_path / "system.chiplet")
    topology = plan(source)
    at = {d.id: d.at for d in source.dies}
    box = (min(x for x, _ in at.values()) - 500, min(y for _, y in at.values()) - 500)
    span = (1000, 2200)
    for edge in source.external.edges:
        for entry in edge.entries:
            ref = entry if not isinstance(entry, str) else next(
                r for r in source.rails if r.name == entry).ports[0]
            die = next(d for d in source.dies if d.id == ref.instance)
            x, y = pin_point(_Die(die.id, 0, die.at, die.rotation, (0, 0), "placed"),
                             topology.folders[die.id], ref.port)
            assert nearest_edge((x - box[0], y - box[1]), span) == edge.edge
    # The fixture dies have every pin along their south side; mounted flip
    # chip, U2.STATUS lands at the east end of U2's row and goes east.
    rows = {e.edge: [str(getattr(x, "port", x)) for x in e.entries]
            for e in source.external.edges}
    assert rows == {"west": ["CLK", "VDD", "VSS"], "east": ["STATUS"]}
    margins = {r["subject"]: r["um"] for r in placed.reasons if r["kind"] == "margin"}
    assert margins == {"north": 100.0, "south": 100.0, "west": float(BAND),
                       "east": float(BAND)}


# U2.VDD's pins sit 400 um below U2's centre, which is 1000 um from the south
# edge and 1000 um from the east: its pin is nearest south.
@pytest.mark.parametrize("ports, edge", [("[U1.VDD, U2.VDD]", "west"),
                                         ("[U2.VDD, U1.VDD]", "south")])
def test_a_rail_goes_by_the_first_die_it_serves(tmp_path, ports, edge):
    text = SIZED.replace("ports: [U1.VDD, U2.VDD]", f"ports: {ports}")
    placed = _place(tmp_path, text, [("U1", (1000, 2200), 0), ("U2", (2000, 1000), 0)],
                    again=True)
    assert _row(placed, "edge", "VDD")["edge"] == edge
    assert _row(placed, "edge", "VSS")["edge"] == "west"
    assert _row(placed, "edge", "U2.STATUS")["edge"] == "east"


@pytest.mark.parametrize("row", ["[U1.CLK]", "[U1.CLK,]", "[U1.CLK, ]"])
def test_an_entry_on_an_edge_is_held_and_new_ones_follow_it(tmp_path, row):
    text = (SIZED.replace("    profile: intm4tm2_wire_bond_v0\n",
                          "    profile: intm4tm2_wire_bond_v0\n    edges:\n"
                          f"      east: {row}\n")
            .replace("pins: [U2.STATUS, U1.CLK, VDD, VSS]", "pins: [U2.STATUS, VDD, VSS]"))
    placed = _place(tmp_path, text, [("U1", (1000, 2200), 0), ("U2", (2000, 1000), 0)],
                    again=True)
    rows = {e.edge: e.entries for e in _system(placed).external.edges}
    assert [str(getattr(e, "port", e)) for e in rows["east"]] == ["CLK", "STATUS"]
    assert _row(placed, "edge", "U1.CLK")["why"] == "held"


#: One die with a pin near each side, die-local (x, y) um on a 1000 um die.
#: Mounted flip chip, die-local west lands east and east lands west.
COMPASS_PLACES = {"PAD_N1_0": (300, 950), "PAD_N2_0": (700, 950), "PAD_S1_0": (500, 50),
                  "PAD_W1_0": (50, 500), "PAD_E1_0": (950, 500)}


def _compass(tmp_path, size=""):
    ports = [(p, "OUTPUT", "SIGNAL", p, 1) for p in ("N1", "N2", "S1", "W1", "E1")]
    write_die(tmp_path / "dies" / "COMPASS", "COMPASS", ports, places=COMPASS_PLACES)
    text = (f'format_version: "1.3"\n_metadata:\n  finalize_required: true\n'
            f'  finalizer: "packaging-flow run"\nassembly:\n  name: compass\n'
            f'system:\n  carrier: {{technology: intm4tm2, top_cell: CF_CARRIER{size}}}\n'
            f'  dies:\n    - {{id: U1, die: dies/COMPASS, method: cupillar_opt1}}\n'
            f'  external:\n    profile: intm4tm2_wire_bond_v0\n'
            f'unplaced:\n  pins: [U1.N1, U1.S1, U1.W1, U1.E1, U1.N2]\n')
    path = tmp_path / "system.chiplet"
    path.write_text(text, encoding="utf-8")
    return path


def _edges(placed):
    return {e.edge: [getattr(x, "port", x) for x in e.entries]
            for e in _system(placed).external.edges}


def test_one_die_s_pins_go_to_the_edges_their_pins_face(tmp_path):
    path = _compass(tmp_path)
    placed = placement_edits(path, _doc(path.read_bytes(), [("U1", (0, 0), 0)]), PROFILE)
    # North in pin projection order (N2 mounts west of N1), not entry order.
    assert _edges(placed) == {"north": ["N2", "N1"], "south": ["S1"], "west": ["E1"],
                              "east": ["W1"]}
    assert _row(placed, "edge", "U1.N2")["position"] == 0


def test_a_turned_die_turns_its_pins_edges(tmp_path):
    path = _compass(tmp_path)
    placed = placement_edits(path, _doc(path.read_bytes(), [("U1", (0, 0), 90)]), PROFILE)
    # A quarter turn counter-clockwise: the north pins face west, N2 lower.
    assert _edges(placed) == {"west": ["N2", "N1"], "east": ["S1"], "south": ["E1"],
                              "north": ["W1"]}


def test_a_stated_size_off_the_grid_that_holds_the_dies_is_kept(tmp_path):
    """The grid rounds a size place chooses, never a stated one."""
    dies = [("U1", (1000, 2200), 0), ("U2", (1000, 1000), 180)]
    # U2 kept at 1000 (500 um to its east side), and a pad on the east edge:
    # the dies need exactly 1500 + 380 um.
    text = SIZED.replace("size: [3000, 3200]", "size: [1880, 3200]")
    placed = _place(tmp_path, text, dies)
    assert placed.size == (1880.0, 3200.0)
    assert _edges(placed)["east"] == ["CLK"]
    narrow = SIZED.replace("size: [3000, 3200]", "size: [1870, 3200]")
    assert _refused(tmp_path / "narrow", narrow, dies) == [("carrier_too_small", None)]


# --- the bytes ----------------------------------------------------------------------
def test_placing_writes_the_same_bytes_every_time(tmp_path):
    dies = [("U1", (0, 1200), 0), ("U2", (0, 0), 90)]
    first = _place(tmp_path / "a", BOTH, dies)
    second = _place(tmp_path / "b", BOTH, dies)
    assert first.text == second.text and first.reasons == second.reasons


def test_the_placed_file_is_the_patchers_bytes(tmp_path):
    placed = _place(tmp_path, BARE, [("U1", (0, 1200), 0), ("U2", (0, 0), 180)], profile=None)
    assert placed.text.decode() == (
        BARE.replace('format_version: "1.3"', 'format_version: "1.1"')
        .replace("top_cell: CF_CARRIER}", "top_cell: CF_CARRIER, size: [1000, 2200]}")
        .replace("die: dies/DRIVER, method",
                 "die: dies/DRIVER, at: [500, 1700], rotation: 0, method")
        .replace("die: dies/ECHO, method",
                 "die: dies/ECHO, at: [500, 500], rotation: 180, method"))
    assert placed.edits[0] == SetSize((1000, 2200))


@pytest.mark.parametrize("again", [False, True])
def test_placing_a_placed_file_again_writes_nothing(tmp_path, again):
    placed = _place(tmp_path, BOTH, [("U1", (0, 1200), 0), ("U2", (0, 0), 90)])
    path = tmp_path / "system.chiplet"
    path.write_bytes(placed.text)
    dies = [(d.id, d.at, d.rotation) for d in _system(placed).dies]
    redo = placement_edits(path, _doc(placed.text, dies), PROFILE, again=again)
    assert redo.edits == () and redo.text == placed.text


def test_bytes_and_a_path_place_alike(tmp_path):
    path = write_source(tmp_path, BOTH)
    data = path.read_bytes()
    doc = _doc(data, [("U1", (0, 1200), 0), ("U2", (0, 0), 0)])
    assert (placement_edits(data, doc, PROFILE, path=path).text
            == placement_edits(path, doc, PROFILE).text)
