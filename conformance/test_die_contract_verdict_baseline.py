# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The die contract verdict ledger: what this reader answers, written down.

STRICT_ADMISSION_API_VERSION promises that a verdict is reproducible under any
reader declaring the same capability, and "a moved answer bumps the integer" is
a process rule with nothing behind it until something records the answers. This
ledger does. Each document of CORPUS, under fixtures/die_contract_verdicts/, is
asked the two questions this ledger asks, ``bind`` and then the port profile,
and the answer is a row of fixtures/die_contract_verdict_baseline.jsonl keyed by
the document's sha256 and the capability integer, never by the fixture path.

The ledger is APPEND-ONLY and historical. A row is written from the same commit
as the reader it describes, so it agrees with that reader by construction; the
property enforced is about the file, not the reader: no two rows may share a
document and a capability. A reader whose answer moved without a bump produces a
row that collides with the one already there, and the fix is to bump the
integer (a new key; the old row stays as the record of what the previous
capability said) or to restore the answer, never to edit the row. A document
whose bytes change is a new key, and its old row stays as the answer to bytes
the tree no longer carries.

The corpus is a list here, not a glob, and it is held to reach every outcome
class of both questions and every element disposition the reader can report:
the ledger's claim is only as wide as the answers it has rows for. What the
questions leave out is stated in docs/STRICT_ADMISSION_API.md.

Recorded as the verdict: an outcome class per question and the sorted
(pointer, disposition) pairs the reader reported unread. Never the message
text, which is commentary and may be reworded freely; it is kept beside the row
as ``note`` and compared by nothing, like ``fixture`` and ``reader_release``.
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

import chiplet_format_io as cfio  # noqa: E402
from chiplet_format_io import die_contract as dc  # noqa: E402

FIXTURES = ROOT / "conformance" / "fixtures"
DOCUMENTS = FIXTURES / "die_contract_verdicts"
LEDGER = FIXTURES / "die_contract_verdict_baseline.jsonl"
QUESTIONS = ("bind", "port_profile")
NOT_ASKED = {"outcome": "not_asked", "unread": []}
ROW_KEYS = {"document", "capability", "reader_release", "document_version",
            "verdict", "fixture", "note"}

#: The documents, by name. Taking one out is an edit here, beside the rows it
#: orphans; adding one is an edit here and one pasted row.
CORPUS = (
    "v1_0_conforming.json",
    "v1_0_port_identity.json",
    "v1_0_stale_layout.json",
    "v1_1_conforming.json",
    "v1_1_unknown_key.json",
    "v1_1_unknown_terminal_role.json",
    "v1_1_unknown_attestation_result.json",
    "v1_1_port_bus_hole.json",
    "v1_2_unknown_key.json",
    "v1_2_port_without_ordinal.json",
    "v1_2_unknown_terminal_role.json",
    "v1_2_unknown_attestation_result.json",
    "v1_2_unknown_opening_shape.json",
    "v1_2_unknown_inventory_kind.json",
    "v1_1_supply_domains.json",
    "v1_2_supply_domains.json",
    "v1_2_ground_domain_at_volts.json",
    "v1_3_unknown_key.json",
    "v1_3_port_without_ordinal.json",
    "v1_3_unknown_terminal_role.json",
    "v1_3_unknown_attestation_result.json",
    "v1_3_unknown_opening_shape.json",
    "v1_3_unknown_inventory_kind.json",
)
OUTCOMES = {"bind": {"bound", "incomplete", "refused"},
            "port_profile": {"admitted", "incomplete", "refused", "not_asked"}}
#: Every disposition the reader can report on an element it did not read. The
#: document-level one is a refusal, not a report; the port one is reported by
#: the pairing rule rather than by the table.
REPORTABLE = (set(dc.DISPOSITIONS.values()) - {dc.DOCUMENT_REFUSED}) | {dc.PORT_UNREADABLE}


def documents():
    return [DOCUMENTS / name for name in CORPUS]


def _unread(findings):
    return sorted({(f.pointer, f.disposition) for f in findings})


def verdict(path):
    """This reader's answer to one document, as the two questions this ledger
    asks in order: ``bind`` at its defaults (every digest, the box, the
    version policy), then ``port_profile``, ``check_terminals(
    require_port_identity=True)`` on what bound. The second is not asked of a
    document the first refused. Returns the answer and the messages, the
    latter as commentary."""
    answer = {}
    notes = []
    try:
        bound = dc.bind(path)
    except dc.DieContractIncomplete as exc:
        answer["bind"] = {"outcome": "incomplete", "unread": _unread(exc.unrecognised)}
        notes.append(str(exc))
        bound = None
    except dc.DieContractError as exc:
        answer["bind"] = {"outcome": "refused", "unread": []}
        notes.append(str(exc))
        bound = None
    else:
        answer["bind"] = {"outcome": "bound", "unread": _unread(bound.unrecognised)}
    if bound is None:
        answer["port_profile"] = dict(NOT_ASKED)
    else:
        try:
            dc.check_terminals(bound, require_port_identity=True)
        except dc.DieContractIncomplete as exc:
            answer["port_profile"] = {"outcome": "incomplete",
                                      "unread": _unread(exc.unrecognised)}
            notes.append(str(exc))
        except dc.DieContractError as exc:
            answer["port_profile"] = {"outcome": "refused", "unread": []}
            notes.append(str(exc))
        else:
            answer["port_profile"] = {"outcome": "admitted", "unread": []}
    # The messages name files by the path they were read from, the document
    # and any sibling it binds; a row must not carry where a checkout lived.
    prefix = str(DOCUMENTS) + os.sep
    return answer, "; ".join(n.replace(prefix, "") for n in notes)


