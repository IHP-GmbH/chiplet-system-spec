# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The interposer LVS reference netlist, written from a ``.chiplet``.

The interposer PDK's LVS deck (``libs.tech/klayout/tech/lvs``) compares a
carrier's labeled connectivity with a reference SPICE netlist. This module
writes that reference from the design, independently of whoever labels the
layout, so the deck can check the two against each other. The file is exactly
three LF-terminated ASCII lines::

    * intended interposer connectivity, from design.chiplet sha256 <hex>
    .SUBCKT <carrier top cell> <port> <port> ...
    .ENDS <carrier top cell>

The header names the SHA-256 of the design file's bytes; the deck's runner
refuses a reference whose design is not the one the labels were written from.
The subckt name is the carrier GDS top cell, taken as given, never derived: a
wrong one stops the deck rather than passing. Today's carriers carry no
devices, so the subckt holds ports only.

The ports are the net set: every net that lands on the carrier, which is a
net with a netlist connection to a ``die`` mounted ``flip_chip``, or a net that
an io_pad of the interposer names. The label writer labels exactly this set.
Anything the rule does not cover is refused rather than guessed: a connection to
any other component, an external netlist, more than one interposer. Ports are
sorted by byte order, on one line, with no ``+`` continuation.

Names are written raw and never escaped. KLayout's SPICE reader folds case and
keeps every printable ASCII character inside a name except whitespace, ``=``,
quotes and ``\\``; a leading ``*`` ends the line, and a ``PARAMS:`` prefix is a
reader error. A name the reader would not keep as written is refused, and so
are two names equal but for case, which the reader would merge into one port.
The top cell is held to the stricter set a subckt name keeps.

Usage::

    python -m chiplet_system_description.interposer_reference --design design.chiplet \\
        --top INTERPOSER --out intended_interposer.cir

The output is created, never overwritten. Exit status: 0 written, 1 internal
error, 2 usage, 3 refused.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

from ._cfio import cfio
from .connectivity import refusal
from .errors import SystemSpecError

HEADER = "* intended interposer connectivity, from design.chiplet sha256 "

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3

#: Printable ASCII but space, ``=``, the quotes and the backslash.
_PORT_RE = re.compile(r"[!#-&(-<>-\[\]-~]+\Z")
_TOP_RE = re.compile(r"[A-Za-z0-9_./:\[\]]+\Z")


class InterposerReferenceRefused(SystemSpecError):
    """No reference can be written. ``refusals`` lists every reason."""

    def __init__(self, refusals: List[Dict[str, Any]]):
        self.refusals = list(refusals)
        super().__init__("; ".join(r["message"] for r in self.refusals))

    @property
    def codes(self) -> List[str]:
        return sorted({r["code"] for r in self.refusals})


def _params_prefix(name: str) -> bool:
    return name.upper().startswith("PARAMS:")


def net_set(doc: Dict[str, Any], out: List[dict]) -> List[str]:
    """The nets that land on the carrier, sorted by byte order. Refusals go to ``out``."""
    comps = doc.get("components") or []
    by_id: Dict[str, Dict[str, Any]] = {}
    for comp in comps:
        if comp["id"] in by_id:
            out.append(refusal("component_repeated", f"two components are named {comp['id']!r}"))
        by_id.setdefault(comp["id"], comp)
    carriers = [comp for comp in comps if comp["type"] == "interposer"]
    if len(carriers) != 1:
        out.append(refusal("interposer_count", f"the design has {len(carriers)} interposers; "
                           f"the net set is read from exactly one"))
    netlist = doc.get("netlist") or {}
    if not isinstance(netlist, dict):
        out.append(refusal("design_unreadable", "netlist is not a mapping"))
        netlist = {}
    rows = netlist.get("nets") or []
    if not isinstance(rows, list):
        out.append(refusal("design_unreadable", "netlist.nets is not a list"))
        rows = []
    if netlist.get("external_netlist"):
        out.append(refusal("external_netlist", f"the nets are kept in "
                           f"{netlist['external_netlist']!r}, which this writer does not read"))
    names, ports, owner = set(), set(), {}
    for net in rows:
        name = net.get("name") if isinstance(net, dict) else None
        conns = net.get("connections") if isinstance(net, dict) else None
        if not isinstance(name, str) or not name:
            out.append(refusal("net_unnamed", f"a netlist net has no name: {net!r}"))
            continue
        if name in names:
            out.append(refusal("net_repeated", f"two netlist nets are named {name!r}"))
        names.add(name)
        if not isinstance(conns, (list, type(None))):
            out.append(refusal("design_unreadable", f"net {name!r}: connections is not a list"))
            continue
        for conn in conns or []:
            comp = by_id.get(conn.get("component")) if isinstance(conn, dict) else None
            if comp is None or not isinstance(conn.get("pin"), str) or not conn["pin"]:
                out.append(refusal("connection_unknown", f"net {name!r} connects {conn!r}, "
                                   f"which names no component and pin"))
                continue
            terminal = (comp["id"], conn["pin"])
            if terminal in owner and owner[terminal] != name:
                out.append(refusal("terminal_on_two_nets", f"{terminal[0]}.{terminal[1]} is on "
                                   f"net {owner[terminal]!r} and on net {name!r}"))
            owner.setdefault(terminal, name)
            if comp["type"] == "die" and comp.get("orientation") == "flip_chip":
                ports.add(name)
            else:
                out.append(refusal(
                    "connection_off_carrier",
                    f"net {name!r} connects {comp['id']} (type {comp['type']!r}, orientation "
                    f"{comp.get('orientation')!r}); only a flip_chip die lands on the carrier"))
    for carrier in carriers:
        pads = carrier.get("io_pads") or []
        if not isinstance(pads, list) or not all(isinstance(pad, dict) for pad in pads):
            out.append(refusal("design_unreadable", f"{carrier['id']}: io_pads is not a list "
                               f"of mappings"))
            continue
        for pad in pads:
            net = pad.get("net")
            if net is None or net == "":
                continue  # a pad with no net is no landing of any net
            if not isinstance(net, str):
                out.append(refusal("design_unreadable", f"io_pad {pad.get('id')!r} has the net {net!r}"))
                continue
            if net not in names:
                out.append(refusal("io_pad_net_unknown", f"io_pad {pad.get('id')!r} names the "
                                   f"net {net!r}, which no netlist net carries"))
            ports.add(net)
    return sorted(ports, key=lambda name: name.encode("utf-8"))


