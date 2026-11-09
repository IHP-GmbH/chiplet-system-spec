# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The run tiers: a bare ``pytest`` starts no tool, the spec is the repository
this package lives in, a sibling comes from its own variable before
``HCDP_ROOT``, and a case that reaches a seam without its marker fails.

Each check here runs without a sibling and without a tool, so it holds in every
tier, including the one with nothing beside the checkout."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from tests import conftest


def test_the_default_run_deselects_the_live_cases(request):
    assert request.config.getini("addopts") == ["-m", "not live_openroad"]


def test_both_markers_are_registered(request):
    names = {line.split(":", 1)[0] for line in request.config.getini("markers")}
    assert {conftest.SIBLING_MARKER, conftest.LIVE_MARKER} <= names


@pytest.fixture()
def no_routes(monkeypatch):
    """No sibling variable, no HCDP_ROOT, no CI, and no interposer_pnr import."""
    for name in (*conftest.SIBLING_ENV.values(), "HCDP_ROOT", "HCDP_CI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(conftest, "_interposer_pnr_checkout", lambda: None)
    monkeypatch.setattr(conftest, "_current_item", None)
    return monkeypatch


def test_the_spec_is_the_repository_above_this_package():
    assert conftest.spec_path("reference/python") == conftest.SPEC_ROOT / "reference" / "python"


def test_the_spec_reads_pass_from_a_copy_outside_the_umbrella(tmp_path):
    """The checked-out files, copied away from the umbrella, still read their own
    spec: the reference identity check passes there, not skipped, with
    ``HCDP_ROOT`` unset and the variable that once named the spec pointing
    nowhere. The copy is what git lists, so local venvs and builds stay out."""
    assert conftest.SPEC_ROOT not in tmp_path.resolve().parents, "basetemp inside the repository"
    listed = subprocess.run(["git", "-C", str(conftest.SPEC_ROOT), "ls-files", "-z",
                             "--cached", "--others", "--exclude-standard"],
                            capture_output=True, check=True).stdout.decode().split("\0")
    copy = tmp_path / "copy"
    for rel in filter(None, listed):
        source = conftest.SPEC_ROOT / rel
        if source.is_file():
            (copy / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copy / rel)
    env = {k: v for k, v in os.environ.items() if k not in ("HCDP_ROOT", "HCDP_CI")}
    env.update(CHIPLET_SPEC_ROOT=str(tmp_path / "elsewhere"), PYTHONDONTWRITEBYTECODE="1")
    run = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs",
         "--basetemp", str(tmp_path / "basetemp"),
         "tests/test_vendored_identity.py::test_vendored_cfio_is_byte_identical_to_reference"],
        cwd=copy / "description", env=env, capture_output=True, text=True)
    assert run.returncode == 0 and "1 passed" in run.stdout, run.stdout + run.stderr


def test_a_path_the_spec_lacks_fails(no_routes):
    with pytest.raises(pytest.fail.Exception, match="not in the spec repository: absent"):
        conftest.spec_path("absent")


def test_the_umbrella_is_hcdp_root_when_set(no_routes, tmp_path):
    no_routes.setenv("HCDP_ROOT", str(tmp_path))
    no_routes.setenv("HCDP_CI", "1")
    assert conftest.sibling("interconnect_pdk") == (tmp_path / "interconnect_pdk",
                                                    f"the umbrella ({tmp_path})")


def test_the_umbrella_is_beside_the_repository_when_unset(no_routes):
    assert conftest.sibling("interconnect_pdk")[0] == (
        conftest.SPEC_ROOT.parent / "interconnect_pdk")


@pytest.mark.parametrize("name,env", sorted(conftest.SIBLING_ENV.items()))
def test_a_siblings_own_variable_comes_before_the_umbrella(no_routes, tmp_path, name, env):
    no_routes.setenv(env, str(tmp_path / "own"))
    no_routes.setenv("HCDP_ROOT", str(tmp_path / "umbrella"))
    assert conftest.sibling(name) == (tmp_path / "own", f"${env}")


