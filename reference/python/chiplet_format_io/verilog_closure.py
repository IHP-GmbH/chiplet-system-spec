# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Which Verilog sources a design needs, read without a simulator.

A gate-level view of a die is its netlist plus the model of every cell, pad
and primitive the netlist reaches. :func:`units` reads, for each source in
compile order, the modules and primitives it defines and the names each one
instantiates. :func:`closure` walks them from the top design and states which
sources the design needs and which reached names nothing defines.

The reader is narrow on purpose. It takes the shape LibreLane netlists and PDK
cell models have: comments; ``ifdef``, ``ifndef``, ``elsif``, ``else`` and
``endif`` over names ``define``\\ d earlier in the same compile; the
``timescale``, ``celldefine``, ``endcelldefine`` and ``resetall`` directives;
attributes, specify and table blocks; declarations, continuous assignments,
gate primitives and instances. Anything else refuses (any other directive or a
macro use, ``include`` among them; behavioural code, generate, defparam),
because a construct skipped here could instantiate something the walk would
then never see.

The tokenizer is public (:data:`TOKEN`, :data:`UNIT`, :func:`strip`,
:func:`active_text`, :func:`split_statements`, :func:`is_name`), so that another
reader of the same netlists splits them the same way.
"""
from __future__ import annotations

import re
from typing import AbstractSet, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

__all__ = [
    "GATES",
    "DECLARATIONS",
    "REFUSED",
    "HARMLESS",
    "ENDS",
    "TOKEN",
    "UNIT",
    "VerilogClosureError",
    "strip",
    "active_text",
    "split_statements",
    "is_name",
    "units",
    "closure",
]

GATES = frozenset(
    "and nand or nor xor xnor buf not bufif0 bufif1 notif0 notif1 pullup pulldown nmos pmos "
    "rnmos rpmos cmos rcmos tran rtran tranif0 tranif1 rtranif0 rtranif1".split())
DECLARATIONS = frozenset(
    "input output inout wire reg tri tri0 tri1 wand wor triand trior trireg supply0 supply1 "
    "integer real time realtime parameter localparam specparam genvar event assign".split())
#: Keywords that open behavioural or generated code. Some read like an instance
#: (``defparam u.P``, ``always_comb y = a``), so they refuse by name.
REFUSED = frozenset(
    "always always_comb always_ff always_latch initial final function task generate defparam "
    "for if case begin fork".split())
#: Directives that change nothing the walk reads.
HARMLESS = frozenset("timescale celldefine endcelldefine resetall".split())
ENDS = {"module": "endmodule", "macromodule": "endmodule", "primitive": "endprimitive"}

_LEX = re.compile(r'\\\S+|/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"', re.S)
_DIRECTIVE = re.compile(r"`(\w+)")
TOKEN = re.compile(r"\\\S+|\$?[A-Za-z_][A-Za-z0-9_$]*|\d[\w.']*|\S")
_NAME = re.compile(r"\\\S+|[A-Za-z_][A-Za-z0-9_$]*")
_TIMESCALE = re.compile(r"[^`\n]*")
UNIT = re.compile(r"\b(module|macromodule|primitive)\b(.*?)\b(endmodule|endprimitive)\b", re.S)


class VerilogClosureError(Exception):
    """A source this reader does not read whole, or a closure it cannot state."""


def strip(text: str) -> str:
    """Comments out, their newlines kept, and strings emptied. An escaped
    identifier is read first and kept, so a ``//`` or ``"`` inside one is
    part of the name."""
    def blank(match: re.Match) -> str:
        found = match.group(0)
        if found.startswith("\\"):
            return found
        if found.startswith('"'):
            return '""'
        return "\n" * found.count("\n") or " "
    return _LEX.sub(blank, text)


def active_text(text: str, defines: Set[str], where: str) -> str:
    """The text the conditionals leave in. ``define`` and ``undef`` update
    ``defines``, which carries into the next source of the same compile."""
    out: List[str] = []
    stack: List[Tuple[bool, bool, bool]] = []  # (parent active, a branch taken, this branch active)
    active, pos = True, 0
    for match in _DIRECTIVE.finditer(text):
        if match.start() < pos:
            continue  # inside a `define line already consumed
        if active:
            out.append(text[pos:match.start()])
        name = match.group(1)
        line_end = text.find("\n", match.end())
        line_end = len(text) if line_end < 0 else line_end
        argument = re.match(r"[ \t]+([A-Za-z_]\w*)", text[match.end():line_end])
        end = match.end()
        if name in ("ifdef", "ifndef", "elsif", "define", "undef"):
            if argument is None:
                raise VerilogClosureError(f"{where}: `{name} names no macro")
            end += argument.end()
        if name in ("ifdef", "ifndef"):
            hit = active and ((argument.group(1) in defines) != (name == "ifndef"))
            stack.append((active, hit, hit))
        elif name in ("elsif", "else", "endif"):
            if not stack:
                raise VerilogClosureError(f"{where}: `{name} without `ifdef")
            parent, taken, _ = stack.pop()
            if name == "elsif":
                now = parent and not taken and argument.group(1) in defines
                stack.append((parent, taken or now, now))
            elif name == "else":
                stack.append((parent, True, parent and not taken))
        elif name in ("define", "undef"):
            if text[:line_end].rstrip().endswith("\\"):
                raise VerilogClosureError(f"{where}: `{name} {argument.group(1)} runs over several lines")
            if active:
                (defines.add if name == "define" else defines.discard)(argument.group(1))
            if name == "define":
                end = line_end  # the macro body runs to the end of the line
        elif name == "timescale":
            end = _TIMESCALE.match(text, end).end()  # it may share its line with another directive
        elif name not in HARMLESS and active:
            raise VerilogClosureError(f"{where}: `{name} is a directive or macro this reader does not read")
        active = stack[-1][2] if stack else True
        pos = end
    if stack:
        raise VerilogClosureError(f"{where}: an `ifdef is not closed")
    if active:
        out.append(text[pos:])
    return "".join(out)


def split_statements(body: str, where: str) -> List[List[str]]:
    """The body's tokens split at each semicolon outside brackets."""
    out: List[List[str]] = []
    current: List[str] = []
    depth = 0
    for token in TOKEN.findall(body):
        if token in ("(", "[", "{"):
            depth += 1
        elif token in (")", "]", "}"):
            depth -= 1
        if token == ";" and depth == 0:
            out.append(current)
            current = []
        else:
            current.append(token)
    if current:
        raise VerilogClosureError(f"{where}: text with no closing ';': {' '.join(current[:8])}")
    return out


