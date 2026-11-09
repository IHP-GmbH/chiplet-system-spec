# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The writer: a whole file from items, and a revision that moves only its tokens."""
from __future__ import annotations

import dataclasses
import difflib
from decimal import Decimal

import pytest

from chiplet_system_description import elaborate
from chiplet_system_description.revision import revise_system
from chiplet_system_description.model import PortRef
from chiplet_system_description.source import SourceError, check_text, read_source
from chiplet_system_description.source.document import Carrier, DieItem, External, EdgeItem
from chiplet_system_description.source.elaborate import elaborate_source
from chiplet_system_description.source.scalars import bare, number, quantized, quote, scalar
from chiplet_system_description.source.writer import SourceWriteError, dumps_source, write_revision

from tests.source_dies import SOURCE, write_source
from tests.test_source_plan import Resolver

FLOW = "flow:\n  packaging_flow:\n    checks: {template: workshop}\n"


# --- scalars --------------------------------------------------------------------
@pytest.mark.parametrize("value,text", [
    (1000, "1000"), (1000.0, "1000"), (0.1, "0.1"), (-0.0, "0"), (1e-7, "0.0000001"),
    (1.5e21, "1500000000000000000000"), (Decimal("1200.500"), "1200.5"), (Decimal("1E+3"), "1000"),
])
def test_a_number_is_its_shortest_decimal(value, text):
    assert number(value) == text
    assert float(text) == float(value)


@pytest.mark.parametrize("bad", [True, "1", float("nan"), float("inf")])
def test_a_number_that_is_not_one_is_refused(bad):
    with pytest.raises((TypeError, ValueError)):
        number(bad)


def test_placements_compare_on_the_nanometre_grid():
    assert quantized(1000.0000000001) == quantized(1000) == Decimal("1000.000")
    assert quantized(0.0004) != quantized(0.0006)


@pytest.mark.parametrize("text", ['plain', 'say "hi"', "back\\slash", "tab\tline\nbreak",
                                  "nel\u0085ls ps ", "é kept", "#: not a comment"])
def test_a_quoted_string_reads_back_exactly(text):
    import yaml
    assert yaml.safe_load(f"k: {quote(text)}\n") == {"k": text}
    assert " " not in quote(text) and "\u0085" not in quote(text)


@pytest.mark.parametrize("text,written", [
    ("U1", "U1"), ("dies/DRIVER", "dies/DRIVER"), ("_n", "_n"), ("U1.VDD", "U1.VDD"),
    ("intm4tm2_wire_bond_v0", "intm4tm2_wire_bond_v0"), ("a-b.c/d", "a-b.c/d"),
    ("d2d_f[0..2]", '"d2d_f[0..2]"'), ("1.1", '"1.1"'), ("0755", '"0755"'),
    ("2026-03-22", '"2026-03-22"'), ("packaging-flow run", '"packaging-flow run"'),
    ("no", '"no"'), ("ON", '"ON"'), ("Null", '"Null"'), ("y", '"y"'), ("", '""'),
    ("../ECHO", '"../ECHO"'), (".inf", '".inf"'),
])
def test_a_string_goes_bare_only_when_the_rule_allows(text, written):
    import yaml
    assert scalar(text) == written
    assert yaml.safe_load(f"k: {written}\n") == {"k": text}


def test_the_retyped_words_are_refused_in_every_case_form():
    import itertools
    for word in ("y", "n", "yes", "no", "true", "false", "on", "off", "null"):
        for form in itertools.product(*((c.lower(), c.upper()) for c in word)):
            assert not bare("".join(form))


def test_a_name_the_rule_does_not_allow_is_written_quoted_and_reads_back():
    items = _items()
    items["rails"] = [("on", "power", [PortRef("U1", "VDD"), PortRef("U2", "VDD")]),
                      ("VSS", "ground", [PortRef("U1", "VSS"), PortRef("U2", "VSS")])]
    text = dumps_source(**items)
    assert '    - {name: "on", class: power, ports: [U1.VDD, U2.VDD]}\n' in text
    assert check_text(text)[0].rails[0].name == "on"


