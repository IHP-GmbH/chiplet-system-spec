# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Vendored-copy and fixture byte-identity. One adk-tools mirror has
already drifted, so these are not optional. The reference is in this repository,
so its checks never skip; strict-live turns an absent interposer-pnr into a
failure.

The vendored ``chiplet_format_io`` is exactly what this package runs, byte for
byte the spec's reference: every vendored file must exist upstream and match
it, and every upstream file that is not vendored must be named in
``NOT_VENDORED``, so a module the reference adds is a decision here and never a
silent gap. ``__pycache__`` and ``.pyc`` are the only exclusions; both trees
carry compiled bytes on a developer machine and those are not the package."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import List

import pytest

from chiplet_system_description._cfio import VENDOR_DIR
from tests.conftest import (
    GOLDEN_CHIPLET,
    INTERPOSER_PNR,
    PINS_JSON,
    require_sibling,
    spec_path,
)

VENDORED_PKG = VENDOR_DIR / "chiplet_format_io"
REFERENCE_PKG = "reference/python/chiplet_format_io"  # inside the spec

#: Reference modules deliberately not vendored. ``die_contract.py`` and
#: ``pad_numbers.py`` are vendored: pin numbers have one placement, the classic
#: import calls it, and it reads terminals through die_contract. Schema
#: admission is not called. This tree has no schema, and a schema file inside
#: the vendored package would be a non-Python file the die flow's native
#: snapshot refuses. The modules below are what this package does not read:
#: most hold what a die flow and its readers share, and several import each
#: other, so a vendored copy of one would not load; ``footprint_ids.py`` names
#: the footprints a KiCad sheet is given, and this package draws none. ``technology.py`` is vendored: the
#: system joins a die's PDK to its technology through it. ``sheet_names.py`` is
#: vendored: both sheet imports read a net name with the generator's rule.
#: ``test_elaborate_does_not_import_the_contract`` keeps the eDSL path off the
#: contract reader. Any non-Python file the reference may add is excluded by
#: default for the snapshot reason.
NOT_VENDORED = frozenset({
    "agreement.py",  # imports transistor_view, so a vendored copy could not load
    "eda_views.py",  # imports ready_die_names, which this package does not vendor
    "footprint_ids.py",  # footprint names; this package does not draw symbols
    "pins.py",  # the pin tables of a die view and a die contract
    "ready_die_names.py",  # the file names and build-record words of a ready die
    "transistor_view.py",  # imports eda_views, so a vendored copy could not load
    "verilog_closure.py",  # the Verilog closure a die's views are read with
})


def _package_files(root: Path):
    """Every file under ``root``, relative and sorted, compiled bytes excluded."""
    return sorted(
        p.relative_to(root)
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )


def compare_packages(vendored: Path, reference: Path) -> List[str]:
    """Findings; empty when ``vendored`` is the reference minus ``NOT_VENDORED``."""
    ours = set(_package_files(vendored))
    theirs = set(_package_files(reference))
    excluded = {Path(name) for name in NOT_VENDORED}
    findings = []
    if not ours:
        findings.append(f"no vendored files under {vendored}; the identity gate would pass vacuously")
    for rel in sorted(ours - theirs):
        findings.append(f"vendored but not in the reference: {rel}")
    for rel in sorted(theirs - ours - excluded):
        findings.append(f"in the reference, neither vendored nor named in NOT_VENDORED: {rel}")
    for rel in sorted(excluded - theirs):
        findings.append(f"named in NOT_VENDORED but no longer in the reference: {rel}")
    for rel in sorted(excluded & ours):
        findings.append(f"vendored although NOT_VENDORED names it: {rel}")
    for rel in sorted(ours & theirs):
        if (vendored / rel).read_bytes() != (reference / rel).read_bytes():
            findings.append(f"{vendored / rel} has drifted from {reference / rel}")
    return findings


def _assert_identical(local: Path, upstream: Path):
    assert local.read_bytes() == upstream.read_bytes(), (
        f"{local} has drifted from {upstream}"
    )


def test_vendored_cfio_is_byte_identical_to_reference():
    reference = spec_path(REFERENCE_PKG)
    findings = compare_packages(VENDORED_PKG, reference)
    assert not findings, "\n".join(findings)


@pytest.mark.needs_sibling_sources
def test_fixtures_are_byte_identical_to_interposer_pnr_originals():
    fixtures = require_sibling(INTERPOSER_PNR, "tests/fixtures")
    _assert_identical(GOLDEN_CHIPLET, fixtures / "ihp_two_die_route.chiplet")
    _assert_identical(PINS_JSON, fixtures / "two_die_route.pins.json")



# --- prove-it-can-fail: each outcome of the comparison on a throwaway pair ---
def _pair(tmp_path: Path):
    reference = tmp_path / "reference"
    vendored = tmp_path / "vendored"
    reference.mkdir()
    vendored.mkdir()
    (reference / "__init__.py").write_bytes(b"reader\n")
    for name in sorted(NOT_VENDORED):
        (reference / name).write_bytes(b"excluded\n")
    (vendored / "__init__.py").write_bytes(b"reader\n")
    assert compare_packages(vendored, reference) == []
    return vendored, reference


