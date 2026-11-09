# Parser-attested root capture: reader contract

Both reference readers implement this contract since reader 1.6.0. It adds an
API; no existing loader, writer or key-sequence API changes.

## Purpose and public shape

Studio must preserve root blocks it does not author, including `interposer`,
without confusing omitted writer-owned fields with lost unknown content.
Ownership, recovery identity and destination policy belong to the host. This
API only establishes safe source slices; success is not schema validation,
round-trip acceptance, or permission to overwrite a destination.

Both reference implementations expose `capture_top_level_blocks(text)`.
Python names: `CapturedTopLevelBlock` and `TopLevelBlockCapture`; C++ uses the
same names. A block has `key`, `byte_start`, `byte_end`, and `raw`. A capture has
`safe`, `preamble`, `blocks`, `reason_code`, `reason`, and optional `byte_offset`.
Python blocks are an immutable tuple; C++ uses an owned vector. Strings own
their content. Offsets are half-open UTF-8 byte offsets, never character indices.

Safe results have an empty reason/code and no error offset. Unsafe results have
`safe=false`, a nonempty code and diagnostic, empty preamble and no blocks.
Malformed/unsupported documents return an unsafe verdict, not partial captures.
Resource/programmer failures need not be disguised as document verdicts.

## Accepted subset and attestation

1. One immutable strict UTF-8 buffer, without BOM; line endings LF or CRLF.
   No normalization, newline insertion, or Unicode replacement. A host passes
   the identical buffer to loading and capture. File I/O must not normalize it.
2. Exactly one nonempty root mapping. Reuse the reader's existing lexical
   splitter, then independently attest each root key's identity, order and
   actual parser start against its lexical byte boundary. Matching names alone
   is insufficient. Each slice also parses independently as exactly its one
   root entry. Reconstructing preamble plus ordered raw slices equals the input.
3. Version-one capture keys use `[A-Za-z_][A-Za-z0-9_.-]*`, and reject the
   case-insensitive words `y`, `n`, `yes`, `no`, `true`, `false`, `on`, `off`,
   and `null`. This deliberately conservative shared subset avoids divergent
   scalar-key resolution. It does not narrow what the existing loader accepts.
   Refuse quoted, explicit, complex, non-string and duplicate root keys.
4. Preamble contains only blank lines and comments; tabs in the leading
   indentation of those lines are unsupported. Tabs after the `#` comment
   marker remain valid. This is a conservative capture-profile restriction,
   not a universal YAML validity rule: the qualified Python/C++ parsers differ
   on pre-root indentation tabs. It ends at the first key's
   line start. Each block ends at the next key's line start; trailing comments
   and line endings belong to the preceding block. No final newline is required.
5. Actual anchors and aliases are unsupported in this first additive version,
   including self-contained ones. Detect them through parser events, not raw
   text searches. Quoted anchor-looking strings remain valid. Later support for
   proven self-contained aliases can relax this limitation independently.
6. Explicit tags and directives are unsupported. Parser events attest actual
   properties; source inspection at a parser-attested property start may resolve
   the non-specific `!` case that otherwise resembles implicit quoted scalars.
   Do not reject ordinary quoted, literal or folded scalars as explicit tags.
   Preamble restrictions reject directives without scanning scalar contents.
7. Column-zero document-marker-shaped lines (`---` / `...` with the reader's
   existing marker syntax) are conservatively unsupported anywhere. Document
   markers and multiple documents must not travel inside a movable root slice.
   This also refuses such an ambiguous line inside a multiline quoted value;
   report that conservative boundary-line limitation, not a claim that the parser
   classified it as a directive. Indented literal/folded marker text is allowed.

Use actual parser structure, not a second YAML parser in the host. For UTF-8
positions, attest root line/column and offsets against original bytes; explicitly
test multibyte text before later keys. Do not silently trust character offsets
as byte offsets. Error offsets are optional when an exact byte location is not
established.

## Shared conformance and integration requirements

One shared fixture, `conformance/fixtures/root_capture_cases.json`, is consumed
by the Python and C++ tests. Positive cases include the format's all-blocks fixture,
interposer and metadata content, exact comments/CRLF/no-final-newline bytes,
UTF-8 offsets, literal/folded/quoted multiline values, quoted anchor/tag-looking
strings, and arbitrary nested unknown values. Compare explicit expected keys,
preamble and slices, not only round-trip concatenation.

Negative cases include phantom root-looking quoted-scalar lines, duplicate roots,
quoted/explicit/non-string/flow-root keys, actual internal and cross-block aliases,
duplicate anchors, explicit tags/directives, document markers/multiple documents,
invalid encoding/line endings and partial-capture refusal. Separately prove that
capture does not turn an invalid format version or lifecycle state into a valid
load/save. Preserve every existing key/flow/version oracle and writer test.

The multiline/comment ownership positive uses spaces on its leading blank line.
Tabs in pre-root indentation are separate unsafe oracle cases, not whitespace
to normalize. The `preamble_tab` capture-profile refusal reports the exact UTF-8
byte offset in both implementations, including after multibyte comments.
The fixture's `error_byte_offset_if_present` witness also checks any reported
location; independent language controls require a location for this restriction.
Other parser-error offsets remain optional under the public API.
Positive controls preserve tabs after comment markers and inside quoted/literal
values byte-for-byte. Existing loaders, splitters and writers are unchanged.
The prior native qualification observed yaml-cpp 0.7 accepting the leading-tab
case that PyYAML refused; this explicit capture restriction closes that profile
disagreement without changing either underlying parser's general language.

## Parser source references

yaml-cpp 0.7 exposes anchor and alias callbacks through its public
[event interface](https://raw.githubusercontent.com/jbeder/yaml-cpp/yaml-cpp-0.7.0/include/yaml-cpp/eventhandler.h).
Its [document parser](https://raw.githubusercontent.com/jbeder/yaml-cpp/yaml-cpp-0.7.0/src/singledocparser.cpp)
retains a node's starting mark before consuming properties, while non-plain
implicit scalars use a non-specific tag. Its UTF-8
[stream implementation](https://raw.githubusercontent.com/jbeder/yaml-cpp/yaml-cpp-0.7.0/src/stream.cpp)
advances positions by consumed bytes. Still qualify the exact installed build;
these source references do not prove the identity of a local binary.
