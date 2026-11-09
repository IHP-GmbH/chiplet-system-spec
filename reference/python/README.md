<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 IHP GmbH
-->

# chiplet-format-io (Python reference implementation)

Permissive (**Apache-2.0**) reader/writer for the `.chiplet` assembly format. Depends
only on **PyYAML** and deliberately imports no GPL library (`pcbnew`, `klayout`), so it
can be embedded in tools under any license, open-source or proprietary.

This is an **independent reference** implementation of `../../docs/CHIPLET_FORMAT_SPEC.md`.
It is intentionally not the byte-exact writer used inside the GPL host tools (the KiCad
plugin and the KiCad-fork exporter are locked to each other by a byte-exact parity gate);
output here is semantically equivalent canonical YAML, not byte-identical to those hosts.

## Quick start

The library works on plain Python `dict` objects: load, mutate, dump.

```python
import chiplet_format_io as cfio

assembly = cfio.load("design.chiplet")          # dict, validated
assembly["assembly"]["name"] = "renamed"
cfio.dump(assembly, "design.chiplet")           # canonical YAML
```

## Install / test

```bash
pip install -e .            # or: pip install PyYAML
pytest tests -q
```

The distribution name is `chiplet-format-io`; the import package is `chiplet_format_io`.

## API

The names in this table are exported from the top-level package.

| Name | Kind | Description |
| --- | --- | --- |
| `load(path, *, allow_intermediate=False, validate=True)` | function | Read a `.chiplet` file and return a `dict`. |
| `loads(text, *, allow_intermediate=False, validate=True)` | function | Parse a `.chiplet` document from a YAML string into a `dict`. |
| `dump(data, path, *, validate=True)` | function | Write a `dict` to a `.chiplet` file as canonical YAML. |
| `dumps(data, *, validate=True)` | function | Serialize a `dict` to a canonical YAML string. |
| `validate(data, *, allow_intermediate=False)` | function | Validate a parsed mapping in place; return it; raise on error. |
| `ChipletFormatError` | exception | Raised when a document is malformed or unsupported; subclass of `ValueError`. |
| `SUPPORTED_FORMAT_VERSION` | str | The `format_version` the writer stamps on an assembly (`"1.0"`). |
| `SOURCE_FORMAT_VERSION` | str | The lowest stamp of a source, a document with a `system` block (`"1.1"`); the writer stamps a source the lowest minor its content needs. |
| `ACCEPTED_FORMAT_VERSIONS` | tuple | The highest minor this reader implements, per major (`("1.5",)`): a document at or below it reads silently, a higher minor of the same major warns, another major is refused. |

### Submodules

These are imported by module name, not from the top-level package. `die_contract` is strict
die-contract admission ([STRICT_ADMISSION_API.md](../../docs/STRICT_ADMISSION_API.md)). Eleven more
sit beside it. Seven hold what a die flow and the tools that read its output share,
`pad_numbers` the symbol pin a pad gets from the contract's terminals alone, `footprint_ids`
the footprint names a KiCad sheet is given, and `sheet_names` the names it carries verbatim.
`technology_package` validates the package a technology enters the flow as:

