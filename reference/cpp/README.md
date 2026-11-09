<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 IHP GmbH -->

# chiplet_format_io (C++ reference library)

A permissive, **Apache-2.0** reference reader/writer for the `.chiplet`
interchange format. It is the C++ twin of the Python
[`chiplet-format-io`](../python) library and implements the format described in
[`../../docs/CHIPLET_FORMAT_SPEC.md`](../../docs/CHIPLET_FORMAT_SPEC.md).

## Why it exists

The format is permissive on purpose: anyone may implement readers, writers and
tools for `.chiplet` under **any** license, open-source or proprietary. This
library makes that practical in C++ without dragging in the copyleft host tools.

- **Dependency-clean.** The only third-party dependency is
  [yaml-cpp](https://github.com/jbeder/yaml-cpp) (MIT). The library does **not**
  include any KLayout, Qt or pcbnew header, so it can be embedded in tooling
  under any license. A test asserts the sources stay free of those.
- **Plain structs.** `load()` returns a `ChipletDocument` tree of plain value
  structs (`include/chiplet_format_io/chiplet_format_io.hpp`). Paths are kept
  verbatim; the library never touches the filesystem to resolve them; that is
  the consumer's job.
- **Semantic, not byte-exact.** This is an independent reference. It is *not*
  the byte-exact writer used inside the GPL host tools (the KiCad plugin / KiCad
  fork exporter), which are locked to each other by a separate parity gate.
  Output here is canonical YAML, semantically equivalent, not byte-identical.

## API

```cpp
#include <chiplet_format_io/chiplet_format_io.hpp>
namespace cfio = chiplet_format_io;

cfio::ChipletDocument doc = cfio::load("design.chiplet");   // parse + validate
doc.assembly.name = "renamed";
cfio::dump(doc, "design.chiplet");                          // canonical YAML

std::string text = cfio::dumps(doc);                        // to string
cfio::ChipletDocument d = cfio::loads(text);                // from string
cfio::validate(doc);                                        // throws on error
```

`ACCEPTED_FORMAT_VERSIONS` (`{"1.5"}`) is the highest minor the reader implements:
a document at or below it reads silently, a higher minor of the same major warns,
another major is refused. A source document (a top-level `system` block) is
always refused with `SourceDocumentError`; this reader has no source path. A
document with the root key `unplaced` (a source's, format 1.3) and no `system`
block is refused too.

`cfio::load`/`loads` take an optional `LoadOptions{allow_intermediate, validate}`;
`cfio::dump`/`dumps` take an optional `DumpOptions{validate}`. Errors are reported
by throwing `cfio::ChipletFormatError`.

### Parser-attested source capture (development candidate)

`cfio::capture_top_level_blocks(text)` returns `TopLevelBlockCapture`: `safe`,
`preamble`, owned `blocks`, `reason_code`, `reason`, and optional `byte_offset`.
Each `CapturedTopLevelBlock` owns `key` and `raw`, with half-open UTF-8 byte
offsets `byte_start` and `byte_end`. Read the source in binary mode and use the
same unchanged buffer for `loads` and capture. For safe results, concatenating
the preamble and all raw blocks reconstructs the input exactly.

Unlike the existing lexical `split_top_level_keys`, this API attests actual
parser key identity, order and source position, then checks each slice can
stand alone. Unsafe documents return a diagnostic with no partial blocks or
preamble; capture success is not schema or lifecycle validation. Version-one
capture deliberately refuses ambiguous keys, actual anchors/aliases, explicit
tags/directives and unsupported encoding or boundary-line forms. Existing
reader/writer APIs keep their previous behavior. The precise subset and shared
conformance obligations are in the [capture contract](../../docs/ROOT_BLOCK_CAPTURE_DESIGN.md).

Hosts must separately preserve ownership and intentional deletion, validate the
composed output, protect destination identity and implement atomic recovery.
An omitted writer-owned block is not unknown content to restore. This API
does not promise general nested-field preservation. Both readers run the shared
capture cases in `conformance/fixtures/root_capture_cases.json`.

Validation strictness matches the C++ host reader it can replace: missing
`component.id`/`type`, missing `interface.id`/`type`, an unknown interface type,
and a missing `net.name` are always rejected during parsing. `validate=false`
relaxes only the document-level gate, which checks a missing `format_version`
key, a wrong `format_version` value, the `finalize_required` intermediate guard,
a missing or non-map `assembly` section, and a missing `assembly.name`.

## Build & test

Requires CMake (>= 3.16), a C++17 compiler and `libyaml-cpp-dev`.

```bash
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

If yaml-cpp is unavailable on the host, build inside a container, e.g.:

```bash
docker run --rm -v "$PWD":/src -w /src ubuntu:24.04 bash -c '
  apt-get update && apt-get install -y --no-install-recommends \
    cmake g++ libyaml-cpp-dev make &&
  cmake -S . -B build && cmake --build build &&
  ctest --test-dir build --output-on-failure'
```
