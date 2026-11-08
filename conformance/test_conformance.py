# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Drive the conformance corpus as a test (corpus-as-parity).

reference_python is required; interposer_pnr_ir joins when the sibling checkout
is importable; reference_cpp joins when CHIPLET_CFIO_CPP_BIN points at a build.
The prove-it-can-fail mutant lives in test_format_version_policy.py (the writer
stamp-down and the intolerant reader are shown red there).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_conformance as rc


def test_corpus_parity_all_available_impls():
    ran, failures = rc.run(cpp_bin=os.environ.get("CHIPLET_CFIO_CPP_BIN"))
    assert "reference_python" in ran
    assert not failures, "conformance failures:\n" + "\n".join(failures)


def test_manifest_covers_the_required_seed_fixtures():
    manifest = rc._load_manifest()
    files = {e["file"] for e in manifest["fixtures"]}
    required = {
        "v1_0_baseline.chiplet", "v1_0_unquoted_numeric.chiplet",
        "v1_0_additive_unknown_key.chiplet", "v1_6_higher_minor.chiplet",
        "v2_0_higher_major.chiplet", "v0_9_lower_major.chiplet",
        "v1_0_missing_version.chiplet", "v1_0_malformed_version.chiplet",
    }
    assert required <= files
    # every fixture file referenced by the manifest exists
    for f in files:
        assert (rc.FIXTURES / f).is_file(), f
