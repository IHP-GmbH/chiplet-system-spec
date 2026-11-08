# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""A conformance module collected first still hands the session this checkout's package.

CI's conformance job installs ./reference/python non-editable and runs pytest in
conformance/. The first module that imports chiplet_format_io decides which copy
the whole session sees, so a module that imports it before putting reference/python
first on sys.path gives every later test the installed copy. The checkout-fallback
test in test_die_contract.py is the one that notices, so it is the witness here.

Each case collects one module first and then the witness, in a child pytest whose
path holds a copy of the package outside the checkout, as an installed wheel is.
It turns red on: drop the sys.path insert from the module collected first.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WITNESS = "test_the_checkout_fallback_only_fires_inside_the_checkout"


@pytest.mark.parametrize("first", ["test_agreement.py", "test_transistor_view.py"])
def test_a_module_collected_first_leaves_the_checkout_package_in_place(tmp_path, first):
    site = tmp_path / "site-packages"
    shutil.copytree(ROOT / "reference" / "python" / "chiplet_format_io", site / "chiplet_format_io",
                    ignore=shutil.ignore_patterns("__pycache__"))
    env = dict(os.environ, PYTHONPATH=str(site), PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k", WITNESS,
         first, "test_die_contract.py"],
        cwd=HERE, env=env, capture_output=True, text=True, timeout=300)
    out = child.stdout + child.stderr
    assert child.returncode == 0, out[-4000:]
    assert "1 passed" in child.stdout, out[-4000:]
