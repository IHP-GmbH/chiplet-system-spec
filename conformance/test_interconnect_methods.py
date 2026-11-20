"""Interconnect method registry 1.2: the pad parameters' new names.

Contract 1.2 adds ``passivation_opening_um`` and ``pad_metal_enclosure_um`` as
the names of what 1.1 calls ``passiv_opening_um`` and ``tm2_enclosure_um``. The
old names stay required through every 1.x, so a 1.1 reader keeps finding them.
This file holds the schema, the example and the policy; the reader's refusal of
two names with two values (``technology_package.method_fab_params``) is tested
in ``test_technology_package.py`` with the module's other refusals.
"""
import copy
import json
import sys
from pathlib import Path

from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

from chiplet_format_io import technology_package as tp  # noqa: E402

SCHEMA = json.loads((ROOT / "schemas" / "interconnect_methods.schema.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((ROOT / "examples" / "interconnect_methods.json").read_text(encoding="utf-8"))
#: The policy with its line breaks folded, so a sentence check does not depend on wrapping.
POLICY = " ".join((ROOT / "docs" / "VERSION_POLICY.md").read_text(encoding="utf-8").split())

#: new name -> the 1.1 name it replaces.
RENAMED = {
    "passivation_opening_um": "passiv_opening_um",
    "pad_metal_enclosure_um": "tm2_enclosure_um",
}


def _fab_params_schema(schema):
    return schema["properties"]["methods"]["additionalProperties"]["properties"]["fab_params"]


def _valid(schema, instance):
    cls = validator_for(schema)
    cls.check_schema(schema)
    return cls(schema).is_valid(instance)


def _with_fab_params(fab_params):
    registry = copy.deepcopy(EXAMPLE)
    method = registry["default_method"]
    registry["methods"][method]["fab_params"] = fab_params
    return registry


def test_the_reader_renames_what_the_schema_renames():
    assert tp.FAB_PARAMS_RENAMED == RENAMED


def test_the_example_is_at_the_reader_minor_and_valid():
    assert EXAMPLE["schema_version"] == tp.SUPPORTED_METHODS_VERSION == "1.2"
    assert _valid(SCHEMA, EXAMPLE)


def test_every_example_method_states_both_spellings_equal():
    for method, spec in EXAMPLE["methods"].items():
        for new, old in RENAMED.items():
            assert spec["fab_params"][new] == spec["fab_params"][old], method


def test_the_schema_keeps_the_old_names_required():
    assert _fab_params_schema(SCHEMA)["required"] == ["passiv_opening_um", "tm2_enclosure_um"]
    assert not _valid(SCHEMA, _with_fab_params(
        {"passivation_opening_um": 40.0, "pad_metal_enclosure_um": 7.5}))


def test_the_schema_marks_the_old_names_deprecated_and_names_the_new():
    properties = _fab_params_schema(SCHEMA)["properties"]
    for new, old in RENAMED.items():
        assert "Deprecated since 1.2" in properties[old]["description"]
        assert new in properties[old]["description"]
        assert properties[new]["exclusiveMinimum"] == 0


def test_the_policy_names_the_deprecation():
    assert "`passiv_opening_um` and `tm2_enclosure_um`" in POLICY
    assert "removed only at interconnect methods 2.0" in POLICY
