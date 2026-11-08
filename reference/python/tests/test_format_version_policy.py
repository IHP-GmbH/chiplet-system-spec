# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""H-B: the tolerant format_version policy and the passthrough writer stamp.

The matrix (accept / warn / reject) plus the crux round-trip: a lossless
passthrough writer must PRESERVE a same-major higher-minor input, not stamp it
down, so it never manufactures a "1.0"-labelled file carrying 1.1 content.
"""
import warnings

import pytest

import chiplet_format_io as cfio


def _doc(fv):
    # fv is spliced verbatim so quoting/typing is under the test's control.
    return f"format_version: {fv}\nassembly:\n  name: a\n"


@pytest.fixture(autouse=True)
def _clean_warn_state():
    # The warn-once dedup set is process-global; keep cases independent.
    cfio._reset_version_warnings()
    yield
    cfio._reset_version_warnings()


# --- API surface ---------------------------------------------------------

def test_public_api_exports_policy():
    assert "check_format_version" in cfio.__all__
    assert "FormatVersionWarning" in cfio.__all__
    assert hasattr(cfio, "check_format_version")
    assert issubclass(cfio.FormatVersionWarning, Warning)


# --- accept --------------------------------------------------------------

def test_accept_baseline_no_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning would raise
        assert cfio.check_format_version("1.0") == "1.0"


def test_accept_implemented_minor_no_warn():
    # 1.1 is the highest minor these readers implement (ACCEPTED), so it reads
    # silently even though the assembly stamp is still "1.0".
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert cfio.check_format_version("1.1") == "1.1"


def test_every_writer_stamp_reads_silently():
    # Each kind of document has its own stamp; none of them may warn here.
    for stamp in (cfio.SUPPORTED_FORMAT_VERSION, cfio.SOURCE_FORMAT_VERSION):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert cfio.check_format_version(stamp) == stamp


def test_accept_unquoted_numeric_coerced():
    # PyYAML turns unquoted 1.0 into float 1.0; the reader coerces via str().
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        data = cfio.loads(_doc("1.0"))  # no quotes
    assert str(data["format_version"]) in ("1.0", "1.0")


def test_accept_additive_unknown_key_roundtrips():
    # Passthrough: an unknown top-level key on a "1.0" file survives dump/load.
    doc = 'format_version: "1.0"\nassembly:\n  name: a\nfuture_block:\n  k: v\n'
    data = cfio.loads(doc)
    reloaded = cfio.loads(cfio.dumps(data))
    assert reloaded["future_block"] == {"k": "v"}


# --- warn (same major, higher minor) ------------------------------------

def test_higher_minor_warns_and_accepts():
    with pytest.warns(cfio.FormatVersionWarning):
        assert cfio.check_format_version("1.6") == "1.6"


def test_warn_once_deduped_default_channel():
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        cfio.check_format_version("1.6")
        cfio.check_format_version("1.6")
    fired = [w for w in rec if issubclass(w.category, cfio.FormatVersionWarning)]
    assert len(fired) == 1  # deduped per distinct version


def test_on_warn_callback_is_undeduped():
    events = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfio.check_format_version("1.6", on_warn=events.append)
        cfio.check_format_version("1.6", on_warn=events.append)
    assert len(events) == 2  # every event, undeduped


# --- reject --------------------------------------------------------------

@pytest.mark.parametrize("fv,label", [
    ('"2.0"', "higher major"),
    ('"0.9"', "lower major"),
    ('"1"', "malformed (no minor)"),
    ('"1.x"', "malformed (non-numeric)"),
    ('"1.0.0"', "malformed (three parts)"),
])
def test_reject(fv, label):
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads(_doc(fv))


def test_reject_missing_version():
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads("assembly:\n  name: a\n")


# --- the crux: passthrough writer preserves higher minor -----------------

def test_roundtrip_preserves_higher_minor():
    """load "1.6" then write -> "1.6" out, NOT "1.0" (the flipped assertion).

    A stamp-down here would forge a "1.0" label over 1.6 content and suppress
    the higher-minor warning at every downstream reader. The same holds for an
    accepted minor above the assembly stamp ("1.1").
    """
    for fv in ("1.1", "1.6"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = cfio.loads(_doc(f'"{fv}"'))
            out = cfio.dumps(data)
            reloaded = cfio.loads(out)
        assert str(reloaded["format_version"]) == fv


def test_write_stamps_supported_when_equal():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = cfio.dumps({"format_version": "1.0", "assembly": {"name": "a"}})
    assert 'format_version: "1.0"' in out or "format_version: '1.0'" in out


def test_write_stamps_supported_when_missing_and_unvalidated():
    # validate=False lets a version-less dict through; the writer stamps SUPPORTED.
    out = cfio.dumps({"assembly": {"name": "a"}}, validate=False)
    reloaded = cfio.loads(out)
    assert str(reloaded["format_version"]) == "1.0"


def test_dumps_does_not_mutate_caller_dict():
    d = {"format_version": "1.6", "assembly": {"name": "a"}}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfio.dumps(d)
    assert d["format_version"] == "1.6"  # shallow copy, caller untouched