# --- a whole file -------------------------------------------------------------------
def _items():
    return dict(
        name="two_die", description="Driver and echo on a carrier",
        interposer_adapter="intm4tm2",
        carrier=Carrier("intm4tm2", "CF_CARRIER", (2000, 3200), 0),
        dies=[DieItem("U1", "dies/DRIVER", (1000, 2200), "cupillar_opt1", 0, 750, 0),
              DieItem("U2", "dies/ECHO", (1000, 1000), "cupillar_opt1", 180, 750, 0)],
        buses=[(("U1", "U2"), ["d2d_f0", "d2d_f1", "d2d_f2", "d2d_r0", "d2d_r1"], "", "_n")],
        rails=[("VDD", "power", [PortRef("U1", "VDD"), PortRef("U2", "VDD")]),
               ("VSS", "ground", [PortRef("U1", "VSS"), PortRef("U2", "VSS")])],
        alone=[PortRef("U2", "STATUS"), PortRef("U1", "CLK")],
        flow_text=FLOW, comment="A two-die source the tests mutate line by line.")


def test_the_items_write_the_fixture_byte_for_byte():
    assert dumps_source(**_items()) == SOURCE


def test_a_written_file_holds_every_block():
    items = _items()
    items.update(
        alone=[], registry="registry/ipdk",
        links=[(PortRef("U1", "CLK"), PortRef("U2", "STATUS"), None)],
        open=[],
        external=External("intm4tm2_wire_bond_v0",
                          (EdgeItem("west", ("VDD",), 0), EdgeItem("east", ("VSS",), 0)), 0),
        fill_keepout=[("M4", 20), ("TM1", 60.5)])
    text = dumps_source(**items)
    source, findings = check_text(text)
    assert findings == []
    assert source.links[0].net is None and source.registry == "registry/ipdk"
    assert '  fill_keepout: {halo_um: {M4: 20, TM1: 60.5}}\n' in text
    assert '      west: [VDD]\n' in text


def test_a_link_net_is_written_only_when_it_is_not_the_default():
    items = _items()
    items.update(alone=[], links=[(PortRef("U1", "CLK"), PortRef("U2", "STATUS"), "U1_CLK"),
                                  (PortRef("U2", "d2d_r9"), PortRef("U1", "d2d_r9"), "back")])
    text = dumps_source(**items)
    assert '{a: U1.CLK, b: U2.STATUS}' in text
    assert '{a: U2.d2d_r9, b: U1.d2d_r9, net: back}' in text


def test_consecutive_refs_on_one_instance_collapse():
    items = _items()
    items["alone"] = [PortRef("U1", f"g{i}") for i in range(3)] + [PortRef("U2", "g3")]
    text = dumps_source(**items)
    assert '    - "U1.g[0..2]"\n    - U2.g3\n' in text


def test_a_file_tier_0_refuses_is_not_returned():
    items = _items()
    items["dies"][1] = dataclasses.replace(items["dies"][1], rotation=45)
    with pytest.raises(SourceError, match="is not one of 0, 90, 180, 270"):
        dumps_source(**items)


