# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The order tier 0 reads a source in: the bare-string rule, the vendored
reader, the walk.

The vendored reader (``allow_source=True``) judges the document as a whole
first: the line break rules, the version, ``_metadata.finalize_required``, no
derived block, the assembly. A refusal there is the file's, on line 0, and the
walk does not run on a document that is not a source it can trust. The
``system`` block and the root ``unplaced`` come last in that reader, as
``cfio.SystemBlockError``, and the walk states the same rules on their lines,
so on that refusal the walk runs and its findings stand for it. When the walk
finds nothing to refuse, the refusal stands as the vendored reader words it:
the backstop for a rule the walk does not hold, which otherwise shows once the
walk's lines are fixed. Nothing here imports the reader, so the reader imports
this.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import yaml

from .._cfio import cfio

if TYPE_CHECKING:  # pragma: no cover
    from .document import Source, _Reader


def read(reader: "_Reader") -> Optional["Source"]:
    """The source ``reader`` holds the text of, or None when it refuses; the
    findings are on ``reader``."""
    root = yaml.compose(reader.text, Loader=yaml.SafeLoader)
    quoted = reader.quoted(root)
    try:
        data = cfio.loads(quoted, allow_source=True, validate=False)
        refused = None
        try:
            cfio.validate(data, allow_source=True)
        except cfio.SystemBlockError as exc:
            refused = exc
    except cfio.ChipletFormatError as exc:
        reader.finding(0, str(exc))
        return None
    if "system" not in data:
        reader.finding(0, "this is an assembly, not a source: it has no top-level 'system' block")
        return None
    source = reader.walk(root, data)
    if refused is None:
        return source
    if all(f.note for f in reader.findings):
        reader.finding(0, str(refused))
    return None