| Module | Holds |
| --- | --- |
| `ready_die_names` | A ready die's file names, its build record's words, its die LVS verdict words and the level-1 tokens, and `run_check_not_counted`, the rule for which LVS run-check value counts. |
| `eda_views` | `read`, `gate_view` and `check_bound` over an `eda-views.json` record; a refusal raises `EdaViewsRefused`. |
| `transistor_view` | `transistor_view`, `deck_text` and `derive` over a transistor-level view, and the netlist scanner they read with; a refusal raises `TransistorViewRefused`. |
| `agreement` | `agree` and `agree_record`, the agreement of a simulated SPICE view with the CDL its LVS read, and its device table; a refusal raises `AgreementRefused`. |
| `verilog_closure` | `units` and `closure` over Verilog sources, and the tokenizer they read with. |
| `technology` | `TECHNOLOGY_OF_PDK`, from a PDK name to the technology id. |
| `pins` | `PINS_VERSION` and the pin list's use, role and direction tables. |
| `pad_numbers` | `pad_numbers`: the symbol pin of each terminal (`number`, `name`, `pad_index`, `port`, `ordinal`), from the contract's `terminals` alone, read through die_contract. `is_port` and `no_connect` derive from `port`. A port's pads share the pad name; a repeated name that is no port is `<name>_<k>` in pad-index order and no-connect. No port identity, a half identity, one name on a port and off it or on two ports, or a generated number that collides, raises `DieContractError`. |
| `footprint_ids` | `die_footprint(cell)` is `chiplet_dies:<cell>` and `ext_pad_footprint(um)` is `chiplet_carrier:EXT_PAD_<um>um`. A cell KiCad 9.0.6 would not keep, an empty name, or any whitespace raises `FootprintIdError`. A pad size is a positive whole number of um: a bool, a float, a string, zero or a negative is the same error. |
| `sheet_names` | `SHEET_NAME_RULE` and `is_sheet_name`: a KiCad sheet's net, pin and pad names, a plain first character, then plain characters and `[N]` digit subscripts in any order. A range, an empty, nested or non-digit subscript, or a bracket first is refused. `SHEET_NAME_WORDS` is the rule in words, for a refusal. |
| `technology_package` | `check_package(root)` and `validate_package(root, require=...)` over a technology package, the tree whose manifest is `libs.tech/adk/technology.json` (`schemas/technology_package.schema.json`). A package that cannot be read at all raises `TechnologyPackageError`; otherwise each capability set, A (geometry and assembly), B (automated design) and C (analysis), passes or lists every finding with a stable message id. `methods=` names an interconnect method registry, which `escape_capacity` pitches are checked against. `python -m chiplet_format_io.technology_package ROOT [--methods FILE]` is the command: exit 0 when the required sets pass, 1 when one does not, 2 when the package is refused, 3 when the validator itself fails; a pass with facts it could not check says how many. Needs the `strict` extra (jsonschema). |

### Validation

Reads validate by default. `validate=True` checks the structural contract: `format_version`
must be accepted under `ACCEPTED_FORMAT_VERSIONS`, the `assembly` section must exist and carry a non-empty
`name`, `technologies` (if present) must be a mapping, and each entry in `components` (if present)
must be a mapping with non-empty `id` and `type`. Failures raise `ChipletFormatError`. Pass
`validate=False` to skip these checks (for example, to inspect a document you already know is
nonconforming).

### Canonical YAML and key order

`dump` and `dumps` emit canonical YAML: block style, `sort_keys=False` so insertion order is
preserved, and `allow_unicode=True`. This is semantic, not byte-exact to the GPL host writers.

### Intermediate documents

A document carrying `_metadata.finalize_required: true` is an intermediate file that still needs
its finalizer (for example, `hyp_to_gds --update-chiplet-file`). On read, such a document is
refused by default; pass `allow_intermediate=True` to load it anyway. Writing an intermediate
document is always permitted (`dump`/`dumps` validate with intermediates allowed).

### Parser-attested source capture (development candidate)

`capture_top_level_blocks(text)` returns a `TopLevelBlockCapture`, with a
separate `preamble` and ordered immutable `CapturedTopLevelBlock` objects.
Each block has `key`, `raw`, and half-open `byte_start` / `byte_end` offsets
in the original UTF-8 bytes. Read files without newline normalization, decode
strictly, and give the same text to loading and capture:

```python
from pathlib import Path

text = Path("design.chiplet").read_bytes().decode("utf-8", errors="strict")
assembly = cfio.loads(text)       # The normal format and lifecycle policy.
capture = cfio.capture_top_level_blocks(text)
if not capture.safe:
    raise ValueError(f"Cannot preserve source blocks: {capture.reason}")
assert capture.preamble + "".join(block.raw for block in capture.blocks) == text
```

A safe result attests actual parser root keys and boundaries; it is not merely
the lexical key sequence returned by `split_top_level_keys`. Unsupported or
malformed text returns an unsafe result with `reason_code`, `reason`, an
optional byte offset, and **no partial blocks or preamble**. A non-string
argument is a programmer error, not a document verdict.

The conservative capture subset excludes anchors/aliases, explicit tags,
directives, document-marker boundary lines, ambiguous root keys and unsupported
encodings/line endings. Quoted marker-looking text is not generally forbidden.
See the exact [capture contract](../../docs/ROOT_BLOCK_CAPTURE_DESIGN.md).
These restrictions do not change existing loader or writer verdicts.

Capture success does not authorize a save. Hosts still own field ownership,
intentional deletion, final composed-document validation, destination identity,
atomic persistence and recovery. Do not reinsert an omitted writer-owned block
as unknown content, or claim preservation of unmodeled nested fields. Both
readers run the shared capture cases in `conformance/fixtures/root_capture_cases.json`.
