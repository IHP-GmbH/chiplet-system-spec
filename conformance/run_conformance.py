#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Run the .chiplet conformance corpus against one or more implementations.

The manifest's read verdict (accept/warn/refuse) and normalized version are the
cross-implementation PARITY contract: every reader must agree. Writer and
round-trip expectations are class-specific and checked only against a
passthrough implementation (see manifest.yaml, implementation-class dimension).

Adapters:
  * reference_python  -- the vendored chiplet_format_io (passthrough); built in.
  * interposer_pnr_ir -- interposer_pnr.ir (typed reader); used when importable.
  * reference_cpp     -- an external built binary via --cpp-bin / CHIPLET_CFIO_CPP_BIN
                         that prints one of accept|warn|refuse|version=<v> per
                         fixture on argv; skipped (reported) when absent.

Usage:
    run_conformance.py [--interposer-pnr <src dir>] [--cpp-bin <path>]
Exit code 0 iff every applicable expectation held.
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

# reference_python: add the sibling reference package to the path.
sys.path.insert(0, str(HERE.parent / "reference" / "python"))
import chiplet_format_io as cfio  # noqa: E402


def _load_manifest():
    import yaml
    with open(HERE / "manifest.yaml", "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _normalized(fv):
    parsed = cfio.parse_format_version(fv)
    return None if parsed is None else f"{parsed[0]}.{parsed[1]}"


# --- adapters: each returns (verdict, version_or_None, data_or_None) ------
# A refusing adapter also leaves the exception on its own `last_error`, so an
# expectation can name the error class and the sentence (`refusal:`), which
# verdict parity alone does not check.

def adapt_reference_python(text):
    cfio._reset_version_warnings()
    adapt_reference_python.last_error = None
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        try:
            data = cfio.loads(text, allow_intermediate=True)
        except cfio.ChipletFormatError as exc:
            adapt_reference_python.last_error = exc
            return ("refuse", None, None)
    warned = any(issubclass(w.category, cfio.FormatVersionWarning) for w in rec)
    return ("warn" if warned else "accept",
            _normalized(data.get("format_version")), data)


def make_ir_adapter(ir):
    def adapt(text):
        import yaml
        adapt.last_error = None
        data = yaml.safe_load(text)
        try:
            asm = ir.parse_assembly(data, allow_intermediate=True)
        except ir.ChipletError as exc:
            # ir's base error is its counterpart of the reference's base (see
            # the manifest header), so a refusal raised as the base is named as
            # the reference's; a subclass (SourceDocumentError) keeps its own.
            if type(exc) is ir.ChipletError:
                named = cfio.ChipletFormatError(str(exc))
                named.__cause__ = exc
                exc = named
            adapt.last_error = exc
            return ("refuse", None, None)
        warned = any("format_version" in w and "newer" in w
                     for w in getattr(asm, "warnings", []))
        return ("warn" if warned else "accept",
                _normalized((data or {}).get("format_version")), None)
    return adapt


# --- checking ------------------------------------------------------------

def check_impl(name, meta, adapt, manifest, *, passthrough_only_ok):
    """Return a list of failure strings (empty == all expectations held)."""
    failures = []
    is_passthrough = meta.get("class") == "passthrough"
    writes = meta.get("writes", False)
    reads_source_text = meta.get("reads_source_text", False)
    checks_pad_usage = meta.get("checks_pad_usage", False)
    refuses_sources = meta.get("refuses_source_documents", False)
    refuses_units = meta.get("refuses_other_units", False)
    refuses_unplaced = meta.get("refuses_unplaced_assemblies", False)
    for entry in manifest["fixtures"]:
        f = entry["file"]
        # A source-text expectation asks about the raw bytes (the top-level block
        # grammar). A reader handed an already-parsed mapping cannot be asked, so
        # it is skipped rather than failed; see the manifest header.
        if entry.get("source_text_only") and not reads_source_text:
            continue
        # Likewise for validation rule 8: a reader that has not adopted it is
        # skipped rather than failed, and says so in the manifest.
        if entry.get("pad_usage_only") and not checks_pad_usage:
            continue
        # And for the source refusal (format 1.1), the same way.
        if entry.get("source_refusal_only") and not refuses_sources:
            continue
        if entry.get("units_refusal_only") and not refuses_units:
            continue
        if entry.get("unplaced_refusal_only") and not refuses_unplaced:
            continue
        text = (FIXTURES / f).read_text(encoding="utf-8")
        verdict, version, data = adapt(text)

        if verdict != entry["expect"]:
            failures.append(f"{name}: {f}: verdict {verdict!r}, "
                            f"expected {entry['expect']!r}")
            continue
        refusal = entry.get("refusal")
        if refusal and verdict == "refuse":
            exc = getattr(adapt, "last_error", None)
            if type(exc).__name__ != refusal["error"]:
                failures.append(f"{name}: {f}: refused with "
                                f"{type(exc).__name__}, expected "
                                f"{refusal['error']}")
            elif refusal["says"] not in str(exc):
                failures.append(f"{name}: {f}: the refusal does not say "
                                f"{refusal['says']!r}")
        if entry["expect"] in ("accept", "warn") and "version" in entry:
            if version is not None and version != entry["version"]:
                failures.append(f"{name}: {f}: version {version!r}, "
                                f"expected {entry['version']!r}")

        # passthrough-only expectations. Verified through the reference_python
        # dict writer (the adapter that returns `data`); a typed implementation
        # is skipped, not failed. ir is passthrough-via-raw and its writer parity
        # is asserted in interposer-pnr's own suite, not re-driven here.
        if data is None:
            continue
        rk = entry.get("roundtrip_key")
        if rk and entry.get("roundtrip_key_passthrough_only") and is_passthrough:
            reloaded = cfio.loads(cfio.dumps(data))
            if rk not in reloaded:
                failures.append(f"{name}: {f}: unknown key {rk!r} dropped on "
                                f"round-trip (passthrough must preserve)")
        if entry.get("writer") == "preserve_input" \
                and entry.get("writer_passthrough_only") and is_passthrough and writes:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                reloaded = cfio.loads(cfio.dumps(data))
            if _normalized(reloaded.get("format_version")) != entry["version"]:
                failures.append(f"{name}: {f}: writer stamped "
                                f"{reloaded.get('format_version')!r}, passthrough "
                                f"must preserve {entry['version']!r}")
    return failures


def run(interposer_pnr_src=None, cpp_bin=None):
    manifest = _load_manifest()
    impls = manifest["implementations"]
    all_failures = []
    ran = []

    all_failures += check_impl("reference_python", impls["reference_python"],
                               adapt_reference_python, manifest,
                               passthrough_only_ok=True)
    ran.append("reference_python")

    # interposer_pnr_ir, when importable
    ir = _try_import_ir(interposer_pnr_src)
    if ir is not None:
        all_failures += check_impl("interposer_pnr_ir", impls["interposer_pnr_ir"],
                                   make_ir_adapter(ir), manifest,
                                   passthrough_only_ok=False)
        ran.append("interposer_pnr_ir")
    else:
        print("SKIP interposer_pnr_ir: not importable "
              "(pass --interposer-pnr <src>)", file=sys.stderr)

    if cpp_bin:
        all_failures += _check_cpp(cpp_bin, manifest)
        ran.append("reference_cpp")
    else:
        print("SKIP reference_cpp: no --cpp-bin / CHIPLET_CFIO_CPP_BIN "
              "(build the C++ reference to include it)", file=sys.stderr)

    return ran, all_failures


def _try_import_ir(src):
    for cand in filter(None, [src,
                              str(HERE.parents[1] / "interposer-pnr" / "src")]):
        if Path(cand, "interposer_pnr", "ir.py").is_file():
            sys.path.insert(0, cand)
            try:
                from interposer_pnr import ir  # type: ignore
                return ir
            except Exception:
                return None
    return None


def _check_cpp(cpp_bin, manifest):
    import subprocess
    failures = []
    cpp = manifest["implementations"]["reference_cpp"]
    reads_source_text = cpp.get("reads_source_text", False)
    checks_pad_usage = cpp.get("checks_pad_usage", False)
    refuses_sources = cpp.get("refuses_source_documents", False)
    refuses_units = cpp.get("refuses_other_units", False)
    refuses_unplaced = cpp.get("refuses_unplaced_assemblies", False)
    for entry in manifest["fixtures"]:
        f = entry["file"]
        if entry.get("source_text_only") and not reads_source_text:
            continue
        if entry.get("pad_usage_only") and not checks_pad_usage:
            continue
        if entry.get("source_refusal_only") and not refuses_sources:
            continue
        if entry.get("units_refusal_only") and not refuses_units:
            continue
        if entry.get("unplaced_refusal_only") and not refuses_unplaced:
            continue
        proc = subprocess.run([cpp_bin, str(FIXTURES / f)],
                              capture_output=True, text=True)
        out = (proc.stdout + proc.stderr).lower()
        verdict = ("refuse" if proc.returncode != 0 else
                   "warn" if "warn" in out else "accept")
        if verdict != entry["expect"]:
            failures.append(f"reference_cpp: {f}: verdict {verdict!r}, "
                            f"expected {entry['expect']!r}")
        elif verdict == "refuse" and entry.get("refusal") and \
                entry["refusal"]["says"].lower() not in out:
            # A binary reports a message, not a class: the sentence is checked.
            failures.append(f"reference_cpp: {f}: the refusal does not say "
                            f"{entry['refusal']['says']!r}")
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interposer-pnr", default=None,
                    help="interposer-pnr src dir (for the ir adapter)")
    ap.add_argument("--cpp-bin", default=os.environ.get("CHIPLET_CFIO_CPP_BIN"))
    args = ap.parse_args(argv)
    ran, failures = run(args.interposer_pnr, args.cpp_bin)
    print(f"ran: {', '.join(ran)}")
    for msg in failures:
        print("FAIL " + msg)
    if failures:
        print(f"{len(failures)} conformance failure(s)")
        return 1
    print("conformance OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
