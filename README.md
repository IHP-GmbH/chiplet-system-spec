<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 IHP GmbH
-->

# chiplet-system-spec

The specification of the `.chiplet` format: the YAML file that places the dies, interposer
and substrate of a multi-die assembly in one coordinate frame, with the sidecar files that
travel with it. Every tool downstream of layout in the IHP Open ADK flow writes it, reads it
or takes its flags from it. The repository holds the specification, its JSON Schemas,
reference readers and writers in Python and C++, and a conformance suite.

## Use

From the repository root, install the Python reference reader in a virtual environment and
load the example assembly:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install ./reference/python
python3 -c 'import chiplet_format_io as cfio; print(cfio.load("examples/interposer_demo_design.chiplet")["assembly"]["name"])'
```

## Documentation

The full documentation is at
https://ihp-open-adk-docs.readthedocs.io/en/latest/formats.html

The reference kept with the code is under `docs/`.

## Release

Released as part of the IHP Open ADK. The release page lists the commit of every
repository in each release:
https://ihp-open-adk-docs.readthedocs.io/en/latest/install/00_release.html

## License

Apache-2.0. See `LICENSE`.
