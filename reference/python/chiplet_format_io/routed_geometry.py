# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Read routed geometry through the shared version policy and canonical schema."""
import json
from pathlib import Path

from . import ChipletFormatError, check_contract_version
from .die_contract import _read_packaged_schema

SCHEMA = "routed-geometry"
SUPPORTED_VERSION = "1.0"


class RoutedGeometryError(ChipletFormatError):
    """A routed geometry document is missing or structurally unreadable."""


def routed_geometry_schema():
    """Read the one authored schema, from the checkout or installed package."""
    raw = _read_packaged_schema("routed_geometry.schema.json")
    if raw is None:
        raise RoutedGeometryError("routed geometry schema is not installed")
    return json.loads(raw)


def load_routed_geometry(path, *, on_warn=None):
    """Read a document, accepting compatible minor additions with a warning.

    Raises ContractVersionError for an unsupported or malformed version, and
    RoutedGeometryError for an unreadable file, discriminator or structure.
    Structural validation requires the package's strict (jsonschema) extra.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RoutedGeometryError(f"{path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RoutedGeometryError(f"{path}: expected a {SCHEMA} document")
    check_contract_version(data.get("version"), SUPPORTED_VERSION,
                           name=SCHEMA, on_warn=on_warn)
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise RoutedGeometryError("reading routed geometry requires jsonschema") from exc
    errors = list(Draft202012Validator(routed_geometry_schema()).iter_errors(data))
    if errors:
        first = errors[0]
        pointer = "/".join(map(str, first.absolute_path))
        raise RoutedGeometryError(f"{path}: {pointer}: {first.message}")
    return data
