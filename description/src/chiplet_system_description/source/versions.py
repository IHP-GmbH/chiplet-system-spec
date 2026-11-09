# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Which format minor this reader implements, and the stamp a file should carry.

:func:`implemented` is the vendored reader's accepted-minor map, one entry per
major. :func:`choose_stamp` is the one trial the writer and the patcher share:
read the text once at the highest minor that major implements, and the content's
own floor (:func:`.unplaced.content_minor`) is the stamp. Pass ``keep_unless``
and the stamp changes only when the content needs more than the file has, or
less than both that and the minor it had before the edits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from .._cfio import cfio
from .unplaced import content_minor

Read = Callable[[str], Tuple[Any, Sequence[Any]]]


def implemented() -> Dict[int, Tuple[int, str]]:
    """Per major, the highest minor this reader implements and its text."""
    return cfio.accepted_minors()


def newer_than_reader(format_version: str) -> Optional[str]:
    """The version this reader implements, when ``format_version`` (already
    accepted by the vendored reader) is a higher minor of its major; else None."""
    major, minor = cfio.parse_format_version(format_version)
    floor = implemented().get(major)
    return floor[1] if floor is not None and minor > floor[0] else None


@dataclass(frozen=True)
class ChosenStamp:
    """What :func:`choose_stamp` decided.

    ``version`` is the stamp to write, or None to leave the stamp that is
    already there (the trial did not read, or the patcher's rule says stay).
    ``source`` and ``findings`` are the trial read, so a caller whose trial
    did not read can report those findings. ``tried`` is the version that
    read was asked for, or None when this reader implements no minor of the
    major.
    """

    version: Optional[str]
    source: Any
    findings: Sequence[Any]
    tried: Optional[str] = None


def choose_stamp(read: Read, major: int, *,
                 keep_unless: Optional[Tuple[Tuple[int, int], Optional[str]]] = None
                 ) -> ChosenStamp:
    """The minor ``read``'s text needs.

    ``read(version)`` returns ``(source, findings)`` for the text stamped
    ``version``. The trial is the highest minor :func:`implemented` gives
    ``major``. ``keep_unless=(have, before)`` is the patcher's rule: the stamp
    changes only when the content needs more than ``have``, or less than both
    ``have`` and ``before`` (the edits removed what needed more).
    """
    highest = implemented().get(major)
    if highest is None:
        return ChosenStamp(None, None, ())
    tried = highest[1]
    source, findings = read(tried)
    if findings or source is None:
        return ChosenStamp(None, source, findings, tried)
    need = content_minor(source)
    if keep_unless is None:
        return ChosenStamp(need, source, findings, tried)
    have, before = keep_unless
    parsed = cfio.parse_format_version(need)
    if parsed is None:
        return ChosenStamp(None, source, findings, tried)
    before_parsed = cfio.parse_format_version(before) if before is not None else None
    lowered = before_parsed is not None and parsed < before_parsed and parsed < have
    if parsed > have or lowered:
        return ChosenStamp(need, source, findings, tried)
    return ChosenStamp(None, source, findings, tried)
