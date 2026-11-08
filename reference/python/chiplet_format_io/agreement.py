# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The agreement between the SPICE a block simulates and the CDL its LVS read.

:func:`agree` reads the deck text of a ``spice`` transistor view against the
CDL files an LVS run took as its reference, and returns an :class:`Agreement`
whose findings are empty when the two agree. What that backs is narrow: the
simulated netlist names the same devices on the same nets as the LVS
reference, with MOS, resistor and capacitor sizes equal. Tap and antenna-diode
geometry is not compared, so tap body-tie resistance and antenna-diode
capacitance follow the schematic's sizes, not the layout's. Nothing here is a
claim about the layout or its LVS.

The rule, with names compared ASCII case-folded:

- The SPICE top pairs with the CDL subckt of the same name, else with the
  CDL's one uncalled subckt. Every other SPICE subckt pairs with the CDL subckt
  of its name, both read through the mapping :func:`deck_text` applies. Only
  the CDL subckts reachable from the paired top are read.
- Ports compare as a set of names; their order is counted, not judged.
- A SPICE ``X`` card on a device of the technology's table (:data:`DEVICES`)
  pairs with the CDL card of the device's letter and the same rest of label
  (``XM1`` with ``MM1``); an ``X`` card on a local subckt pairs with the CDL
  card of its own label, and their nets compare by port name.
- MOS, resistor and capacitor cards compare nets in pin order, the model, and
  every parameter as an exact decimal with its scale suffix applied (``3.0u``
  equals ``3u``). A resistor's CDL body is its ``$SUB=`` net; its CDL value is
  derived from ``w``, ``l``, ``b`` and ``m`` by the PDK symbol and is counted,
  not compared: :data:`NOT_CLAIMED` names it, and no PDK formula is evaluated.
