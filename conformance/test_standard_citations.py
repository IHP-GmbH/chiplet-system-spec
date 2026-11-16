# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Gate: 3Dblox is cited by the standard's designation, not the draft's.

IEEE approved 3Dblox as IEEE 3537-2026 on 2026-11-04. Until then the texts here
called it "IEEE P3537", the project number of a draft, and a reader meeting that
designation takes the language to be unfinished. This reads every file git
tracks and fails on "P3537" anywhere in it. The list is derived, not
hand-maintained: a new file is covered the moment it is committed.

The one exemption is the Version History section of the spec: a row there
records what an earlier edit said, and rewriting it would falsify the record.

What a green here does NOT cover: whether the status a document states beside the
designation is still current, which is a fact about a public web page on the day
of the edit (each citation names the page and the date it was read); draft-state
wording that does not use the designation, such as "being standardized"; and a
file that is not valid UTF-8, which is skipped.
"""

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = "docs/CHIPLET_FORMAT_SPEC.md"
CITING = (SPEC, "docs/3dblox_interop.md")
HISTORY = "\n## Version History\n"
DRAFT = re.compile(r"\bP3537\b")
THIS = pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix()


def _tracked():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                         capture_output=True, check=True).stdout
    return [name for name in out.decode().split("\0") if name and name != THIS]


def _governed(name):
    try:
        text = (ROOT / name).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return ""
    if name == SPEC:
        head, history = text.split(HISTORY, 1)
        # The exemption ends at the next section, so text appended after the
        # table is governed again.
        _, _, after = history.partition("\n## ")
        text = head + after
    return text


def test_no_tracked_file_cites_the_draft_designation():
    offenders = []
    for name in _tracked():
        for number, line in enumerate(_governed(name).splitlines(), 1):
            if DRAFT.search(line):
                offenders.append(f"{name}:{number}")
    assert not offenders, offenders


def test_the_walk_reaches_the_documents_that_cite_it():
    """A green from a walk that found nothing would prove nothing."""
    tracked = set(_tracked())
    for name in CITING:
        assert name in tracked, name
        assert "IEEE 3537-2026" in _governed(name), name


def test_the_history_exemption_is_only_the_history_section():
    spec = (ROOT / SPEC).read_text(encoding="utf-8")
    assert spec.count(HISTORY) == 1
    history = spec.split(HISTORY, 1)[1]
    assert history.lstrip().startswith("| Version |")
    # The one row the exemption exists for is still there, and still exempt.
    assert DRAFT.search(history)
    assert not DRAFT.search(_governed(SPEC))