def test_seeded_mutant_one_byte_edit_fails_identity(tmp_path):
    vendored, reference = _pair(tmp_path)
    (vendored / "__init__.py").write_bytes(b"reader\n# drift\n")
    assert any("drifted" in f for f in compare_packages(vendored, reference))


def test_a_reference_module_that_is_neither_vendored_nor_named_is_a_finding(tmp_path):
    vendored, reference = _pair(tmp_path)
    (reference / "new_module.py").write_bytes(b"new\n")
    assert any("neither vendored nor named" in f for f in compare_packages(vendored, reference))


def test_vendoring_an_excluded_module_is_a_finding(tmp_path):
    vendored, reference = _pair(tmp_path)
    (vendored / "agreement.py").write_bytes(b"contract\n")
    assert any("although NOT_VENDORED names it" in f for f in compare_packages(vendored, reference))


def test_a_stale_exclusion_is_a_finding(tmp_path):
    vendored, reference = _pair(tmp_path)
    (reference / "agreement.py").unlink()
    assert any("no longer in the reference" in f for f in compare_packages(vendored, reference))


def test_a_stray_vendored_file_is_a_finding(tmp_path):
    vendored, reference = _pair(tmp_path)
    (vendored / "stray.py").write_bytes(b"stray\n")
    assert any("vendored but not in the reference: stray.py" in f
               for f in compare_packages(vendored, reference))


def test_an_empty_vendored_tree_is_a_finding(tmp_path):
    vendored, reference = _pair(tmp_path)
    (vendored / "__init__.py").unlink()
    assert any("vacuously" in f for f in compare_packages(vendored, reference))


def test_elaborate_does_not_import_the_contract():
    """The eDSL path does not load the contract reader. A fresh interpreter,
    so an earlier test that numbered pins cannot make this look loaded."""
    import subprocess
    import sys

    script = (
        "import chiplet_system_description.elaborate\n"
        "import sys\n"
        "names = ('chiplet_system_description._vendor.chiplet_format_io.die_contract',"
        " 'chiplet_format_io.die_contract')\n"
        "bad = [n for n in names if n in sys.modules]\n"
        "raise SystemExit(1 if bad else 0)\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


#: The contract entry points this package must not call: admission needs the
#: schema, and ``bind`` checks digests a source does not carry.
CONTRACT_ENTRY_POINTS = frozenset({"bind", "validate_die_contract", "admit_component"})


def contract_uses(text: str) -> List[str]:
    """``line: name`` for every import, name or attribute in ``text`` that
    reaches one of :data:`CONTRACT_ENTRY_POINTS`.

    The syntax tree is walked, so an alias, a dotted call and an import split
    over lines are all seen, and a name inside a string or a comment is not.
    """
    hits = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        else:
            continue
        hits += [f"{node.lineno}: {n}" for n in names if n in CONTRACT_ENTRY_POINTS]
    return hits


@pytest.mark.parametrize("text", [
    "from ._vendor.chiplet_format_io import die_contract as dc\ndc.bind(p)\n",
    "from ._vendor.chiplet_format_io.die_contract import (\n    BoundContract,\n"
    "    bind as _b)\n",
    "import x.die_contract\nx.die_contract.validate_die_contract(d)\n",
    "f = admit_component\n",
])
def test_every_spelling_of_a_contract_call_is_seen(text):
    assert contract_uses(text)


def test_a_name_in_a_string_or_a_comment_is_not_a_call():
    assert contract_uses('x = "call bind(x) here"  # bind(y)\n') == []


def test_src_does_not_call_the_vendored_contract():
    """Nothing under description/src reaches ``bind``,
    ``validate_die_contract`` or ``admit_component``. The vendored tree is the
    reference's own code, so it is not scanned."""
    src = VENDOR_DIR.parent
    hits = []
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src)
        if "__pycache__" in rel.parts or rel.parts[0] == VENDOR_DIR.name:
            continue
        hits += [f"{rel}:{hit}" for hit in contract_uses(path.read_text(encoding="utf-8"))]
    assert not hits, "\n".join(hits)


def test_the_copy_never_takes_the_public_name():
    """The public name is the installed reference's: importing this package
    neither puts the copy on ``sys.path`` nor registers it as
    ``chiplet_format_io``."""
    import sys

    from chiplet_system_description._cfio import cfio

    assert cfio.__name__ == "chiplet_system_description._vendor.chiplet_format_io"
    assert Path(cfio.__file__).resolve() == VENDORED_PKG / "__init__.py"
    assert all(Path(entry or ".").resolve() != VENDOR_DIR for entry in sys.path)
    try:
        import chiplet_format_io as public
    except ImportError:
        return  # no reference installed, so nothing else can answer the name
    assert public is not cfio
    assert VENDOR_DIR not in Path(public.__file__).resolve().parents
