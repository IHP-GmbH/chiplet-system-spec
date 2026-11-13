# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Gate: 3Dblox is cited by the standard's designation, not the draft's.

IEEE approved 3Dblox as IEEE 3537-2026 on 2026-11-04. Until then the texts here
called it "IEEE P3537", the project number of a draft, and a reader meeting that
designation takes the language to be unfinished. This walks every committed text
file and fails on a "P3537" that does not carry a year.

The one exemption is the spec's Version History table: a row there records what
an earlier edit said, and rewriting it would falsify the record.

What a green here does NOT cover: whether the status a document states beside the
designation is still current. That is a fact about a public web page on the day
of the edit, which is why each citation names the page and the date it was read.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SUFFIXES = {".md", ".json", ".yaml", ".yml", ".py", ".txt", ".toml",
            ".chiplet", ".cpp", ".hpp", ".h"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "build",
             "dist", "node_modules"}
DRAFT = re.compile(r"\bP3537\b(?!-\d{4})")
HISTORY = "## Version History"


def _texts():
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        parts = path.relative_to(ROOT).parts
        if SKIP_DIRS.intersection(parts) or any(
                part.endswith(".egg-info") for part in parts):
            continue
        if path == pathlib.Path(__file__).resolve():
            continue
        yield path


def _governed(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.name == "CHIPLET_FORMAT_SPEC.md":
        text = text.split(HISTORY, 1)[0]
    return text


def test_no_text_cites_the_draft_designation():
    offenders = []
    for path in _texts():
        for number, line in enumerate(_governed(path).splitlines(), 1):
            if DRAFT.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, offenders


def test_the_walk_reaches_the_documents_that_cite_it():
    """A green from a walk that found nothing would prove nothing."""
    names = {path.relative_to(ROOT).as_posix() for path in _texts()}
    assert {"docs/CHIPLET_FORMAT_SPEC.md", "docs/3dblox_interop.md"} <= names
    for name in ("docs/CHIPLET_FORMAT_SPEC.md", "docs/3dblox_interop.md"):
        assert "IEEE 3537-2026" in _governed(ROOT / name), name


def test_the_history_exemption_is_only_the_history_table():
    """The cut must land on the heading, so the body above it stays governed."""
    spec = (ROOT / "docs" / "CHIPLET_FORMAT_SPEC.md").read_text(encoding="utf-8")
    assert spec.count(HISTORY) == 1
    assert spec.split(HISTORY, 1)[1].lstrip().startswith("| Version |")


def test_the_pattern_catches_the_draft_and_passes_the_standard():
    assert DRAFT.search("IEEE P3537")
    assert DRAFT.search("(3Dblox -> P3537)")
    assert not DRAFT.search("IEEE 3537-2026")
    assert not DRAFT.search("IEEE P3537-2026")