def is_name(token: str) -> bool:
    return _NAME.fullmatch(token) is not None


def _definitions(text: str, defines: Set[str], where: str) -> Dict[str, FrozenSet[str]]:
    kept = active_text(strip(text), defines, where)
    kept = re.sub(r"\(\*\s*[A-Za-z_\\].*?\*\)", " ", kept, flags=re.S)
    kept = re.sub(r"\bspecify\b.*?\bendspecify\b", " ", kept, flags=re.S)
    kept = re.sub(r"\btable\b.*?\bendtable\b", " ", kept, flags=re.S)
    defined: Dict[str, FrozenSet[str]] = {}
    last = 0
    for match in UNIT.finditer(kept):
        outside = kept[last:match.start()].strip()
        if outside:
            raise VerilogClosureError(f"{where}: text outside a module: {outside[:60]!r}")
        last = match.end()
        what = match.group(1)
        statements = split_statements(match.group(2), where)
        if ENDS[what] != match.group(3) or not statements or not statements[0] or not is_name(statements[0][0]):
            raise VerilogClosureError(f"{where}: a {what} with no name or no {ENDS[what]}")
        name = statements[0][0]
        if name in defined:
            raise VerilogClosureError(f"{where}: {name} is defined twice")
        uses: Set[str] = set()
        for statement in statements[1:]:
            head = statement[0]
            if head in REFUSED:
                raise VerilogClosureError(f"{where}: {name} holds {head}, which this reader does not read")
            if head in DECLARATIONS or head in GATES:
                continue
            if is_name(head) and len(statement) > 1 and (statement[1] in ("#", "(") or is_name(statement[1])):
                uses.add(head)
                continue
            raise VerilogClosureError(
                f"{where}: {name} holds a statement this reader does not read: {' '.join(statement[:6])}")
        defined[name] = frozenset(uses)
    outside = kept[last:].strip()
    if outside:
        raise VerilogClosureError(f"{where}: text outside a module: {outside[:60]!r}")
    return defined


def units(sources: Iterable[Tuple[str, str]]) -> List[Dict[str, FrozenSet[str]]]:
    """For each ``(where, text)`` in compile order, the modules and primitives
    it defines, each with the names it instantiates. The define set starts
    empty and carries from one source to the next, as one compile would."""
    defines: Set[str] = set()
    return [_definitions(text, defines, where) for where, text in sources]


def closure(top: str, defined: Sequence[Mapping[str, AbstractSet[str]]],
            names: Optional[Sequence[str]] = None) -> Tuple[List[int], Dict[str, str]]:
    """The indexes of the sources that define something ``top`` reaches, in
    order, and each reached name no source defines with a unit that
    instantiates it. A reached name two sources define refuses: which one a
    compile takes is the tool's choice, not the record's. So does any name two
    needed sources define, reached or not, since a compile reads whole files.
    ``names`` names the sources in a refusal."""
    def twice(name: str, indexes: Sequence[int]) -> VerilogClosureError:
        which = f": {', '.join(names[i] for i in indexes)}" if names else ""
        return VerilogClosureError(f"{name} is defined by {len(indexes)} sources{which}")

    owners: Dict[str, List[int]] = {}
    for index, units_here in enumerate(defined):
        for name in units_here:
            owners.setdefault(name, []).append(index)
    needed: Set[int] = set()
    undefined: Dict[str, str] = {}
    seen: Set[str] = set()
    todo: List[Tuple[str, str]] = [(top, top)]
    while todo:
        name, user = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        if name not in owners:
            undefined[name] = user
            continue
        if len(owners[name]) > 1:
            raise twice(name, owners[name])
        index = owners[name][0]
        needed.add(index)
        todo.extend((used, name) for used in sorted(defined[index][name], reverse=True))
    for name, indexes in sorted(owners.items()):
        both = [index for index in indexes if index in needed]
        if len(both) > 1:
            raise twice(name, both)
    return sorted(needed), undefined
