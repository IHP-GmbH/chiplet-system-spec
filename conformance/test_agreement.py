# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The agreement of a simulated SPICE with the CDL its LVS read, over two IP blocks.

OTA1336 and CMP7345 are vendored from their IHP__ repositories with only comment
lines changed: each block's schematic SPICE and the CDL in its ``netlist/layout``.
Both pairs agree. Every control is one named edit of the CDL text: a red one
must turn the verdict to ``does not hold``, and a positive one must leave it
holding.

The amendment input is read on a synthetic block that names no IP. Each of its
tests names, in its docstring, the mutation of the code it turns red on. It is
then read on CMP7345, locally patched: the amended CDL and the receipt that
names the patch are vendored beside the shipped CDL.
"""
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

from chiplet_format_io.agreement import (AMENDMENT_FORMAT, DEVICES, REASONS, AgreementRefused,  # noqa: E402
                                        agree, agree_record)
from chiplet_format_io.eda_views import SCHEMA  # noqa: E402
from chiplet_format_io.transistor_view import TransistorViewRefused, transistor_view  # noqa: E402

FIXTURES = ROOT / "conformance" / "fixtures" / "transistor_view"
PAIRS = {"ota1336": "OTA1336.spice.cdl", "cmp7345": "CMP7345.cdl"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _view(name):
    return transistor_view(FIXTURES / name / "view.json", "spice", "schematic")


def _agree(tmp_path, name, text, receipt=None):
    """``agree`` of the fixture view against ``text`` as its one CDL file, under a copy of the fixture."""
    shutil.copytree(FIXTURES / name, tmp_path / name)
    (tmp_path / name / "under_test.cdl").write_text(text, encoding="utf-8")
    view = transistor_view(tmp_path / name / "view.json", "spice", "schematic")
    amendment = None
    if receipt is not None:
        data = receipt if isinstance(receipt, bytes) else (json.dumps(receipt, indent=1) + "\n").encode()
        (tmp_path / name / "receipt.json").write_bytes(data)
        amendment = {"file": "receipt.json", "sha256": sha(data)}
    return agree(view, [{"file": "under_test.cdl", "sha256": sha(text.encode("utf-8"))}], amendment=amendment)


def _cdl(name, file=None):
    return (FIXTURES / name / (file or PAIRS[name])).read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# The two positives
# ----------------------------------------------------------------------
@pytest.mark.parametrize("name,cards", [("ota1336", 25), ("cmp7345", 72)])
def test_each_ip_block_agrees_with_its_lvs_cdl(name, cards):
    read = agree(_view(name), [{"file": PAIRS[name], "sha256": sha((FIXTURES / name / PAIRS[name]).read_bytes())}])
    assert read.findings == [] and read.holds and read.word == "holds" and read.amendment is None
    assert read.counts["cards"] == cards
    assert "the layout and its LVS: no claim" in read.not_claimed


def test_cmp7345_reads_geometry_it_does_not_compare_and_drops_mm_ok():
    read = agree(_view("cmp7345"), [{"file": "CMP7345.cdl", "sha256": sha((FIXTURES / "cmp7345" / "CMP7345.cdl").read_bytes())}])
    assert read.counts["sim_only_dropped"] == 63 and read.counts["geometry_not_compared"] > 0
    assert read.counts["port_order_differs"] == 1


@pytest.mark.parametrize("name,file", [(n, f) for n, f in PAIRS.items()]
                         + [("ota1336", "OTA1336-final.spice"), ("cmp7345", "CMP7345.amended.cdl")])
def test_the_vendored_netlists_state_their_source_and_hold_no_path(name, file):
    lines = (FIXTURES / name / file).read_text(encoding="utf-8").split("\n")
    header = "\n".join(lines[:4])
    assert "https://github.com/KrzysztofHerman/IHP__" in header
    # split so that REUSE does not read this line as a license tag
    assert "SPDX-License-" "Identifier: Apache-2.0" in header
    assert not any("sch_path" in line or "sym_path" in line or "/home/" in line for line in lines[4:])


# ----------------------------------------------------------------------
# The controls: one named edit of the CDL each
# ----------------------------------------------------------------------
def _edit(text, pattern, change):
    """``text`` with the first line matching ``pattern`` replaced by ``change(line)``, a str or a list."""
    lines = text.split("\n")
    k = next(k for k, line in enumerate(lines) if re.match(pattern, line))
    new = change(lines[k])
    lines[k:k + 1] = new if isinstance(new, list) else [new]
    return "\n".join(lines)


def _swap(line, a, b):
    t = line.split()
    t[a], t[b] = t[b], t[a]
    return " ".join(t)


MOS = r"(?i)M\S*\s"
TAP = r"(?i)R\S*\s.*\b[pn]tap1\b"
DIODE = r"(?i)D\S*\s.*\bdp?antenna\b"
TOP = {"ota1336": r"(?i)\.subckt OTA1336 ", "cmp7345": r"(?i)\.subckt CMP7345 "}

CONTROLS = {
    "N1 swap two nets of one MOS card": (MOS, lambda l: _swap(l, 1, 2)),
    "N2 double one MOS w": (MOS, lambda l: re.sub(r"(?i)\bw=(\S+)", lambda m: f"w=2*{m.group(1)}", l)),
    "N3 lv to hv on one MOS card": (MOS, lambda l: l.replace("sg13_lv_", "sg13_hv_", 1)),
    "N4 drop one MOS card": (MOS, lambda l: []),
    "N5 add one MOS card": (MOS, lambda l: [l, l.split()[0] + "Z " + l.split(" ", 1)[1]]),
    "N6 rename one top port": ("TOP", lambda l: " ".join(l.split()[:2] + [l.split()[2] + "_x"] + l.split()[3:])),
    "P1 reverse the top's port order": ("TOP", lambda l: " ".join(l.split()[:2] + l.split()[2:][::-1])),
    "T1 one tap moved to another net": (TAP, lambda l: " ".join([l.split()[0], "moved"] + l.split()[2:])),
    "T2 ptap1 to ntap1, or back": (TAP, lambda l: l.replace("ptap1", "ntap1") if "ptap1" in l else l.replace("ntap1", "ptap1")),
    "T3 drop one tap card": (TAP, lambda l: []),
    "T4 add one tap card": (TAP, lambda l: [l, l.split()[0] + "Z " + l.split(" ", 1)[1]]),
    "TP one tap's terminals reversed": (TAP, lambda l: _swap(l, 1, 2)),
    "D1 one diode's anode and cathode swapped": (DIODE, lambda l: _swap(l, 1, 2)),
    "D2 dantenna to dpantenna, or back": (DIODE, lambda l: l.replace("dantenna", "dpantenna") if " dantenna" in l else l.replace("dpantenna", "dantenna")),
    "D3 drop one diode card": (DIODE, lambda l: []),
    "D4 m=2 on one diode": (DIODE, lambda l: re.sub(r"(?i)\bm=\S+", "m=2", l) if re.search(r"(?i)\bm=", l) else l + " m=2"),
}


def _controls():
    for name in PAIRS:
        for control, (pattern, change) in CONTROLS.items():
            if control.startswith("D") and name == "ota1336":
                continue  # OTA1336 holds no antenna diode
            yield pytest.param(name, control, pattern, change, id=f"{name}: {control}")


@pytest.mark.parametrize("name,control,pattern,change", list(_controls()))
def test_a_control_reads_as_it_is_named(tmp_path, name, control, pattern, change):
    text = _edit(_cdl(name), TOP[name] if pattern == "TOP" else pattern, change)
    assert text != _cdl(name)
    read = _agree(tmp_path, name, text)
    if control.startswith(("P", "TP")):
        assert read.holds, read.findings
    else:
        assert not read.holds and read.word == "does not hold"


# ----------------------------------------------------------------------
# The device table and the resistor form
# ----------------------------------------------------------------------
def test_the_device_table_is_data_of_letters_pin_counts_and_forms():
    for devices in DEVICES.values():
        for letter, pins, form in devices.values():
            assert letter in "MRCD" and pins in (2, 3, 4) and form in ("sized", "resistor", "tap", "diode")
    assert DEVICES["sg13g2"]["rhigh"] == ("R", 3, "resistor")


RES_SPICE = ".subckt blk a b\nXR1 a b sub! rhigh w=0.5e-6 l=0.96e-6 m=1 b=0 mm_ok=1\n.ends\n"
RES_CDL = ".SUBCKT blk a b\nRR1 a b 2.345k $SUB=sub! $[rhigh] w=0.5e-6 l=0.96e-6 b=0 m=1\n.ENDS\n"


def _blk(tmp_path, spice, cdl, pdk="ihp-sg13g2"):
    (tmp_path / "blk.spice").write_text(spice)
    (tmp_path / "blk.cdl").write_text(cdl)
    view = {"format": "chiplet-eda-views-transistor/v1", "dialect": "spice", "netlist_kind": "schematic",
            "top": "blk", "top_form": "subckt", "pins": [{"name": "a", "role": "signal"}, {"name": "b", "role": "signal"}],
            "devices": ["rhigh"], "local_subckts": [], "pdk": {"name": pdk, "revision": "r"},
            "files": [{"file": "blk.spice", "sha256": sha(spice.encode())}]}
    (tmp_path / "view.json").write_text(json.dumps(view))
    return agree(transistor_view(tmp_path / "view.json", "spice", "schematic"),
                 [{"file": "blk.cdl", "sha256": sha(cdl.encode())}])


def test_a_resistor_reads_its_body_from_sub_and_counts_its_value(tmp_path):
    read = _blk(tmp_path, RES_SPICE, RES_CDL)
    assert read.holds, read.findings
    assert read.counts["resistor_value_not_compared"] == 1
    assert "resistor CDL value: derived value, not compared" in read.not_claimed


def test_the_resistor_value_is_not_read_so_any_value_still_holds(tmp_path):
    assert _blk(tmp_path, RES_SPICE, RES_CDL.replace("2.345k", "1")).holds


@pytest.mark.parametrize("edit", [("$SUB=sub!", "$SUB=vss"), ("l=0.96e-6 b", "l=1.92e-6 b"), ("b=0", "b=1")])
def test_a_resistor_body_or_size_change_does_not_hold(tmp_path, edit):
    assert not _blk(tmp_path, RES_SPICE, RES_CDL.replace(*edit)).holds


# ----------------------------------------------------------------------
# Refusals
# ----------------------------------------------------------------------
@pytest.mark.parametrize("reason,spice,cdl,pdk", [
    ("technology", RES_SPICE, RES_CDL, "gf180mcuD"),
    ("cdl_syntax", RES_SPICE, RES_CDL.replace("$[rhigh]", "rhigh $comment"), "ihp-sg13g2"),
    ("cdl_top", RES_SPICE, ".SUBCKT one a b\n.ENDS\n.SUBCKT two a b\n.ENDS\n", "ihp-sg13g2"),
    ("cdl_top", RES_SPICE, RES_CDL + RES_CDL, "ihp-sg13g2"),
])
def test_a_cdl_that_cannot_be_read_refuses_with_its_reason(tmp_path, reason, spice, cdl, pdk):
    assert reason in REASONS
    with pytest.raises(AgreementRefused) as excinfo:
        _blk(tmp_path, spice, cdl, pdk)
    assert excinfo.value.reason == reason, str(excinfo.value)
    assert isinstance(excinfo.value, TransistorViewRefused)


def test_a_cdl_file_that_is_not_its_bytes_refuses(tmp_path):
    view = _view("ota1336")
    with pytest.raises(AgreementRefused) as excinfo:
        agree(view, [{"file": "OTA1336.spice.cdl", "sha256": "0" * 64}])
    assert excinfo.value.reason == "digest"


# ----------------------------------------------------------------------
# The record
# ----------------------------------------------------------------------
def test_agree_record_reads_the_spice_view_against_the_cdl_rows(tmp_path):
    src = FIXTURES / "ota1336"
    rows = []
    for kind, file in (("spice", "OTA1336-final.spice"), ("cdl", "OTA1336.spice.cdl"), ("transistor_view", "view.json")):
        data = (src / file).read_bytes()
        (tmp_path / file).write_bytes(data)
        rows.append({"kind": kind, "file": file, "sha256": sha(data), "status": "recorded"})
    record = tmp_path / "eda-views.json"
    record.write_text(json.dumps({"schema": SCHEMA, "design": "OTA1336", "corners": ["nom"],
                                  "default_corner": "nom", "views": rows, "not_recorded": []}))
    read = agree_record(record, "schematic")
    assert read.holds and read.counts["cards"] == 25


# ----------------------------------------------------------------------
# A reference amendment, on a synthetic block
# ----------------------------------------------------------------------
# The amended CDL brings a body net out of inv: one port on its subckt, the
# matching pin on the X card that calls it, and two MOS bulk terminals.
SYN_SPICE = """.subckt blk a b vdd vss
XM1 a b vss vss sg13_lv_nmos w=1u l=0.13u ng=1 m=1 mm_ok=1
x1 a b vdd vss inv
.ends
.subckt inv i o vdd vss
XM2 o i vdd vdd sg13_lv_pmos w=2u l=0.13u ng=1 m=1 mm_ok=1
XM3 o i vss vss sg13_lv_nmos w=1u l=0.13u ng=1 m=1 mm_ok=1
.ends
"""
SYN_SHIPPED = """.SUBCKT inv i o vdd vss
MM2 o i vdd vdd sg13_lv_pmos w=2u l=0.13u ng=1 m=1
MM3 o i vss vss sg13_lv_nmos w=1u l=0.13u ng=1 m=1
.ENDS
.SUBCKT blk a b vdd vss
MM1 a b vss vss sg13_lv_nmos w=1u l=0.13u ng=1 m=1
X1 a b vdd vss / inv
.ENDS
"""
SYN_AMENDED = """.SUBCKT inv i o vdd vss vsub
MM2 o i vdd vdd sg13_lv_pmos w=2u l=0.13u ng=1 m=1
MM3 o i vss vsub sg13_lv_nmos w=1u l=0.13u ng=1 m=1
.ENDS
.SUBCKT blk a b vdd vss
MM1 a b vss vsub sg13_lv_nmos w=1u l=0.13u ng=1 m=1
X1 a b vdd vss vsub / inv
.ENDS
"""
#: What r3.2 finds on the amended CDL with no receipt: one finding per card.
SYN_FINDINGS = ["blk/XM1", "blk/x1", "subckt inv", "inv/XM3"]


def _syn_receipt():
    """A cdl-reference-amendment/1 receipt for SYN_AMENDED, naming no IP."""
    def card(shipped, amended, subckt, kind, element, before, after, **of):
        return {"shipped_line": shipped, "amended_line": amended, "subckt": subckt, "element": element,
                "kind": kind, "net": "vsub", **of, "before": before, "after": after}
    shipped, amended = SYN_SHIPPED.split("\n"), SYN_AMENDED.split("\n")
    return {
        "format": AMENDMENT_FORMAT, "amendment": "synthetic", "macro": "blk",
        "label": "a synthetic block, for the format only",
        "shipped": {"repository": "none", "commit": "none", "path": "blk.cdl",
                    "sha256": sha(SYN_SHIPPED.encode())},
        "amended": {"repository": "none", "branch": "none", "commit": "none", "path": "blk.cdl",
                    "sha256": sha(SYN_AMENDED.encode())},
        "comments": {"removed": [], "added": []},
        "cards": [card(1, 1, "inv", "subckt_pin", ".SUBCKT", shipped[0], amended[0]),
                  card(3, 3, "inv", "bulk", "MM3", shipped[2], amended[2]),
                  card(6, 6, "blk", "bulk", "MM1", shipped[5], amended[5]),
                  card(7, 7, "blk", "instance_pin", "X1", shipped[6], amended[6], of="inv")],
        "counts": {"bulk": 2, "instance_pin": 1, "subckt_pin": 1},
        "mechanical_diff": "ALLOWED",
    }


def _syn(tmp_path, cdl, receipt=None):
    """``agree`` of the synthetic block against ``cdl``, with ``receipt`` as its amendment when given."""
    (tmp_path / "blk.spice").write_text(SYN_SPICE)
    (tmp_path / "blk.cdl").write_text(cdl)
    view = {"format": "chiplet-eda-views-transistor/v1", "dialect": "spice", "netlist_kind": "schematic",
            "top": "blk", "top_form": "subckt",
            "pins": [{"name": "a", "role": "signal"}, {"name": "b", "role": "signal"},
                     {"name": "vdd", "role": "supply"}, {"name": "vss", "role": "supply"}],
            "devices": ["sg13_lv_nmos", "sg13_lv_pmos"], "local_subckts": ["inv"],
            "pdk": {"name": "ihp-sg13g2", "revision": "r"},
            "files": [{"file": "blk.spice", "sha256": sha(SYN_SPICE.encode())}]}
    (tmp_path / "view.json").write_text(json.dumps(view))
    amendment = None
    if receipt is not None:
        data = receipt if isinstance(receipt, bytes) else (json.dumps(receipt, indent=1) + "\n").encode()
        (tmp_path / "receipt.json").write_bytes(data)
        amendment = {"file": "receipt.json", "sha256": sha(data)}
    return agree(transistor_view(tmp_path / "view.json", "spice", "schematic"),
                 [{"file": "blk.cdl", "sha256": sha(cdl.encode())}], amendment=amendment)


def test_without_a_receipt_the_rule_reads_as_before(tmp_path):
    """The input is additive: with no receipt the shipped CDL holds, and the
    amended one does not, on exactly one finding per card.

    Red on: the MOS bulk terminal left out of the net compare."""
    shipped = _syn(tmp_path, SYN_SHIPPED)
    assert shipped.findings == [] and shipped.word == "holds" and shipped.amendment is None
    amended = _syn(tmp_path, SYN_AMENDED)
    assert [f.split(":")[0] for f in amended.findings] == SYN_FINDINGS
    assert amended.word == "does not hold" and amended.amendment is None


def test_with_its_receipt_the_amended_cdl_holds_and_the_word_names_it(tmp_path):
    """Red on: the word reading plain "holds" when a receipt was read."""
    data = (json.dumps(_syn_receipt(), indent=1) + "\n").encode()
    read = _syn(tmp_path, SYN_AMENDED, data)
    assert read.findings == [] and read.amendment == sha(data)
    assert read.word == f"holds with a named reference amendment {sha(data)}"


def test_red_a_receipt_on_a_cdl_it_was_not_written_for_refuses(tmp_path):
    """One comment line added to the amended CDL: every card still fits its
    line, and only the receipt's amended.sha256 tells the file apart.

    Red on: amended.sha256 not checked, the first CDL file taken instead."""
    with pytest.raises(AgreementRefused) as excinfo:
        _syn(tmp_path, SYN_AMENDED + "* one line more\n", _syn_receipt())
    assert excinfo.value.reason == "amendment"


def test_red_counts_that_are_not_the_cards_refuse(tmp_path):
    """Red on: the counts not checked against the cards' tally."""
    receipt = _syn_receipt()
    receipt["counts"]["bulk"] += 1
    with pytest.raises(AgreementRefused) as excinfo:
        _syn(tmp_path, SYN_AMENDED, receipt)
    assert excinfo.value.reason == "amendment"


