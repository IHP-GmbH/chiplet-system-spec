# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The one writer of a source ``.chiplet``.

Two ways in. :func:`dumps_source` writes a whole file from items, for the
converter and anything else that starts a file; a string goes bare when the
bare-string rule allows it and is quoted otherwise, every item is one line, and
indexed runs collapse into ``[a..b]``. :func:`write_revision` changes a file
that exists: :func:`~chiplet_system_description.source.edit.revision_edits` diffs the System
the file elaborated to against a revised one, identity by identity, and
:func:`~chiplet_system_description.source.patch.patch_system` rewrites only the tokens that
moved, so every other byte of the file (comments, the ``flow`` block) is the
file's own.

A number is written as the shortest decimal that reads back to it, with no
exponent and no ``.0`` on an integer value. A file is stamped the lowest minor
its content needs (:func:`.versions.choose_stamp`): the text is read once at the
highest minor this reader implements, then stamped down to the content's own
floor: ``"1.5"`` when a rail states a routing requirement, ``"1.4"`` when a
die names an admission, ``"1.3"`` when it leaves
anything to placement, ``"1.2"`` for a rail's ``volts``, else ``"1.1"``.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple, Union

from .._cfio import cfio
from ..errors import SystemSpecError
from ..model import PortRef, System
from .document import (
    Carrier,
    DieItem,
    External,
    Finding,
    Source,
    SourceError,
    check_text,
    entry_text,
    ref_text,
)
from .plan import Plan, collapse, plan
from .rows import flow_list
from .scalars import number, scalar
from .versions import choose_stamp


class SourceWriteError(SystemSpecError):
    """A revision the source file cannot express, with the line it would touch.

    ``code`` names the kind (see :class:`~chiplet_system_description.source.patch.EditFinding`)
    and ``port`` the port it is about, when there is one."""

    def __init__(self, line: int, message: str, *, code: str, port: Optional[str] = None):
        self.line = line
        self.message = message
        self.code = code
        self.port = port
        super().__init__(f"line {line}: {message}" if line else message)


def _refs(refs: Iterable[PortRef]) -> List[str]:
    """Port refs as written: consecutive runs on one instance collapse."""
    out: List[str] = []
    run: List[PortRef] = []

    def flush():
        if run:
            out.extend(f"{run[0].instance}.{name}" for name in collapse([r.port for r in run]))
            run.clear()

    for ref in refs:
        if run and ref.instance != run[0].instance:
            flush()
        run.append(ref)
    flush()
    return out


# ----------------------------------------------------------------------
# A whole file
# ----------------------------------------------------------------------
def die_line(die: DieItem) -> str:
    """One ``dies`` line; a die not placed yet (``at`` None) has neither
    ``at`` nor ``rotation``."""
    fields = [f"id: {scalar(die.id)}", f"die: {scalar(die.die)}"]
    if die.at is not None:
        fields += [f"at: [{number(die.at[0])}, {number(die.at[1])}]",
                   f"rotation: {die.rotation}"]
    fields.append(f"method: {scalar(die.method)}")
    if die.thickness is not None:
        fields.append(f"thickness: {number(die.thickness)}")
    if die.admission is not None:
        fields.append(f"admission: {scalar(die.admission)}")
    return "    - {" + ", ".join(fields) + "}"


