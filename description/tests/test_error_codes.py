# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Every refusal names its code where it is raised; no code is taken by default."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import chiplet_system_description
from chiplet_system_description.revision import BoardRevisionError
from chiplet_system_description.source.writer import SourceWriteError

SRC = Path(chiplet_system_description.__file__).parent
CODES = {"report_malformed", "connection_changed", "carrier_net_changed", "frame_changed",
         "rename_no_field", "rename_taken", "rotation_not_manhattan"}


def _calls(name):
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == name):
                yield path.relative_to(SRC), node


def _code(node, position):
    found = [k.value for k in node.keywords if k.arg == "code"]
    if len(node.args) > position:
        found.append(node.args[position])
    return found


@pytest.mark.parametrize("name,position", [("BoardRevisionError", 1), ("SourceWriteError", 2)])
def test_every_raise_site_names_a_known_code(name, position):
    sites = list(_calls(name))
    assert sites, f"no {name} raised"
    for path, node in sites:
        code = _code(node, position)
        where = f"{path}:{node.lineno}"
        assert len(code) == 1, f"{where}: {name} without a code"
        assert isinstance(code[0], ast.Constant) and code[0].value in CODES, (
            f"{where}: {name} code is not one of the known codes")


@pytest.mark.parametrize("cls", [BoardRevisionError, SourceWriteError])
def test_the_code_has_no_default(cls):
    assert inspect.signature(cls).parameters["code"].default is inspect.Parameter.empty