def test_red_a_receipt_missing_a_bulk_card_does_not_hold(tmp_path):
    """The card and its count both dropped, so the receipt reads; the bulk
    terminal it no longer accounts for is still found.

    Red on: a read receipt waiving every finding."""
    receipt = _syn_receipt()
    receipt["cards"] = [c for c in receipt["cards"] if c["element"] != "MM1"]
    receipt["counts"]["bulk"] -= 1
    read = _syn(tmp_path, SYN_AMENDED, receipt)
    assert [f.split(":")[0] for f in read.findings] == ["blk/XM1"]
    assert read.word == "does not hold"


# ----------------------------------------------------------------------
# CMP7345, locally patched
# ----------------------------------------------------------------------
# The amended CDL exposes the body nets in eleven cards; its receipt is the
# sealed one, read by this sha (PROVENANCE.md beside it).
CMP7345_AMENDMENT = "b4dfc5bd03bb267fa0fea9e921a9daed756a495dd3245ccce8dfa2694366efaf"
CMP7345_RECEIPT = FIXTURES / "cmp7345" / "amendment_receipt.json"


def test_the_cmp7345_receipt_is_the_sealed_one_and_binds_the_amended_cdl():
    receipt = json.loads(CMP7345_RECEIPT.read_text(encoding="utf-8"))
    assert sha(CMP7345_RECEIPT.read_bytes()) == CMP7345_AMENDMENT and receipt["format"] == AMENDMENT_FORMAT
    assert receipt["amended"]["sha256"] == sha((FIXTURES / "cmp7345" / "CMP7345.amended.cdl").read_bytes())
    assert len(receipt["cards"]) == sum(receipt["counts"].values()) == 11


def test_without_its_receipt_the_amended_cmp7345_does_not_hold_on_exactly_its_cards(tmp_path):
    read = _agree(tmp_path, "cmp7345", _cdl("cmp7345", "CMP7345.amended.cdl"))
    assert read.word == "does not hold" and read.amendment is None
    assert len(read.findings) == 11


def test_with_its_receipt_the_amended_cmp7345_holds_with_the_receipt_named(tmp_path):
    read = _agree(tmp_path, "cmp7345", _cdl("cmp7345", "CMP7345.amended.cdl"), CMP7345_RECEIPT.read_bytes())
    assert read.findings == [] and read.amendment == CMP7345_AMENDMENT
    assert read.word == f"holds with a named reference amendment {CMP7345_AMENDMENT}"


def test_the_cmp7345_receipt_on_the_shipped_cdl_refuses(tmp_path):
    with pytest.raises(AgreementRefused) as excinfo:
        _agree(tmp_path, "cmp7345", _cdl("cmp7345"), CMP7345_RECEIPT.read_bytes())
    assert excinfo.value.reason == "amendment"