def test_the_writer_stamps_a_newer_minor_it_implements(monkeypatch):
    """A minor above 1.3 is stamped when that is what the content needs and the
    reader implements it. No 1.4 key is added to the format: the trial read and
    the floor are faked, and a file that does not need the minor keeps its own.

    The trial version is ``versions.implemented``. The writer reads it back
    from ``ChosenStamp.tried``, so that one patch is what the trial is."""
    import warnings

    from chiplet_system_description.source import versions
    from chiplet_system_description.source import writer as writer_mod
    from chiplet_system_description.source.document import Finding

    from chiplet_system_description._cfio import cfio

    monkeypatch.setattr(versions, "implemented", lambda: {1: (4, "1.4")})
    real_check, real_minor = writer_mod.check_text, versions.content_minor

    def check(text, path="system.chiplet"):
        # No 1.4 key exists. This stands in for the read refusing content the
        # 1.1 placeholder cannot carry, so the writer has to restamp.
        if "needs-1.4" in text and 'format_version: "1.4"' not in text:
            return None, [Finding(1, "this content is format 1.4")]
        return real_check(text, path)

    def minor(source):
        # The floor of that faked content. Any other file keeps its real floor,
        # so an ordinary source still stamps 1.1 while implemented() says 1.4.
        if "needs-1.4" in getattr(source, "text", ""):
            return "1.4"
        return real_minor(source)

    monkeypatch.setattr(writer_mod, "check_text", check)
    monkeypatch.setattr(versions, "content_minor", minor)
    items = _items()
    items["comment"] = "needs-1.4"
    with warnings.catch_warnings():
        # The 1.4 trial is a higher minor of an accepted major. Silence that
        # warning and no other.
        warnings.simplefilter("ignore", category=cfio.FormatVersionWarning)
        text = dumps_source(**items)
        assert 'format_version: "1.4"\n' in text
        assert dumps_source(**_items()) == SOURCE


# --- a revision -------------------------------------------------------------------------
COMMENTED = SOURCE.replace("  buses:\n", "  # the forward and return lanes\n  buses:\n")


def _old(tmp_path, text=COMMENTED):
    path = write_source(tmp_path, text)
    system, _ = elaborate_source(path, Resolver())
    return read_source(path), system


def _moved(system, **at):
    instances = []
    for inst in system.instances:
        if inst.id in at:
            x, y, *turn = at[inst.id]
            inst = dataclasses.replace(inst, position=dataclasses.replace(inst.position, x=x, y=y),
                                       rotation_z=turn[0] if turn else inst.rotation_z)
        instances.append(inst)
    return dataclasses.replace(system, instances=tuple(instances))


def _changed(old, new):
    return [line for line in difflib.ndiff(old.splitlines(), new.splitlines())
            if line[:1] in "+-"]


def test_an_unchanged_revision_writes_the_same_bytes(tmp_path):
    source, system = _old(tmp_path)
    assert write_revision(source, system, system) == source.text


def test_a_move_rewrites_only_the_token_that_moved(tmp_path):
    source, system = _old(tmp_path)
    text = write_revision(source, system, _moved(system, U1=(1200.5, 2200.0)))
    assert _changed(source.text, text) == [
        '-     - {id: U1, die: dies/DRIVER, at: [1000, 2200], rotation: 0, method: cupillar_opt1, thickness: 750}',
        '+     - {id: U1, die: dies/DRIVER, at: [1200.5, 2200], rotation: 0, method: cupillar_opt1, thickness: 750}',
    ]
    assert "# the forward and return lanes" in text


def test_board_noise_under_a_nanometre_writes_nothing(tmp_path):
    source, system = _old(tmp_path)
    noisy = _moved(system, U1=(1000.0000000001, 2199.9999999), U2=(1000.0, 1000.0, 540.0))
    assert write_revision(source, system, noisy) == source.text


def test_a_rotation_is_replaced_or_inserted(tmp_path):
    source, system = _old(tmp_path)
    assert 'at: [1000, 1000], rotation: 270,' in write_revision(
        source, system, _moved(system, U2=(1000.0, 1000.0, -90.0)))
    bare = COMMENTED.replace("at: [1000, 2200], rotation: 0,", "at: [1000, 2200],")
    source, system = _old(tmp_path, bare)
    assert 'at: [1000, 2200], rotation: 90, method:' in write_revision(
        source, system, _moved(system, U1=(1000.0, 2200.0, 90.0)))


