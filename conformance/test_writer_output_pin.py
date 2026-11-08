# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The half of the version rule a consumer cannot check: was the bump big enough?

The KiCad plugin implemented the consumer half of META-10 (the bytes moved, so
the declared version must have moved too) and reported the one thing it could not
see from outside: whether the bump was the right SIZE. It only has bytes; the
size depends on what changed semantically, which only this repository knows.

This is that half, and it is mechanical for the case that matters. The reader
release is observable to a consumer exactly when the WRITER'S OUTPUT changes,
because at least one consumer runs a byte-exact writer parity gate over those
bytes. So: pin a digest of what the writer emits over a fixed corpus next to the
release that emitted it. If the digest moves, the MINOR must have moved with it,
and a PATCH is not enough.

Why this exists rather than a sentence in the policy: 1.3.0 was published because
a CONSUMER noticed the bytes had moved while the number stood still, and this
repository's four checks on that number all ask whether its declaration sites
AGREE, never whether it MOVED. Agreement between copies of a value is not a check
that the value is right.

What this does NOT cover, so nobody reads the green as wider than it is: a
release that moves for a reason with no byte consequence, an exported symbol or a
loosened refusal, is invisible here and stays governed by VERSION_POLICY.md and
by review. This catches the direction that bit us, an observable change under a
number that did not move far enough.
"""
import hashlib
import json
import pathlib

import chiplet_format_io as cfio

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PIN = FIXTURES / "writer_output_pin.json"

#: A document carrying every character the writer has to escape, built from code
#: points so the source file itself stays free of them. The corpus of real
#: fixtures is NOT enough on its own, and that is the whole reason this probe is
#: here: not one of them contains NEL, LS, PS or a lone CR, so the change that
#: motivated this check (U+0085 stopped being spelled with PyYAML's own escape)
#: would have moved no byte of a fixture-only digest. A guard whose corpus cannot
#: express the defect it was written for is decoration.
_ESCAPE_PROBE = {
    "format_version": "1.0",
    "assembly": {"name": "a" + chr(0x85) + "b" + chr(0x2028) + "c"
                 + chr(0x2029) + "d" + chr(0x0D) + "e"},
    "components": [],
}


def _writer_output_digest():
    """Digest of what the writer emits, over a deterministic corpus.

    Every loadable fixture in sorted order, then the escape probe. A fixture the
    reader refuses is skipped by design: it has no writer output to pin.
    """
    h = hashlib.sha256()
    used = []
    for path in sorted(FIXTURES.glob("*.chiplet")):
        try:
            doc = cfio.loads(path.read_text(encoding="utf-8"), validate=False)
        except Exception:
            continue
        h.update(path.name.encode("utf-8"))
        h.update(cfio.dumps(doc, validate=False).encode("utf-8"))
        used.append(path.name)
    h.update(b"__escape_probe__")
    h.update(cfio.dumps(_ESCAPE_PROBE, validate=False).encode("utf-8"))
    used.append("__escape_probe__")
    return h.hexdigest(), used


def _release(text):
    parts = text.split(".")
    return int(parts[0]), int(parts[1])


def test_the_corpus_is_not_empty_and_still_exercises_every_escape():
    # Floor guard. A digest over nothing is stable forever and would make the
    # assertion below pass without looking at anything.
    _, used = _writer_output_digest()
    assert len(used) >= 8, f"only {len(used)} documents in the pinned corpus"
    probe = cfio.dumps(_ESCAPE_PROBE, validate=False)
    for escape in ("\\x85", "\\L", "\\P", "\\r"):
        assert escape in probe, \
            f"the probe stopped exercising {escape}, so a change to it is unpinned"


def test_a_changed_writer_output_moved_the_reader_release_minor():
    pin = json.loads(PIN.read_text(encoding="utf-8"))
    digest, _ = _writer_output_digest()
    if digest == pin["writer_output_sha256"]:
        return
    assert _release(cfio.__version__) > _release(pin["reader_release"]), (
        "the writer's output changed since reader release "
        f"{pin['reader_release']} and __version__ is only {cfio.__version__}. A "
        "consumer can observe those bytes, so this needs at least a MINOR and a "
        f"PATCH is not enough. Move the release, then set {PIN.name} to the new "
        f"release and digest {digest}."
    )


def test_the_pin_is_never_ahead_of_the_reader():
    # The pin records the release that PRODUCED those bytes, so it can never name
    # a release the reader has not reached. Without this, updating the pin on its
    # own would silence the check above forever.
    pin = json.loads(PIN.read_text(encoding="utf-8"))
    assert _release(pin["reader_release"]) <= _release(cfio.__version__), \
        f"the pin claims {pin['reader_release']} but the reader is {cfio.__version__}"
