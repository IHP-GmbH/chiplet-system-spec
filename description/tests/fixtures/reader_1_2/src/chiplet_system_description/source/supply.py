# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The supply pins of a source, each with its rail and volts: what a picture
labels a power or ground pin with.

The row shape is stable, since a caller digests the rows to know when to
redraw: ``{instance, pad, port, domain, rail, volts}``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Union

from .document import SourceError, read_source
from .plan import plan


def supply_pins(path: Union[str, Path]) -> List[Dict[str, object]]:
    """One row per supply pin the source at ``path`` puts on a rail, sorted by
    instance then pad.

    ``domain`` is the supply domain the pin's terminal names, None when it
    names none. ``volts`` is that domain's, as its die contract declares
    them; 0 on a ground pin, a ground rail being 0 by its class; and None when
    the die declares none, even where the rail states its own: that is the
    pin ``validate`` leaves unchecked, so a label can say the voltage is not
    declared rather than show one nothing confirmed.
    A source tier 1 refuses raises :class:`SourceError`, as elaboration does.
    """
    source = read_source(path)
    topology = plan(source)
    if topology.refusals:
        raise SourceError(source.path, topology.refusals)
    rows = []
    for rail in source.rails:
        for ref in rail.ports:
            folder = topology.folders[ref.instance]
            for pad in folder.port(ref.port).pads:
                domain, volts = folder.supply(pad)
                rows.append({"instance": ref.instance, "pad": pad, "port": ref.port,
                             "domain": domain, "rail": rail.name,
                             "volts": 0 if rail.net_class == "ground" else volts})
    return sorted(rows, key=lambda row: (row["instance"], row["pad"]))
