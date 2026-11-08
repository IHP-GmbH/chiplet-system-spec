<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 IHP GmbH
-->

# chiplet-system-spec

The specification of the `.chiplet` format, a YAML description of a multi-die assembly (dies,
interposer and substrate in one coordinate frame, with their GDS/OASIS bodies and interconnect),
and of the sidecar files that travel with it. It comes with JSON Schemas, reference
reader/writer libraries in Python and C++, and a conformance suite. Apache-2.0 throughout.

## What is where

| Path | Contents |
|------|----------|
| `docs/` | Format specification, version policy, reader API notes, 3Dblox mapping |
| `schemas/` | JSON Schemas for `.chiplet` and its sidecars, with example instances |
| `examples/` | A sample `.chiplet`, boundary manifests, an interconnect-method registry |
| `reference/python/` | `chiplet-format-io`, the Python reference reader/writer (needs PyYAML) |
| `reference/cpp/` | The same library in C++17 (needs yaml-cpp, builds with CMake) |
| `description/` | `chiplet-system-description`, a typed Python eDSL that elaborates to `.chiplet` |
| `conformance/` | Fixtures and pytest suites that pin reader behaviour across implementations |

## Quick start

From the repository root:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install ./reference/python pytest -r conformance/requirements.txt

# read and validate an example with the reference reader
python -c 'import chiplet_format_io as cfio; print(cfio.load("examples/interposer_demo_design.chiplet")["assembly"]["name"])'

# check the same file against the JSON Schema
python -c 'import json, yaml, jsonschema; jsonschema.validate(yaml.safe_load(open("examples/interposer_demo_design.chiplet")), json.load(open("schemas/chiplet.schema.json")))'

# run the conformance suite, then the library's own tests
(cd conformance && pytest -q)
(cd reference/python && pytest -q)
```

The Python API is in [reference/python/README.md](reference/python/README.md), C++ build steps
in [reference/cpp/README.md](reference/cpp/README.md), and the system description in
[description/README.md](description/README.md).

## Documents and versioning

[docs/CHIPLET_FORMAT_SPEC.md](docs/CHIPLET_FORMAT_SPEC.md) is the normative entry point;
`schemas/chiplet.schema.json` is normative for structure and the reference reader for semantics.
[docs/VERSION_POLICY.md](docs/VERSION_POLICY.md) governs every version number here.

The format is at 1.3. Writers stamp the lowest minor their content needs, so assemblies are
still written as `"1.0"`. A reader accepts a higher minor of its own major with a warning and
refuses any other major. The library release (`chiplet_format_io.__version__`) is separate
from the format version.

## License and contributing

Apache License 2.0, see [LICENSE](LICENSE); per-file declarations in [.reuse/dep5](.reuse/dep5).
Issues and pull requests go to <https://github.com/IHP-GmbH/chiplet-system-spec>; open pull
requests against `dev`. CI ([.github/workflows/tests.yml](.github/workflows/tests.yml)) runs on
pull requests to `dev` and `main`.