def test_a_set_variable_never_falls_back_to_the_umbrella(no_routes, tmp_path):
    (tmp_path / "umbrella" / "interconnect_pdk" / "manifest").mkdir(parents=True)
    no_routes.setenv("INTERCONNECT_PDK_ROOT", str(tmp_path / "own"))
    no_routes.setenv("HCDP_ROOT", str(tmp_path / "umbrella"))
    with pytest.raises(pytest.skip.Exception, match=r"interconnect_pdk/manifest \(.*own/manifest, "
                                                    r"from \$INTERCONNECT_PDK_ROOT\)"):
        conftest.require_sibling("interconnect_pdk", "manifest")


def test_an_empty_variable_counts_as_unset(no_routes, tmp_path):
    no_routes.setenv("INTERCONNECT_PDK_ROOT", "  ")
    no_routes.setenv("HCDP_ROOT", str(tmp_path))
    assert conftest.sibling("interconnect_pdk")[0] == tmp_path / "interconnect_pdk"


def test_interposer_pnr_comes_from_its_import_first(no_routes, tmp_path):
    no_routes.setattr(conftest, "_interposer_pnr_checkout", lambda: tmp_path)
    no_routes.setenv("HCDP_ROOT", str(tmp_path / "umbrella"))
    assert conftest.require_sibling(conftest.INTERPOSER_PNR) == tmp_path


def test_a_ci_run_without_hcdp_root_has_no_umbrella(no_routes):
    no_routes.setenv("HCDP_CI", "1")
    assert conftest.sibling("interconnect_pdk") == (
        None, "neither $INTERCONNECT_PDK_ROOT nor $HCDP_ROOT gives it (HCDP_CI=1 looks nowhere else)")


def test_a_ci_run_without_hcdp_root_reads_a_siblings_own_variable(no_routes, tmp_path):
    no_routes.setenv("HCDP_CI", "1")
    no_routes.setenv("INTERCONNECT_PDK_ROOT", str(tmp_path))
    assert conftest.require_sibling("interconnect_pdk") == tmp_path


def test_a_ci_run_fails_a_case_whose_sibling_no_route_finds(no_routes):
    no_routes.setenv("HCDP_CI", "1")
    with pytest.raises(pytest.fail.Exception, match="interposer-pnr/tests/fixtures "
                                                    r"\(neither the interposer_pnr import nor"):
        conftest.require_sibling(conftest.INTERPOSER_PNR, "tests/fixtures")


def test_a_ci_run_without_hcdp_root_still_collects():
    """The refusal is per case: the conftest imports under HCDP_CI=1 with no root."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("HCDP_ROOT", *conftest.SIBLING_ENV.values())}
    env.update(HCDP_CI="1", PYTHONDONTWRITEBYTECODE="1")
    code = "import tests.conftest as c; assert c.sibling('interconnect_pdk')[0] is None"
    run = subprocess.run([sys.executable, "-B", "-c", code], cwd=conftest.PACKAGE_ROOT, env=env,
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("seam", [
    lambda: conftest.require_sibling("interconnect_pdk"),
    conftest.require_interposer_pnr,
    conftest.require_strict_live_gate,
    conftest.openroad_bin,
    lambda: conftest.run_ipnr("--help"),
], ids=["require_sibling", "require_interposer_pnr", "require_strict_live_gate",
        "openroad_bin", "run_ipnr"])
def test_a_seam_reached_without_its_marker_fails(seam):
    """This case carries neither marker, so every seam refuses it before it
    looks for a sibling or starts anything."""
    with pytest.raises(pytest.fail.Exception, match="is not marked"):
        seam()


def test_an_absent_sibling_fails_under_ci(no_routes, tmp_path):
    """With no case recorded the marker check lets the call through to the
    presence check. The path is absent by construction, so this reads no
    sibling."""
    no_routes.setenv("HCDP_CI", "1")
    no_routes.setenv("INTERCONNECT_PDK_ROOT", str(tmp_path / "absent"))
    with pytest.raises(pytest.fail.Exception, match="sibling checkout not present"):
        conftest.require_sibling("interconnect_pdk")