def dumps_source(*, name: str, carrier: Carrier, dies: Sequence[DieItem],
                 description: str = "", registry: Optional[str] = None,
                 buses: Sequence[Tuple[Tuple[str, str], Sequence[str], str, str]] = (),
                 links: Sequence[Tuple[PortRef, PortRef, Optional[str]]] = (),
                 rails: Sequence[Tuple[str, str, Sequence[PortRef]]] = (),
                 alone: Sequence[PortRef] = (), open: Sequence[PortRef] = (),
                 external: Optional[External] = None,
                 fill_keepout: Optional[Sequence[Tuple[str, float]]] = None,
                 interposer_adapter: Optional[str] = None,
                 flow_text: Optional[str] = None, comment: Optional[str] = None,
                 finalizer: str = "packaging-flow run",
                 unplaced: Sequence[Union[PortRef, str]] = ()) -> str:
    """A source file from its items, read back through tier 0 before it is returned.

    ``buses`` are (ends, port names, net_prefix, net_suffix); ``links`` are
    (a, b, net or None for the default); ``rails`` are (name, class, refs).
    ``flow_text`` is the ``flow`` block as text, written as given. A source
    placement has not reached yet (format 1.3) gives dies with ``at`` None, a
    carrier with ``size`` None, no dies, an external with no edges, and in
    ``unplaced`` the external entries (refs or rail names) no edge carries.
    """
    out: List[str] = []
    if comment:
        out.extend(f"# {line}".rstrip() for line in comment.splitlines())
    stamp_at = len(out)
    out += [f'format_version: "{cfio.SOURCE_FORMAT_VERSION}"', "_metadata:",
            "  finalize_required: true",
            f"  finalizer: {scalar(finalizer)}", "assembly:", f"  name: {scalar(name)}"]
    if description:
        out.append(f"  description: {scalar(description)}")
    out.append("  units: um")
    if interposer_adapter:
        out += ["interposer:", f"  adapter: {scalar(interposer_adapter)}"]
    out.append("system:")
    size = ("" if carrier.size is None
            else f", size: [{number(carrier.size[0])}, {number(carrier.size[1])}]")
    out.append(f"  carrier: {{technology: {scalar(carrier.technology)}, top_cell: "
               f"{scalar(carrier.top_cell)}{size}}}")
    if registry:
        out.append(f"  registry: {scalar(registry)}")
    if dies:
        out.append("  dies:")
        out.extend(die_line(d) for d in dies)
    if buses:
        out.append("  buses:")
        for ends, ports, prefix, suffix in buses:
            fields = [f"ends: {flow_list(ends)}", f"ports: {flow_list(collapse(list(ports)))}"]
            if prefix:
                fields.append(f"net_prefix: {scalar(prefix)}")
            if suffix:
                fields.append(f"net_suffix: {scalar(suffix)}")
            out.append("    - {" + ", ".join(fields) + "}")
    if links:
        out.append("  links:")
        for a, b, net in links:
            fields = [f"a: {scalar(ref_text(a))}", f"b: {scalar(ref_text(b))}"]
            if net is not None and net != f"{a.instance}_{a.port}":
                fields.append(f"net: {scalar(net)}")
            out.append("    - {" + ", ".join(fields) + "}")
    if rails:
        out.append("  rails:")
        for rail_name, net_class, refs in rails:
            out.append(f"    - {{name: {scalar(rail_name)}, class: {scalar(net_class)}, "
                       f"ports: {flow_list(_refs(refs))}}}")
    for block, refs in (("alone", alone), ("open", open)):
        if refs:
            out.append(f"  {block}:")
            out.extend(f"    - {scalar(r)}" for r in _refs(refs))
    if external is not None:
        out += ["  external:", f"    profile: {scalar(external.profile)}"]
        out += ["    edges:"] if external.edges else []
        for edge in external.edges:
            entries = [entry_text(e) for e in edge.entries]
            out.append(f"      {edge.edge}: {flow_list(entries)}")
    if fill_keepout:
        halo = ", ".join(f"{layer}: {number(um)}" for layer, um in fill_keepout)
        out.append(f"  fill_keepout: {{halo_um: {{{halo}}}}}")
    if unplaced:
        out += ["unplaced:", "  pins: " + flow_list([entry_text(e) for e in unplaced])]
    tail = "" if not flow_text else flow_text if flow_text.endswith("\n") else flow_text + "\n"

    def _render(version: str) -> str:
        lines = list(out)
        lines[stamp_at] = f'format_version: "{version}"'
        return "\n".join(lines) + "\n" + tail

    chosen = choose_stamp(lambda version: check_text(_render(version)), 1)
    stamp = chosen.version if chosen.version is not None else chosen.tried
    if stamp is None:
        raise SourceError("<written>", chosen.findings or [Finding(0, "not a source")])
    text = _render(stamp)
    if chosen.version not in (None, chosen.tried):
        source, findings = check_text(text)
    else:
        source, findings = chosen.source, chosen.findings
    if findings or source is None:
        raise SourceError("<written>", findings or [Finding(0, "not a source")])
    return text


# ----------------------------------------------------------------------
# A revision of a file that exists
# ----------------------------------------------------------------------
def _bus_affixes(ports: Sequence[str], nets: Sequence[str]) -> Optional[Tuple[str, str]]:
    """The one (prefix, suffix) naming every port's net, if there is one."""
    first, net = ports[0], nets[0]
    for k in range(len(net) - len(first) + 1):
        if net[k:k + len(first)] != first:
            continue
        prefix, suffix = net[:k], net[k + len(first):]
        if all(f"{prefix}{p}{suffix}" == n for p, n in zip(ports, nets)):
            return prefix, suffix
    return None


def write_revision(source: Source, old: System, new: System,
                   topology: Optional[Plan] = None) -> str:
    """``source``'s text with the tokens ``old`` -> ``new`` moved, and nothing else.

    ``old`` is the System ``source`` elaborated to and ``new`` a revision of it
    that keeps its structure (:func:`chiplet_system_description.revise_system` guarantees
    that).
    """
    from .edit import revision_edits
    from .patch import patch_system

    topology = topology or plan(source)
    if topology.refusals:
        raise SourceError(source.path, topology.refusals)
    edits = revision_edits(source, topology, old, new)
    return patch_system(source.text.encode("utf-8"), edits).text.decode("utf-8")