def row_for(path):
    answer, note = verdict(path)
    return {
        "document": hashlib.sha256(path.read_bytes()).hexdigest(),
        "capability": dc.STRICT_ADMISSION_API_VERSION,
        "reader_release": cfio.__version__,
        "document_version": json.loads(path.read_text()).get("version"),
        "verdict": answer,
        "fixture": path.name,
        "note": note,
    }


def _normal(answer):
    """The verdict as JSON carries it: pairs as lists, keys sorted."""
    return json.loads(json.dumps(answer, sort_keys=True))


def key(row):
    return (row["document"], row["capability"])


def ledger():
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]


def test_the_ledger_is_well_formed_and_no_two_rows_share_a_key():
    """The one property the file itself is held to. A repeated key with a
    different verdict is an answer that moved under a capability that did
    not; a repeated key with the same verdict is a row written twice. Both are
    refused, and neither is fixed by editing a row. A note is commentary, but
    a published fixture may not carry the path of the machine that wrote it."""
    rows = ledger()
    assert rows, f"{LEDGER.name} records nothing"
    seen = {}
    for n, row in enumerate(rows, 1):
        assert set(row) == ROW_KEYS, f"row {n} carries {sorted(row)}"
        assert re.fullmatch(r"[0-9a-f]{64}", row["document"]), f"row {n}: not a sha256"
        assert isinstance(row["capability"], int) and not isinstance(row["capability"], bool)
        assert tuple(row["verdict"]) == QUESTIONS, f"row {n} answers {list(row['verdict'])}"
        assert not re.search(r"(?:^|[\s:'\"(])/", row["note"]), f"row {n}: an absolute path"
        k = key(row)
        assert k not in seen, (
            f"rows {seen.get(k)} and {n} both record document {k[0][:12]} at capability "
            f"{k[1]}; " + ("they disagree, so an answer moved without a bump"
                           if rows[seen[k] - 1]["verdict"] != row["verdict"]
                           else "the second is a copy of the first"))
        seen[k] = n


def test_the_corpus_is_the_list_and_reaches_every_answer_this_reader_can_give():
    """The tree carries exactly CORPUS, so a document leaving enforcement is a
    visible edit and never a deleted file. And the list is held to reach every
    outcome class of both questions and every element disposition the reader
    can report: a disposition with no row is an answer the ledger does not
    hold, however the docstring reads."""
    present = {p.name for p in DOCUMENTS.glob("v*.json")}
    assert present == set(CORPUS), f"tree and CORPUS differ on {sorted(present ^ set(CORPUS))}"
    answers = [verdict(path)[0] for path in documents()]
    for question, classes in OUTCOMES.items():
        reached = {a[question]["outcome"] for a in answers}
        assert reached == classes, f"{question} reaches {sorted(reached)}"
    reported = {d for a in answers for q in QUESTIONS for _, d in a[q]["unread"]}
    assert reported == REPORTABLE, f"dispositions differ on {sorted(reported ^ REPORTABLE)}"


@pytest.mark.parametrize("path", documents(), ids=lambda p: p.stem)
def test_this_reader_answers_each_document_as_its_row_records(path):
    """Every document of the corpus has its row at this capability, and the
    row says what the reader says. The failure messages carry the row to
    append, so a new document or a new capability costs one paste and no
    thought about the shape; an answer that moved is the one case with a
    decision in it."""
    assert path.is_file(), f"{path.name} is in CORPUS and not in the tree"
    row = row_for(path)
    match = [r for r in ledger() if key(r) == key(row)]
    assert match, (
        f"no row for {path.name} at capability {row['capability']}; append this line "
        f"to {LEDGER.name}, edit none:\n" + json.dumps(row, sort_keys=True))
    assert len(match) == 1, f"{len(match)} rows for {path.name} at capability {row['capability']}"
    recorded = match[0]
    assert recorded["verdict"] == _normal(row["verdict"]), (
        f"the answer on {path.name} moved and the capability did not: recorded "
        f"{json.dumps(recorded['verdict'], sort_keys=True)}, this reader says "
        f"{json.dumps(_normal(row['verdict']))}. Bump STRICT_ADMISSION_API_VERSION and "
        f"append the new row, or restore the answer; never rewrite the row.")


def test_the_two_answers_the_policy_fixes_do_not_move_with_any_capability():
    """Two documents answer to VERSION_POLICY rather than to this reader, so a
    bump cannot legitimise moving them. A minor this reader does not implement
    is owed every conforming reading it can give, so the unknown key at 1.3 is
    ignored and reported for as long as 1.3 is above the floor; once adopted,
    that document is refused for a key its own minor does not define, which is
    what adopting 1.2 did to its 1.2 twin, and this pin steps aside. A rail
    with a hole has no defensible lane at any minor. The hole behaviour is
    tested in test_die_contract.py; this pins the answer, not the behaviour."""
    path = DOCUMENTS / "v1_3_unknown_key.json"
    assert path.is_file()
    if dc.reads_element_level(json.loads(path.read_text())):
        assert verdict(path)[0] == {
            "bind": {"outcome": "bound", "unread": [("vendor_extension", dc.IGNORED)]},
            "port_profile": {"outcome": "admitted", "unread": []}}
    path = DOCUMENTS / "v1_1_port_bus_hole.json"
    assert path.is_file()
    assert verdict(path)[0] == {"bind": {"outcome": "bound", "unread": []},
                                "port_profile": {"outcome": "refused", "unread": []}}