- Taps compare their two nets as an unordered pair, antenna diodes in order;
  neither compares its geometry (``A``, ``P``, ``w``, ``l``, nor a tap's ``R``).
- ``mm_ok`` is simulation only and is dropped. A missing ``m`` reads as 1.

A CDL that a reviewed amendment changed on purpose is read with that
amendment's receipt (``cdl-reference-amendment/1``): each card the receipt
names is checked to be exactly one change of a known kind at its stated line,
put back as it was, and the rule above runs unchanged. The result then names
the receipt by sha256.

A refusal is :class:`AgreementRefused`, a
:class:`~chiplet_format_io.transistor_view.TransistorViewRefused` whose
``reason`` is one word of :data:`REASONS`.
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from .technology import TECHNOLOGY_OF_PDK as _TECHNOLOGY_OF_PDK
from .transistor_view import (TransistorView, TransistorViewRefused, _file, _fold, _map, _tokens, _VALUE,
                              _WHITE, _json, card_word, instance_subckt, subckt_ports, transistor_view)

__all__ = [
    "DEVICES",
    "SIM_ONLY",
    "NOT_CLAIMED",
    "AMENDMENT_FORMAT",
    "AMENDMENT_KINDS",
    "REASONS",
    "AgreementRefused",
    "Agreement",
    "agree",
    "agree_record",
]

#: Per technology, each PDK device a SPICE ``X`` card may name: the letter its
#: CDL card takes, its SPICE pin count, and the form the two cards compare in.
DEVICES: Dict[str, Dict[str, Tuple[str, int, str]]] = {
    "sg13g2": {
        "sg13_lv_nmos": ("M", 4, "sized"), "sg13_lv_pmos": ("M", 4, "sized"),
        "sg13_hv_nmos": ("M", 4, "sized"), "sg13_hv_pmos": ("M", 4, "sized"),
        "rsil": ("R", 3, "resistor"), "rppd": ("R", 3, "resistor"), "rhigh": ("R", 3, "resistor"),
        "cap_cmim": ("C", 2, "sized"),
        "dantenna": ("D", 2, "diode"), "dpantenna": ("D", 2, "diode"),
        "ntap1": ("R", 2, "tap"), "ptap1": ("R", 2, "tap"),
    },
}
#: Parameters only a simulator reads.
SIM_ONLY = ("mm_ok",)
#: What an agreement does not cover, listed on every result so that no one reads
#: ``holds`` as covering it.
NOT_CLAIMED = (
    "tap and antenna-diode geometry: not compared",
    "resistor CDL value: derived value, not compared",
    "the layout and its LVS: no claim",
)
#: The geometry each form leaves uncompared.
_GEOMETRY = {"tap": ("a", "p", "w", "l", "r"), "diode": ("a", "p", "w", "l")}

AMENDMENT_FORMAT = "cdl-reference-amendment/1"
#: The changes a receipt card may name: a MOS bulk moved to a net, one pin added
#: to an X card, and the matching port added to that subckt.
AMENDMENT_KINDS = ("bulk", "instance_pin", "subckt_pin")

#: The words a refusal's ``reason`` takes.
REASONS = (
    "dialect",      # the view is not a spice view
    "technology",   # no device table for the view's PDK
    "cdl_files",    # no CDL file, or one that is not {file, sha256}
    "digest",       # a file is not the bytes its sha256 binds
    "encoding",     # a file is not strict UTF-8
    "cdl_syntax",   # a card the reader does not know in a CDL subckt it reads
    "cdl_top",      # no CDL subckt pairs with the SPICE top, or a name is defined twice
    "amendment",    # a receipt that does not read, or does not fit these CDL files exactly
)

_SUFFIX = {"t": 12, "g": 9, "meg": 6, "k": 3, "m": -3, "u": -6, "n": -9, "p": -12, "f": -15}
_NUMBER = re.compile(r"([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+))(?:e([-+]?[0-9]+))?(meg|[tgkmunpf])?", re.ASCII)
_DOLLAR_MODEL = re.compile(r"\$\[([^\]]+)\]")
_COUNTS = ("cards", "params", "sim_only_dropped", "geometry_not_compared", "m_read_as_1",
           "resistor_value_not_compared", "port_order_differs", "subckts", "cdl_slash_dropped")


class AgreementRefused(TransistorViewRefused):
    """The agreement cannot be read; ``reason`` is one of :data:`REASONS`."""


class Agreement(NamedTuple):
    """The findings of SPICE against CDL, the receipt sha256 an amendment was
    read with, and what the comparison counted. :data:`NOT_CLAIMED` goes with it."""
    findings: List[str]
    amendment: Optional[str]
    counts: Dict[str, int]

    @property
    def holds(self) -> bool:
        return not self.findings

    @property
    def not_claimed(self) -> Tuple[str, ...]:
        return NOT_CLAIMED

    @property
    def word(self) -> str:
        if self.findings:
            return "does not hold"
        if self.amendment:
            return f"holds with a named reference amendment {self.amendment}"
        return "holds"


def _refuse(reason: str, message: str) -> AgreementRefused:
    return AgreementRefused(reason, message)


def _bytes(root: Path, entry: Mapping[str, Any], what: str, shape: str) -> bytes:
    """The bytes ``entry`` names under ``root``, re-hashed; a path that is not one refuses as ``shape``."""
    try:
        return _file(root, entry.get("file"), entry.get("sha256"), what)
    except TransistorViewRefused as exc:
        raise _refuse(shape if exc.reason == "view_shape" else exc.reason, str(exc).split(": ", 1)[1]) from exc


# ----------------------------------------------------------------------
# Reading both sides
# ----------------------------------------------------------------------
def _number(text: str) -> Optional[Decimal]:
    m = _NUMBER.fullmatch(_fold(text))
    if not m:
        return None
    return Decimal(m.group(1)).scaleb(int(m.group(2) or 0) + _SUFFIX.get(m.group(3) or "", 0))


def _same(a: str, b: str) -> bool:
    na, nb = _number(a), _number(b)
    return na == nb if na is not None and nb is not None else _fold(a) == _fold(b)


def _blocks(lines: Sequence[str], where: str) -> List[Tuple[str, List[str], List[List[str]]]]:
    """(name, ports, cards) of each subckt in ``lines``: comment lines dropped,
    ``+`` lines joined, cards outside a subckt left out."""
    joined: List[str] = []
    for raw in lines:
        line = raw.strip(_WHITE)
        if not line or line.startswith("*"):
            continue
        if line.startswith("+") and joined:
            joined[-1] += " " + line[1:]
        else:
            joined.append(line)
    found: List[Tuple[str, List[str], List[List[str]]]] = []
    current: Optional[Tuple[str, List[str], List[List[str]]]] = None
    for card in joined:
        tokens = _tokens(card)
        word = card_word(tokens[0])
        if word == ".subckt":
            if current is not None:
                raise _refuse("cdl_syntax", f"{where} defines a subckt inside {current[0]}")
            name, ports = subckt_ports(card)
            current = (name, ports, [])
            found.append(current)
        elif word == ".ends":
            current = None
        elif current is not None:
            current[2].append(tokens)
    return found


def _cdl_card(tokens: List[str], counts: Dict[str, int], where: str) -> List[str]:
    """A CDL card as the sg13g2 LVS reads it: ``/`` dropped, ``$[model]`` unwrapped,
    ``$SUB=net`` kept as the parameter ``$sub``. Any other ``$`` refuses."""
    out = []
    for token in tokens:
        if token == "/":
            counts["cdl_slash_dropped"] += 1
            continue
        token = _DOLLAR_MODEL.fullmatch(token).group(1) if _DOLLAR_MODEL.fullmatch(token) else token
        if token.startswith("$") and not _fold(token).startswith("$sub="):
            raise _refuse("cdl_syntax", f"{where}: {' '.join(tokens)!r} holds {token!r}")
        out.append(token)
    if card_word(out[0]).startswith("."):
        raise _refuse("cdl_syntax", f"{where}: {' '.join(tokens)!r} is a dot card inside a subckt")
    return out


def _split(tokens: List[str]) -> Tuple[str, List[str], Dict[str, str]]:
    """(model or subckt, nets, parameters) of an element card."""
    if card_word(tokens[0])[0] == "x":
        k = instance_subckt(tokens)
        plain = list(range(1, k + 1))
    else:
        plain = [i for i, t in enumerate(tokens) if i > 0 and "=" not in t]
        k = plain[-1] if plain else 0
    params = {_fold(t.split("=", 1)[0]): t.split("=", 1)[1] for t in tokens[1:] if "=" in t}
    return (tokens[k] if k else ""), [tokens[i] for i in plain[:-1]], params


# ----------------------------------------------------------------------
# The comparison
# ----------------------------------------------------------------------
def _compare(deck_text: str, spice_top: str, cdl_texts: Sequence[Tuple[str, str]],
             table: Mapping[str, Tuple[str, int, str]]) -> Tuple[List[str], Dict[str, int], str]:
    counts = {k: 0 for k in _COUNTS}
    findings: List[str] = []
    spice = {_fold(n): (n, p, c) for n, p, c in _blocks(deck_text.split("\n"), "the deck text")}
    cdl: Dict[str, List[Tuple[str, List[str], List[List[str]], str]]] = {}
    for name, text in cdl_texts:
        for sub, ports, body in _blocks(text.split("\n"), name):
            cdl.setdefault(_fold(_map(sub)), []).append((sub, ports, body, name))
    called = {_fold(_map(_split(t)[0])) for blocks in cdl.values() for b in blocks for t in b[2]
              if card_word(t[0])[0] == "x" and t[1:]}
    top = _fold(spice_top)
    if top not in cdl:
        uncalled = [k for k in cdl if k not in called]
        if len(uncalled) != 1:
            raise _refuse("cdl_top", f"no CDL subckt is named {spice_top} and {len(uncalled)} are uncalled")
        cdl_top = uncalled[0]
    else:
        cdl_top = top
    reach, todo = [], [cdl_top]
    while todo:
        key = todo.pop(0)
        if key in reach or key not in cdl:
            continue
        if len(cdl[key]) > 1:
            raise _refuse("cdl_top", f"the CDL defines {cdl[key][0][0]} {len(cdl[key])} times")
        reach.append(key)
        sub, ports, body, name = cdl[key][0]
        cdl[key][0] = (sub, ports, [_cdl_card(t, counts, f"{name}: {sub}") for t in body], name)
        todo += [_fold(_map(_split(t)[0])) for t in cdl[key][0][2] if card_word(t[0])[0] == "x"]
    pairs = {top: cdl_top}
    for key, (name, _, _) in spice.items():
        if key != top:
            if key in reach:
                pairs[key] = key
            else:
                findings.append(f"subckt {name}: no CDL subckt of that name")
    findings += [f"subckt {cdl[k][0][0]}: CDL only" for k in reach if k not in pairs.values()]
    counts["subckts"] = len(pairs)
    for skey, ckey in pairs.items():
        sname, sports, scards = spice[skey]
        _, cports, ccards, _ = cdl[ckey][0]
        if sorted(map(_fold, sports)) != sorted(map(_fold, cports)):
            findings.append(f"subckt {sname}: ports {sports} vs {cports}")
        elif list(map(_fold, sports)) != list(map(_fold, cports)):
            counts["port_order_differs"] += 1
        by_label: Dict[str, List[List[str]]] = {}
        for t in ccards:
            by_label.setdefault(_fold(t[0]), []).append(t)
        for t in scards:
            findings += _card(t, sname, by_label, table, spice, cdl, counts)
        findings += [f"{sname}/{t[0]}: CDL only" for rest in by_label.values() for t in rest]
    return findings, counts, cdl[cdl_top][0][0]


def _card(t: List[str], sname: str, by_label: Dict[str, List[List[str]]],
          table: Mapping[str, Tuple[str, int, str]], spice: Mapping[str, Any], cdl: Mapping[str, Any],
          counts: Dict[str, int]) -> List[str]:
    """The findings of one SPICE card against its CDL card, which it takes out of ``by_label``."""
    here = f"{sname}/{t[0]}"
    name, nets, params = _split(t)
    entry = table.get(_fold(name))
    if card_word(t[0])[0] != "x" or (entry is None and _fold(name) not in spice):
        return [f"{here}: not covered (card {t[0][0]} on {name})"]
    want = _fold(entry[0] + t[0][1:]) if entry else _fold(t[0])
    match = by_label.pop(want, None)
    if not match:
        return [f"{here}: no CDL card {want}"]
    found = [f"{here}: {len(match)} CDL cards {want}"] if len(match) > 1 else []
    counts["cards"] += 1
    cname, cnets, cparams = _split(match[0])
    if _fold(cname) != _fold(name):
        found.append(f"{here}: model {name} vs {cname}")
    form = entry[2] if entry else None
    if form == "resistor":
        if len(cnets) == 3 and _VALUE.fullmatch(cnets[2]):
            counts["resistor_value_not_compared"] += 1
            cnets = cnets[:2]
        if "$sub" in cparams:
            cnets = cnets + [cparams.pop("$sub")]
    if entry is None:
        sn = dict(zip(map(_fold, spice[_fold(name)][1]), map(_fold, nets)))
        cports = cdl[_fold(name)][0][1] if _fold(name) in cdl else []
        cn = dict(zip(map(_fold, cports), map(_fold, cnets)))
        if len(nets) != len(spice[_fold(name)][1]) or len(cnets) != len(cports):
            found.append(f"{here}: {len(nets)} nets on {len(spice[_fold(name)][1])} ports vs "
                         f"{len(cnets)} on {len(cports)}")
        elif sn != cn:
            found.append(f"{here}: nets by port {sn} vs {cn}")
    elif len(nets) != entry[1] or (sorted if form == "tap" else list)(map(_fold, nets)) != \
            (sorted if form == "tap" else list)(map(_fold, cnets)):
        found.append(f"{here}: nets {nets} vs {cnets}")
    for key in SIM_ONLY:
        counts["sim_only_dropped"] += params.pop(key, None) is not None
    for key in _GEOMETRY.get(form, ()):
        counts["geometry_not_compared"] += (params.pop(key, None) is not None) + (cparams.pop(key, None) is not None)
    if entry:
        for side in (params, cparams):
            if "m" not in side:
                side["m"] = "1"
                counts["m_read_as_1"] += 1
    for key in sorted(set(params) | set(cparams)):
        counts["params"] += 1
        if key not in params or key not in cparams:
            found.append(f"{here}: param {key} only in {'SPICE' if key in params else 'CDL'}")
        elif not _same(params[key], cparams[key]):
            found.append(f"{here}: param {key}={params[key]} vs {cparams[key]}")
    return found


# ----------------------------------------------------------------------
# A reference amendment
# ----------------------------------------------------------------------
def _enclosing(lines: Sequence[str], at: int) -> Optional[str]:
    """The subckt physical line ``at`` (0-based) stands in, or a .subckt line's own name."""
    current = None
    for k, raw in enumerate(lines[:at + 1]):
        tokens = _tokens(raw)
        word = card_word(tokens[0]) if tokens else ""
        if word == ".subckt" and len(tokens) > 1:
            current = tokens[1]
        elif word == ".ends" and k < at:
            current = None
    return current


def _inserted(after: List[str], before: List[str], net: str, start: int, stop: int) -> List[int]:
    """The positions in ``start..stop`` where ``after`` is ``before`` with ``net`` inserted."""
    return [i for i in range(start, min(stop, len(after))) if after[i] == net
            and after[:i] + after[i + 1:] == before]


def _check_card(card: Any, lines: List[str], pins: Dict[str, List[int]]) -> Tuple[str, int]:
    """One receipt card's kind and its 0-based line, after checking it is exactly
    one change of that kind at that line. ``pins`` gathers the pin positions."""
    keys = ("kind", "amended_line", "subckt", "element", "net", "before", "after")
    if not isinstance(card, Mapping) or not all(k in card for k in keys) \
            or not all(isinstance(card[k], str) and card[k] for k in keys if k != "amended_line"):
        raise _refuse("amendment", f"a card is not {{{', '.join(keys)}}}: {card!r}")
    kind, line = card["kind"], card["amended_line"]
    if kind not in AMENDMENT_KINDS or not isinstance(line, int) or isinstance(line, bool) \
            or not 1 <= line <= len(lines):
        raise _refuse("amendment", f"card {card['element']}: kind {kind!r} or line {line!r} is not one")
    at = line - 1
    following = lines[at + 1].strip(_WHITE) if at + 1 < len(lines) else ""
    if lines[at] != card["after"] or following.startswith("+"):
        raise _refuse("amendment", f"line {line} is not the card's 'after' text as one whole card")
    if _fold(_enclosing(lines, at) or "") != _fold(card["subckt"]):
        raise _refuse("amendment", f"line {line} is not in subckt {card['subckt']}")
    before = [t for t in _tokens(card["before"]) if t != "/"]
    after = [t for t in _tokens(card["after"]) if t != "/"]
    if not before or before[0].startswith(("*", "+")) or _fold(after[0]) != _fold(card["element"]):
        raise _refuse("amendment", f"line {line}: 'before' is no card, or 'after' is not {card['element']}")
    net = card["net"]
    if kind == "bulk":
        ok = card_word(after[0])[0] == "m" and len(after) == len(before) > 4 and after[4] == net \
            and [i for i, (a, b) in enumerate(zip(after, before)) if a != b] == [4]
    elif kind == "subckt_pin":
        at_pin = _inserted(after, before, net, 2, len(after))
        ok = card_word(after[0]) == ".subckt" and _fold(after[1]) == _fold(card["subckt"]) \
            and len(at_pin) == 1 and _fold(net) not in map(_fold, before[2:])
        if ok:
            pins.setdefault(_fold(card["subckt"]), []).append(at_pin[0] - 2)
    else:
        of = card.get("of")
        at_pin = _inserted(after, before, net, 1, instance_subckt(after))
        ok = card_word(after[0])[0] == "x" and isinstance(of, str) and bool(at_pin) \
            and _fold(after[instance_subckt(after)]) == _fold(of)
        if ok:
            pins.setdefault("x " + _fold(of), []).append(frozenset(i - 1 for i in at_pin))
    if not ok:
        raise _refuse("amendment", f"line {line}: {card['before']!r} to {card['after']!r} is not one {kind} "
                                   f"change to {net}")
    return kind, at


def _revert(data: bytes, texts: List[Tuple[str, str, str]]) -> Tuple[List[Tuple[str, str]], str]:
    """The CDL texts with every card the receipt names put back, and its macro."""
    try:
        receipt = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise _refuse("amendment", f"the receipt is not JSON: {exc}") from exc
    if not isinstance(receipt, dict) or receipt.get("format") != AMENDMENT_FORMAT \
            or receipt.get("mechanical_diff") != "ALLOWED" or not isinstance(receipt.get("macro"), str) \
            or not isinstance(receipt.get("amended"), Mapping) or not isinstance(receipt.get("cards"), list) \
            or not isinstance(receipt.get("counts"), Mapping):
        raise _refuse("amendment", f"not a {AMENDMENT_FORMAT} receipt with mechanical_diff ALLOWED")
    which = [k for k, (_, _, sha) in enumerate(texts) if sha == receipt["amended"].get("sha256")]
    if len(which) != 1:
        raise _refuse("amendment", "the receipt was not written for these CDL files: "
                                   f"{len(which)} of them are its amended.sha256")
    name, text, _ = texts[which[0]]
    lines = text.split("\n")
    tally = {kind: 0 for kind in AMENDMENT_KINDS}
    pins: Dict[str, List[Any]] = {}
    seen = set()
    for card in receipt["cards"]:
        kind, at = _check_card(card, lines, pins)
        if at in seen:
            raise _refuse("amendment", f"two cards name line {at + 1}")
        seen.add(at)
        tally[kind] += 1
    for key, wanted in pins.items():
        if key.startswith("x ") and any(len(pins.get(key[2:], [])) != 1 or pins[key[2:]][0] not in w
                                        for w in wanted):
            raise _refuse("amendment", f"an instance_pin of {key[2:]} has no subckt_pin at its position")
    if dict(receipt["counts"]) != tally:
        raise _refuse("amendment", f"counts {dict(receipt['counts'])} are not the cards' {tally}")
    for card in receipt["cards"]:
        lines[card["amended_line"] - 1] = card["before"]
    out = [(n, t) for n, t, _ in texts]
    out[which[0]] = (name, "\n".join(lines))
    return out, receipt["macro"]


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------
def agree(view: TransistorView, cdl: Sequence[Mapping[str, Any]], *,
          amendment: Optional[Mapping[str, Any]] = None) -> Agreement:
    """The agreement of ``view``, a ``spice`` view as :func:`transistor_view`
    read it, with the CDL files ``cdl`` (``{file, sha256}`` each, relative to the
    view's root, in the order LVS read them). ``amendment``, a ``{file, sha256}``
    of a ``cdl-reference-amendment/1`` receipt, is read when given."""
    if view.view.get("dialect") != "spice":
        raise _refuse("dialect", f"a {view.view.get('dialect')} view does not simulate")
    technology = _TECHNOLOGY_OF_PDK.get(view.view["pdk"]["name"])
    if technology not in DEVICES:
        raise _refuse("technology", f"no device table for {view.view['pdk']['name']}")
    if not cdl or not all(isinstance(e, Mapping) and isinstance(e.get("sha256"), str) for e in cdl):
        raise _refuse("cdl_files", "cdl is not a list of {file, sha256}")
    texts = []
    for entry in cdl:
        data = _bytes(view.root, entry, "a cdl file", "cdl_files")
        try:
            texts.append((str(entry["file"]), data.decode("utf-8", errors="strict"), entry["sha256"]))
        except UnicodeDecodeError as exc:
            raise _refuse("encoding", f"{entry['file']} is not UTF-8: {exc}") from exc
    read, macro, sha = [(n, t) for n, t, _ in texts], None, None
    if amendment is not None:
        if not isinstance(amendment, Mapping) or not isinstance(amendment.get("sha256"), str):
            raise _refuse("amendment", "amendment is not {file, sha256}")
        data = _bytes(view.root, amendment, "the amendment", "amendment")
        read, macro = _revert(data, texts)
        sha = hashlib.sha256(data).hexdigest()
    findings, counts, cdl_top = _compare(view.deck.text, view.deck.top, read, DEVICES[technology])
    if macro is not None and _fold(macro) != _fold(cdl_top):
        raise _refuse("amendment", f"the receipt amends {macro}, and the CDL top read is {cdl_top}")
    return Agreement(findings, sha, counts)


def agree_record(record: Path, netlist_kind: str, *,
                 amendment: Optional[Mapping[str, Any]] = None) -> Agreement:
    """:func:`agree` for the ``(spice, netlist_kind)`` view of an
    ``eda-views.json`` record against every ``cdl`` row of that record, in
    listing order."""
    view = transistor_view(record, "spice", netlist_kind)
    rows = _json(Path(record), "record").get("views")
    cdl = [{"file": r.get("file"), "sha256": r.get("sha256")} for r in rows
           if isinstance(r, Mapping) and r.get("kind") == "cdl"]
    return agree(view, cdl, amendment=amendment)