def test_a_rotation_off_the_four_is_refused(tmp_path):
    source, system = _old(tmp_path)
    with pytest.raises(SourceWriteError, match="U1 is turned 45 degrees on the board") as info:
        write_revision(source, system, _moved(system, U1=(1000.0, 2200.0, 45.0)))
    assert info.value.line == 15


def _nets(system, **renamed):
    rows = elaborate(system)["netlist"]["nets"]
    return [dict(row, name=renamed.get(row["name"], row["name"])) for row in rows]


def _placements(system):
    return [{"component": i.id, "position_um": [i.position.x, i.position.y],
             "rotation_deg": i.rotation_z} for i in system.instances]


def test_a_rail_renamed_on_the_board_renames_the_rail(tmp_path):
    source, system = _old(tmp_path)
    new = revise_system(system, placements=_placements(system), nets=_nets(system, VDD="VCC"))
    text = write_revision(source, system, new)
    assert _changed(source.text, text) == [
        '-     - {name: VDD, class: power, ports: [U1.VDD, U2.VDD]}',
        '+     - {name: VCC, class: power, ports: [U1.VDD, U2.VDD]}',
    ]


def test_an_alone_net_has_no_field_to_rename(tmp_path):
    source, system = _old(tmp_path)
    new = revise_system(system, placements=_placements(system),
                        nets=_nets(system, U1_PAD_CLK_0="clk"))
    with pytest.raises(SourceWriteError, match="The net of U1.CLK was renamed to clk") as info:
        write_revision(source, system, new)
    assert info.value.line == 25


def test_a_whole_bus_renamed_moves_its_affixes(tmp_path):
    source, system = _old(tmp_path)
    renamed = {f"d2d_{n}_n": f"top_d2d_{n}_b" for n in ("f0", "f1", "f2", "r0", "r1")}
    new = revise_system(system, placements=_placements(system), nets=_nets(system, **renamed))
    text = write_revision(source, system, new)
    assert ('ports: ["d2d_f[0..2]", "d2d_r[0..1]"], net_prefix: top_, net_suffix: _b}'
            in text)
    again, _ = elaborate_source(write_source(tmp_path, text), Resolver())
    assert [l.net_prefix for l in again.links] == [l.net_prefix for l in new.links]


def test_one_bus_lane_renamed_alone_is_refused(tmp_path):
    source, system = _old(tmp_path)
    new = revise_system(system, placements=_placements(system),
                        nets=_nets(system, d2d_f1_n="sideband"))
    with pytest.raises(SourceWriteError, match="Lane U1.d2d_f1 was renamed alone") as info:
        write_revision(source, system, new)
    assert info.value.line == 19


LINKED = (COMMENTED.replace('  alone:\n    - U2.STATUS\n    - U1.CLK\n', '')
          .replace("  rails:\n", '  links:\n    - {a: U2.STATUS, b: U1.CLK}\n  rails:\n', 1))


def test_a_link_net_is_inserted_replaced_and_dropped(tmp_path):
    source, system = _old(tmp_path, LINKED)
    rename = lambda s, old, net: revise_system(  # noqa: E731
        s, placements=_placements(s), nets=_nets(s, **{old: net}))
    named = write_revision(source, system, rename(system, "U2_STATUS", "status"))
    assert '{a: U2.STATUS, b: U1.CLK, net: status}' in named
    source, system = _old(tmp_path, named)
    renamed = write_revision(source, system, rename(system, "status", "ready"))
    assert '{a: U2.STATUS, b: U1.CLK, net: ready}' in renamed
    source, system = _old(tmp_path, renamed)
    assert write_revision(source, system, rename(system, "ready", "U2_STATUS")) == LINKED


def test_a_revision_that_changes_the_structure_is_refused(tmp_path):
    source, system = _old(tmp_path)
    fewer = dataclasses.replace(system, ties=system.ties[:1])
    with pytest.raises(SourceWriteError, match="changes which ports connect"):
        write_revision(source, system, fewer)
