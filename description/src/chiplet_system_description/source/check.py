# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""``chiplet-system-description check``: tiers 0 and 1 over a source, as ``file:line: words``.

Tier 0 reads the file alone; tier 1 also reads each die folder and starts no EDA
tool. Tier 1 runs only on a file tier 0 passes, since its findings would
otherwise be about items tier 0 could not read. A source that leaves its
placement to ``packaging-flow place`` (format 1.3) is checked as it stands, so
it can be validated before it is placed: each unplaced die, a carrier with no
size and the pins no edge carries yet are notes, and every other rule runs on
what is placed. Ports the file leaves
unaccounted are printed as lines ready to paste, grouped under the block each
most likely goes in; ``check`` itself never writes the file.

Exit 0: no refusing finding (notes may be printed). Exit 1: findings. Exit 2:
the command line is wrong. With ``--json`` the same is one document on stdout:
``{"format": "chiplet_system.check", "version": 1, "file", "findings",
"suggestions"}``, each suggestion one record per port
``{"port", "block", "line", "replace_line", "choice"}``; records that share a
``choice`` sentence are alternatives, of which one is pasted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, TextIO, Tuple

from .document import SYSTEM_KEYS, Finding, check_text
from .plan import Plan, Suggestion, plan, unaccounted_ports

#: The report's format id and version. The id keeps the package's old name on
#: purpose: it is a format identifier, and renaming it is a format change. A
#: reader that compares them imports these names rather than the literals.
REPORT_FORMAT = "chiplet_system.check"
REPORT_VERSION = 1

__all__ = ["REPORT_FORMAT", "REPORT_VERSION", "main", "paste_block", "report", "run"]


def run(path: Path, *, tier: int = 1) -> Tuple[List[Finding], Tuple[Suggestion, ...]]:
    """Every finding of ``path`` up to ``tier``, and the suggested lines."""
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Finding(0, f"cannot be read: {exc}")], ()
    source, findings = check_text(text, path)
    if source is None or tier < 1 or any(not f.note for f in findings):
        return findings, ()
    topology: Plan = plan(source, unplaced=True)
    return [*findings, *topology.findings], topology.suggestions


def paste_block(path: Path, suggestions: Sequence[Suggestion]) -> List[str]:
    """The lines ready to paste, grouped by block, in the system block's order."""
    if not suggestions:
        return []
    count = len(unaccounted_ports(suggestions))
    out = [f"{path}: {count} die port(s) are not accounted for (tier 1). "
           "Paste each line into its block:"]
    groups: Dict[Tuple[str, Optional[int]], List[str]] = {}
    choices: Dict[str, List[Suggestion]] = {}
    for s in suggestions:
        if s.choice:
            choices.setdefault(s.choice, []).append(s)
        else:
            groups.setdefault((s.block, s.replace_line), []).append(s.line)
    order = SYSTEM_KEYS + ("unplaced",)  # a root key, written after system
    for (block, replace), lines in sorted(
            groups.items(), key=lambda kv: (order.index(kv[0][0]), kv[0][1] or 0)):
        if replace:
            out.append(f"  {block}, replace line {replace}:")
        elif block == "unplaced":
            out.append("  unplaced, a top-level key after system:")
        else:
            out.append(f"  {block}:")
        out.extend(lines)
    for sentence, alternatives in choices.items():
        out.append(f"  {sentence} Paste one of:")
        for s in alternatives:
            out.append(f"  {s.block}, replace line {s.replace_line}:")
            out.append(s.line)
    return out


def report(path: Path, findings: Sequence[Finding],
           suggestions: Sequence[Suggestion]) -> Dict[str, object]:
    return {
        "format": REPORT_FORMAT, "version": REPORT_VERSION, "file": str(path),
        "findings": [{"line": f.line, "tier": f.tier, "note": f.note, "message": f.message}
                     for f in findings],
        "suggestions": [record for s in suggestions for record in s.records()],
    }


def main(argv: Optional[Sequence[str]] = None, out: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="chiplet-system-description", description="Read and check a source system.chiplet.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="tiers 0 and 1: the file, then its die folders")
    check.add_argument("file", type=Path)
    check.add_argument("--json", action="store_true", help="one JSON report on stdout")
    check.add_argument("--tier", type=int, choices=(0, 1), default=1,
                       help="stop after this tier (default 1)")
    args = parser.parse_args(argv)

    findings, suggestions = run(args.file, tier=args.tier)
    refused = any(not f.note for f in findings)
    if args.json:
        json.dump(report(args.file, findings, suggestions), out, indent=2)
        out.write("\n")
    else:
        for f in findings:
            out.write(f.render(args.file) + "\n")
        for line in paste_block(args.file, suggestions):
            out.write(line + "\n")
    return 1 if refused else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
