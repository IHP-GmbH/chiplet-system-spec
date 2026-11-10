# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Gate for validation rule 9: component connection -> method -> interface type.

Rule 9 is the cross-artifact half of the pad-usage check. Rule 8 relates a pad's usage class
to an interface type inside one document; rule 9 relates the interconnect METHOD
a component selects through ``connection:`` to the type of the interfaces that
component takes part in, and the method's ``interface_type`` lives in the
interconnect manifest, not in the ``.chiplet``. Neither reference validator holds
a manifest, so neither runs it. What this file gates is therefore the CONTRACT
around the rule, not the rule: that the spec states it as an assembly-stage rule
and names its owners, that the corpus carries the single oracle those owners
parity-test against, and that the oracle is a document the reference readers
accept, which is the measured fact that makes the gap visible instead of
invisible.

What a green here does NOT cover (META-2), and the list is the point:

* the rule itself. Nothing in this repository resolves a method id to an
  ``interface_type``, so no green here is evidence that any inconsistent binding
  is ever refused. The refusals are owned by the KiCad plugin (at export),
  adk-tools' assembly DRC, the Mosaic loader and chiplet-system-description (at load), and
  each owes a parity test against the oracle fixture in its own suite. Rows open
  per host; until those land, rule 9 is written down and unenforced.
* the CONSISTENT case. The corpus carries one inconsistent binding, not a pair;
  a host that refuses every binding would pass against this file alone.
* the message a host emits, and whether it names what the spec says it names.
* the geometry behind the binding: rule 9 compares two identifiers' declared
  types and says nothing about the stack, the pads or the pitch.
* whether ``examples/interconnect_methods.json`` yet carries ``interface_type``
  at all. It does not (that is FUT-4, at interconnect_pdk); the assertion that
  the registry contradicts the document arms itself when the field lands.
"""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

import chiplet_format_io as cfio  # noqa: E402

SPEC = ROOT / "docs" / "CHIPLET_FORMAT_SPEC.md"
FIXTURES = ROOT / "conformance" / "fixtures"
ORACLE = FIXTURES / "v1_0_interface_inconsistent_binding.chiplet"
REGISTRY = ROOT / "examples" / "interconnect_methods.json"


def _manifest():
    return yaml.safe_load(
        (ROOT / "conformance" / "manifest.yaml").read_text(encoding="utf-8"))


def _oracle_row():
    for entry in _manifest()["fixtures"]:
        if entry["file"] == ORACLE.name:
            return entry
    raise AssertionError(f"{ORACLE.name} is not in the conformance manifest")


def _rule_9_text():
    """The body of validation rule 9, out of the spec's Validation Rules list."""
    text = SPEC.read_text(encoding="utf-8")
    body = re.search(r"\n9\. (.*?)\n\*\*Reader behavior", text, re.DOTALL)
    assert body, "no validation rule 9 in the spec"
    return body.group(1)


def test_the_spec_states_rule_9_and_marks_it_assembly_stage():
    text = SPEC.read_text(encoding="utf-8")
    heading = re.search(r"\*\*Assembly stage \((.*?)\):\*\*", text, re.DOTALL)
    assert heading, "rule 9 has no assembly-stage heading of its own"
    assert "do NOT run it" in heading.group(1), \
        "the heading must say the reference validators do not run the rule"
    rule = _rule_9_text()
    assert "interface_type" in rule and "connection" in rule


def test_the_spec_names_every_owner_of_rule_9():
    # A rule nobody is named for is a rule nobody runs. The owners are part of
    # the normative text because the reference validators are not among them.
    rule = _rule_9_text()
    for owner in ("KiCad plugin", "adk-tools", "Mosaic", "chiplet-system-description"):
        assert owner in rule, f"rule 9 does not name {owner}"
    assert "WRITE" in rule and "LOAD" in rule, \
        "rule 9 must say which owner refuses at write and which at load"
    assert ORACLE.name in rule, "rule 9 must point at the oracle fixture"


def test_the_reference_validators_accept_the_oracle():
    # The measured fact behind the whole arrangement: this document is
    # inconsistent and the reference readers have nothing to say about it. If
    # this ever turns into a refusal, either a reader grew a manifest or some
    # other rule started firing, and both are news.
    doc = cfio.loads(ORACLE.read_text(encoding="utf-8"))
    assert doc["format_version"] == "1.0"
    row = _oracle_row()
    assert row["expect"] == "accept"
    assert row["assembly_stage"]["rule"] == 9
    assert row["assembly_stage"]["expect"] == "refuse"


def test_no_implementation_in_this_repository_claims_to_run_rule_9():
    # The parity manifest names a dimension per reader rule it can be skipped
    # for (checks_pad_usage, reads_source_text). Rule 9 has no such dimension on
    # purpose: nothing here runs it, so there is nothing to skip and nothing to
    # claim.
    for name, meta in _manifest()["implementations"].items():
        assert "checks_interface_binding" not in meta, (
            f"{name} declares a rule 9 dimension; no reader in this repository "
            f"holds an interconnect manifest")


def test_the_oracle_binds_a_method_a_host_can_actually_resolve():
    # An oracle whose method id resolves nowhere would be refused by every host
    # for the wrong reason (an unresolved cross-reference, rule 11's SHOULD)
    # and would measure nothing about rule 9.
    doc = yaml.safe_load(ORACLE.read_text(encoding="utf-8"))
    conns = {c["id"]: c.get("connection") for c in doc["components"]}
    bound = {cid: m for cid, m in conns.items() if m}
    assert bound, "the oracle binds no method at all"
    methods = json.loads(REGISTRY.read_text(encoding="utf-8"))["methods"]
    iface = doc["interfaces"][0]
    assert iface["type"] in cfio.KNOWN_INTERFACE_TYPES
    for cid, method_id in bound.items():
        assert method_id in methods, (
            f"component {cid} binds {method_id!r}, which the example registry "
            f"does not define")
        # Arms itself when interconnect_pdk adds interface_type to the methods
        # (FUT-4): from then on the registry itself says the oracle contradicts
        # the document, and this stops resting on the fixture's comment.
        declared = methods[method_id].get("interface_type")
        if declared is not None:
            assert declared != iface["type"], (
                f"{method_id} now declares interface_type {declared!r}; the "
                f"oracle is no longer inconsistent and must be rebuilt")