def _check_names(top: str, ports: List[str], out: List[dict]) -> None:
    if not _TOP_RE.match(top) or _params_prefix(top):
        out.append(refusal("top_cell_name", f"top cell {top!r}: a subckt name keeps only "
                           f"letters, digits and _ . / : [ ], with no PARAMS: prefix"))
    folded: Dict[str, List[str]] = {}
    for name in ports:
        if not _PORT_RE.match(name) or name.startswith("*") or _params_prefix(name):
            out.append(refusal("port_name", f"net {name!r} cannot be a port as written: "
                               f"KLayout's SPICE reader would not keep it"))
        folded.setdefault(name.upper(), []).append(name)
    for names in folded.values():
        if len(names) > 1:
            out.append(refusal("port_case_collision", f"nets {names!r} differ only in case; "
                               f"the reader would read them as one port"))
    if not ports:
        out.append(refusal("no_ports", "no net lands on the carrier, so there is nothing to compare"))


def reference_text(raw: bytes, top: str) -> str:
    """The reference netlist for the design whose file bytes are ``raw``."""
    out: List[dict] = []
    try:
        doc = cfio.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError, cfio.ChipletFormatError) as exc:
        raise InterposerReferenceRefused([refusal("design_unreadable", str(exc))]) from None
    ports = net_set(doc, out)
    _check_names(top, ports, out)
    if out:
        raise InterposerReferenceRefused(out)
    sha = hashlib.sha256(raw).hexdigest()
    return f"{HEADER}{sha}\n.SUBCKT {' '.join([top, *ports])}\n.ENDS {top}\n"


def write_reference(design, top: str, out) -> Path:
    """Write the reference for ``design`` to ``out``, which must not exist yet."""
    out = Path(out)
    text = reference_text(Path(design).read_bytes(), top)
    with open(out, "x", encoding="ascii", newline="\n") as fh:
        fh.write(text)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m chiplet_system_description.interposer_reference",
        description="Write the interposer LVS reference netlist from a .chiplet.")
    ap.add_argument("--design", required=True, help="the design.chiplet")
    ap.add_argument("--top", required=True, help="the carrier GDS top cell, as the LVS run names it")
    ap.add_argument("--out", required=True, help="the reference .cir to create")
    args = ap.parse_args(argv)
    try:
        out = write_reference(args.design, args.top, args.out)
    except InterposerReferenceRefused as exc:
        for r in exc.refusals:
            print(f"refused ({r['code']}): {r['message']}", file=sys.stderr)
        return EXIT_REFUSED
    except FileExistsError:
        print(f"refused (output_exists): {args.out} exists; the reference is never overwritten",
              file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
    print(f"written: {out}")
    return EXIT_OK


__all__ = [
    "HEADER",
    "EXIT_OK",
    "EXIT_INTERNAL",
    "EXIT_USAGE",
    "EXIT_REFUSED",
    "InterposerReferenceRefused",
    "net_set",
    "reference_text",
    "write_reference",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
