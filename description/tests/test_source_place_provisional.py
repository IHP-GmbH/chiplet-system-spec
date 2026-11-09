# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The provisional elaboration interposer-pnr's place reads: an unplaced source
elaborated with each unplaced die at a provisional position, marked so no
reader takes it for a placed design."""
from __future__ import annotations

import pytest

from chiplet_system_description._cfio import cfio
from chiplet_system_description.source import SourceError
from chiplet_system_description.source.elaborate import (PROVISIONAL_METADATA,
                                                         elaborate_provisional,
                                                         elaborate_source)

from tests.source_dies import SOURCE, write_die, write_source
from tests.test_source_plan import LS, PADDED, Resolver
from tests.test_source_unplaced import PROJECT

#: Example 8's rails and edges over the fixture dies (:data:`PADDED`), drawn on
#: a sheet: neither die placed, no carrier size, U1.CLK held on north by its
#: Edge field and the other external entries on no edge yet.
EXAMPLE8 = (PADDED.replace('format_version: "1.1"', 'format_version: "1.3"')
            .replace(", size: [2000, 3200]", "")
            .replace("at: [1000, 2200], rotation: 0, ", "")
            .replace("at: [1000, 1000], rotation: 180, ", "")
            .replace("      south: [U2.STATUS]\n      west: [V12]\n      east: [V33, GND]\n", "")
            .replace("flow:\n  packaging",
                     "unplaced:\n  pins: [U2.STATUS, V12, V33, GND]\nflow:\n  packaging"))


def _example8(tmp_path, text=EXAMPLE8):
    path = write_source(tmp_path, text)
    write_die(tmp_path / "dies" / "DRIVER", "DRIVER", LS)
    return path


def _components(document):
    return {c["id"]: c for c in document["components"]}


def _nets(document):
    return {n["name"] for n in document["netlist"]["nets"]}


def test_example_8_unplaced_elaborates_provisionally(tmp_path):
    resolver = Resolver()
    document, extras = elaborate_provisional(_example8(tmp_path), resolver)
    assert list(document)[:2] == ["format_version", "_metadata"]
    assert document["_metadata"] == {"finalize_required": True,
                                     "finalizer": "packaging-flow place"}
    assert extras.provisional == ("U1", "U2")
    components = _components(document)
    # One row, file order, rotation 0, edge to edge from the origin; the
    # carrier holds them and carries no pad, since its size is not known.
    assert [(components[i]["position"]["x"], components[i]["position"]["y"])
            for i in ("U1", "U2")] == [(500.0, 500.0), (1500.0, 500.0)]
    carrier = components["interposer"]
    assert (carrier["dimensions"]["width"], carrier["dimensions"]["height"]) == (2000.0, 1000.0)
    assert not carrier.get("io_pads") and not [c for c in resolver.calls if c[0] == "io_pads"]


def test_the_pins_on_no_edge_are_on_no_net(tmp_path):
    document, _ = elaborate_provisional(_example8(tmp_path), Resolver())
    nets = _nets(document)
    # U2.STATUS waits in unplaced.pins: no tie. U1.CLK is on an edge: tied.
    assert "U2_PAD_STATUS_0" not in nets and "U1_PAD_CLK_0" in nets
    # The rails in unplaced.pins keep their nets between the dies, and so do
    # the links.
    assert {"GND", "V33", "d2d_f0_n"} <= nets


def test_every_reader_refuses_it_unless_it_allows_intermediate_files(tmp_path):
    document, _ = elaborate_provisional(_example8(tmp_path), Resolver())
    text = cfio.dumps(document)
    with pytest.raises(cfio.ChipletFormatError, match="intermediate"):
        cfio.loads(text)
    assert cfio.loads(text, allow_intermediate=True)["_metadata"] == PROVISIONAL_METADATA


def test_the_plain_elaboration_still_refuses_the_unplaced_file(tmp_path):
    with pytest.raises(SourceError):
        elaborate_source(_example8(tmp_path), Resolver())


def test_a_die_the_file_places_stays_and_the_others_sit_above_it(tmp_path):
    text = (EXAMPLE8.replace("die: dies/ECHO, method", "die: dies/ECHO, at: [1500, 600], "
                                                       "rotation: 90, method")
            .replace("top_cell: CF_CARRIER}", "top_cell: CF_CARRIER, size: [4000, 4000]}"))
    resolver = Resolver()
    document, extras = elaborate_provisional(_example8(tmp_path, text), resolver)
    components = _components(document)
    assert extras.provisional == ("U1",)
    assert (components["U2"]["position"]["x"], components["U2"]["position"]["y"]) == (1500.0,
                                                                                    600.0)
    # U2 reaches up to 1100, so the row of the unplaced starts there.
    assert (components["U1"]["position"]["x"], components["U1"]["position"]["y"]) == (500.0,
                                                                                    1600.0)
    # The stated carrier stays, and the edge it states gets its pads.
    assert components["interposer"]["dimensions"]["width"] == 4000.0
    assert [c[2:] for c in resolver.calls if c[0] == "io_pads"] == [
        ((4000.0, 4000.0), (("north", ("U1_PAD_CLK_0",)),))]


def _sized(size):
    return EXAMPLE8.replace("top_cell: CF_CARRIER}", f"top_cell: CF_CARRIER, size: {size}}}")


def _inside(document):
    components = _components(document)
    carrier = components.pop("interposer")["dimensions"]

    def box(c):
        w, h = c["dimensions"]["width"], c["dimensions"]["height"]
        return (h, w) if c.get("rotation", {}).get("z") in (90.0, 270.0) else (w, h)

    return all(c["position"]["x"] - box(c)[0] / 2 >= 0
               and c["position"]["y"] - box(c)[1] / 2 >= 0
               and c["position"]["x"] + box(c)[0] / 2 <= carrier["width"]
               and c["position"]["y"] + box(c)[1] / 2 <= carrier["height"]
               for c in components.values())


def test_on_a_stated_carrier_with_no_room_above_the_row_runs_from_the_origin(tmp_path):
    text = _sized("[2000, 3200]").replace("die: dies/ECHO, method",
                                          "die: dies/ECHO, at: [1000, 2700], method")
    document, extras = elaborate_provisional(_example8(tmp_path, text), Resolver())
    components = _components(document)
    assert extras.provisional == ("U1",)
    assert (components["U1"]["position"]["x"], components["U1"]["position"]["y"]) == (500.0,
                                                                                    500.0)
    assert _inside(document)


def test_on_a_stated_carrier_a_die_past_the_east_edge_is_drawn_back(tmp_path):
    document, _ = elaborate_provisional(_example8(tmp_path, _sized("[1500, 3200]")), Resolver())
    components = _components(document)
    assert [components[i]["position"]["x"] for i in ("U1", "U2")] == [500.0, 1000.0]
    assert _inside(document)


def test_a_die_larger_than_the_stated_carrier_is_carrier_too_small(tmp_path):
    with pytest.raises(SourceError) as exc:
        elaborate_provisional(_example8(tmp_path, _sized("[800, 3200]")), Resolver())
    assert [f.code for f in exc.value.findings] == ["carrier_too_small"] * 2


def test_on_a_stated_carrier_a_die_that_fits_only_turned_sits_turned(tmp_path):
    path = _example8(tmp_path, _sized("[1500, 4000]"))
    write_die(tmp_path / "dies" / "DRIVER", "DRIVER", LS, box=(2500.0, 1000.0))
    document, _ = elaborate_provisional(path, Resolver())
    components = _components(document)
    assert [components[i]["rotation"]["z"] for i in ("U1", "U2")] == [90.0, 0.0]
    assert (components["U1"]["position"]["x"], components["U1"]["position"]["y"]) == (500.0,
                                                                                    1250.0)
    assert _inside(document)


def test_a_placed_source_elaborates_provisionally_as_it_stands(tmp_path):
    path = write_source(tmp_path, SOURCE)
    document, extras = elaborate_provisional(path, Resolver())
    system, _ = elaborate_source(path, Resolver())
    assert extras.provisional == ()
    placed = {i.id: (i.position.x, i.position.y) for i in system.instances}
    assert {k: (c["position"]["x"], c["position"]["y"]) for k, c in
            _components(document).items() if k != "interposer"} == placed


def test_a_kept_die_inside_the_first_quadrant_is_covered(tmp_path):
    """U2 at [1000, 1000] on a 1000 um die stays inside, so the row is placed."""
    from tests.test_source_unplaced import UNPLACED
    path = write_source(tmp_path, UNPLACED)
    document, _ = elaborate_provisional(path, Resolver())
    assert _components(document)["U2"]["position"]["x"] == 1000.0


@pytest.mark.parametrize("at", ["[0, 0]", "[-3000, 200]"])
def test_an_unsized_carrier_refuses_a_kept_die_outside_the_first_quadrant(tmp_path, at):
    """A kept die centred on the origin, or left of it, leaves the first
    quadrant, so an unsized carrier cannot be the max corner."""
    from tests.test_source_unplaced import UNPLACED
    path = write_source(tmp_path, UNPLACED.replace("at: [1000, 1000]", f"at: {at}"))
    with pytest.raises(SourceError) as exc:
        elaborate_provisional(path, Resolver())
    assert [f.code for f in exc.value.findings] == ["kept_outside"]
    assert "move it to non-negative coordinates or size the carrier" in str(exc.value)


def test_a_file_with_no_dies_does_not_elaborate(tmp_path):
    with pytest.raises(SourceError) as exc:
        elaborate_provisional(write_source(tmp_path, PROJECT), Resolver())
    assert [f.code for f in exc.value.findings] == ["no_dies"]
