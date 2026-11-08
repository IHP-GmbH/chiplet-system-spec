// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 IHP GmbH
//
// chiplet_format_io -- permissive reference reader/writer for the .chiplet format.
//
// Apache-2.0. Dependency-clean: the only third-party dependency is yaml-cpp
// (MIT). This header exposes ONLY the standard library; yaml-cpp is confined to
// the implementation. The library deliberately does NOT include any KLayout, Qt
// or pcbnew header, so it can be embedded in tools under any license,
// open-source or proprietary.
//
// This is an INDEPENDENT reference implementation of the format described in
// docs/CHIPLET_FORMAT_SPEC.md and mirrors the Python reference library
// (reference/python/chiplet_format_io). It is intentionally *not* the byte-exact
// writer used inside the GPL host tools (the KiCad plugin / KiCad fork
// exporter): those are locked to each other by a byte-exact parity gate. Output
// here is canonical YAML, semantically equivalent, not byte-identical to those
// hosts.
//
// Typical use:
//
//     #include <chiplet_format_io/chiplet_format_io.hpp>
//     namespace cfio = chiplet_format_io;
//     cfio::ChipletDocument doc = cfio::load("design.chiplet");
//     doc.assembly.name = "renamed";
//     cfio::dump(doc, "design.chiplet");

#ifndef CHIPLET_FORMAT_IO_HPP
#define CHIPLET_FORMAT_IO_HPP

#include <array>
#include <cstddef>
#include <functional>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace chiplet_format_io {

// The format_version this reference implementation WRITES on an assembly
// document. The on-disk baseline stays additive-stable at "1.0"; readers are
// tolerant of a same-major higher minor (see check_format_version). Bump
// together with docs/CHIPLET_FORMAT_SPEC.md and the Python reference library.
inline constexpr const char* SUPPORTED_FORMAT_VERSION = "1.0";

// The stamp of a SOURCE document, one with a top-level `system` block (format
// 1.1). This reader never writes one and refuses to read one (see
// SourceDocumentError); the constant mirrors the Python reference, whose source
// path does both, so the two references state one set of stamps.
inline constexpr const char* SOURCE_FORMAT_VERSION = "1.1";

// The SET of majors this reader accepts, one MAJOR.MINOR entry per major
// (docs/VERSION_POLICY.md, "Changing the major"), mirroring the Python
// ACCEPTED_FORMAT_VERSIONS. Each entry is the highest minor implemented for its
// major: a document at or below it reads silently, one above it warns. One
// entry is the ordinary state; a second appears only while a major transition is
// open, and it is a promise that the code path for that major exists here. The
// static_assert below is the half of the rule a comment cannot keep: every stamp
// this library states must be something it reads without a warning.
inline constexpr std::array<const char*, 1> ACCEPTED_FORMAT_VERSIONS{{"1.5"}};

namespace detail {
constexpr bool same_text(const char* a, const char* b) {
    while (*a != '\0' && *a == *b) { ++a; ++b; }
    return *a == *b;
}
// The digits of one MAJOR or MINOR part, starting at *p and stopping at '.' or
// the end; -1 when the part is empty or holds anything but a digit.
constexpr int version_part(const char*& p) {
    int value = 0;
    bool any = false;
    while (*p != '\0' && *p != '.') {
        if (*p < '0' || *p > '9') return -1;
        value = value * 10 + (*p - '0');
        any = true;
        ++p;
    }
    return any ? value : -1;
}
// Whether v is "MAJOR.MINOR" at or below the accepted entry for its major.
constexpr bool is_readable(const char* v) {
    const char* p = v;
    const int major = version_part(p);
    if (major < 0 || *p != '.') return false;
    ++p;
    const int minor = version_part(p);
    if (minor < 0 || *p != '\0') return false;
    for (const char* entry : ACCEPTED_FORMAT_VERSIONS) {
        const char* q = entry;
        const int entry_major = version_part(q);
        ++q;
        const int entry_minor = version_part(q);
        if (entry_major == major) return minor <= entry_minor;
    }
    return false;
}
}  // namespace detail

static_assert(detail::is_readable(SUPPORTED_FORMAT_VERSION) &&
                  detail::is_readable(SOURCE_FORMAT_VERSION),
              "every stamp must be at or below an ACCEPTED_FORMAT_VERSIONS "
              "entry: a writer that stamps a version its own reader refuses or "
              "warns on is a version policy nobody can follow");

// The release of THIS reference implementation, mirroring the Python library's
// chiplet_format_io.__version__. Distinct from SUPPORTED_FORMAT_VERSION, which
// is a fact about documents: a consumer that vendors a copy of this reader pins
// a reader RELEASE, so a vendored mirror can be gated on a version instead of on
// bytes. The two reference implementations ship one release number, and
// conformance/test_version_policy.py fails if they drift apart.
inline constexpr const char* READER_RELEASE = "1.21.0";

// The closed interfaces[].type vocabulary (spec validation rule 4), exported
// because a consumer needs it. This library carries whatever string the document
// wrote and refuses nothing over it, so the consumer is the one that decides an
// unrecognised member is unusable, and it cannot refuse the ELEMENT that carries
// one without the list to compare against. Element-refusal is what makes an
// added enum member a MINOR rather than a MAJOR (docs/VERSION_POLICY.md), so
// this array is part of that promise and not a convenience. It is also the list
// a WRITER is bound to: the schema closes the vocabulary for producers.
// One list, four places (this array, schemas/chiplet.schema.json, the spec prose
// and the Python KNOWN_INTERFACE_TYPES); conformance/test_interface_types.py
// reads all four.
inline constexpr std::array<const char*, 5> kKnownInterfaceTypes = {
    {"micro_bump", "copper_pillar", "tsv", "wire_bond", "solder_bump"}};

// Per closed vocabulary, the members added after 1.0 and the format minor that
// added each; a member with no row is since 1.0. A document stamped below a
// member's minor is out of contract and validate() refuses it, whether or not
// this reader knows the member. Mirrors the Python MEMBER_SINCE; the map is
// schema data first (an `x-since` object beside each enum in
// schemas/chiplet.schema.json), and conformance/test_member_since.py holds the
// three to one map. Field names: "interfaces[].type", "io_pads[].io_class",
// "components[].anchor", "components[].orientation", "assembly.units".
struct MemberSince {
    const char* field;
    const char* member;
    const char* since;
};
inline constexpr std::array<MemberSince, 1> kMemberSince{{
    {"interfaces[].type", "solder_bump", "1.1"},
}};

// Throw ChipletFormatError when `member` of vocabulary `field` appears in a
// document stamped `declared` below the minor `since` gives it. A member with
// no row, an unparsable version or another major passes: the version policy
// decides those. The overload without `since` reads kMemberSince; the other is
// how the shared oracle (conformance/fixtures/member_since_cases.json) runs.
void check_member_since(const std::string& declared, const std::string& field,
                        const std::string& member,
                        const std::vector<MemberSince>& since);
void check_member_since(const std::string& declared, const std::string& field,
                        const std::string& member);

// assembly.units, closed (format 1.1), and MUST-UNDERSTAND: a length read in a
// unit the reader does not convert is wrong rather than incomplete, so validation
// refuses any other value. Absent means "um", the normative default. Mirrors the
// Python KNOWN_UNITS; conformance/test_units.py reads both and the schema enum.
inline constexpr std::array<const char*, 1> kKnownUnits = {{"um"}};

// Whether `t` is a member of kKnownInterfaceTypes. Exported with the array: a
// consumer that has to write the loop itself will write it slightly differently
// somewhere, and then two consumers disagree about one document.
bool is_known_interface_type(const std::string& t);

// Apply the tolerant format_version policy (parity-bound to the Python
// check_format_version): malformed, or a major outside ACCEPTED_FORMAT_VERSIONS
// (higher OR lower), throws ChipletFormatError and the refusal names every
// accepted major; an accepted major with a minor at or below that major's floor
// is accepted silently; an accepted major with a higher minor is accepted and,
// when on_warn is set, reported through it (never stderr, never a throw).
// Returns the normalized "MAJOR.MINOR".
std::string check_format_version(
    const std::string& fv,
    const std::function<void(const std::string&)>& on_warn = {});

// The same policy applied to any governed artifact (io_pads.json, pins.json, the
// black-box padmap, the boundary manifest, interconnect_methods.json), mirroring
// the Python check_contract_version. Two differences from the .chiplet entry
// point above, both inherited: `accepted` is the CALLER's set (one MAJOR.MINOR
// floor per major it accepts, the one-element vector being the ordinary case),
// and a MAJOR.MINOR.PATCH spelling is allowed because emitters already write
// "1.0.0" -- the PATCH is parsed only to be discarded. `name` identifies the
// artifact in the message. A version this consumer cannot read throws
// ChipletFormatError; an `accepted` set that is empty, malformed, or declares
// two floors for one major is a PROGRAMMING error and throws
// std::invalid_argument at call time, so a typo in a consumer never reads as
// "the file is bad". Returns the normalized "MAJOR.MINOR".
std::string check_contract_version(
    const std::string& value,
    const std::vector<std::string>& accepted,
    const std::string& name,
    const std::function<void(const std::string&)>& on_warn = {});

// Raised when a .chiplet document is malformed or unsupported. Named to mirror
// the Python ChipletFormatError. Host tools that wrap this library translate it
// into their own exception type to preserve their public contract.
class ChipletFormatError : public std::runtime_error {
public:
    explicit ChipletFormatError(const std::string& msg)
        : std::runtime_error(msg) {}
};

// Raised by loads() on a SOURCE document (a top-level `system` block, format
// 1.1), whatever LoadOptions say: a source is not an assembly, and a reader that
// read it as one would see an assembly with no components. Mirrors the Python
// SourceDocumentError; unlike Python, this reader has no source path.
class SourceDocumentError : public ChipletFormatError {
public:
    explicit SourceDocumentError(const std::string& msg)
        : ChipletFormatError(msg) {}
};

// --- Plain value structs (a faithful, lossless view of the .chiplet schema) ---
//
// Enum-like fields (component type, interface type, io_class, orientation,
// anchor) are kept as their canonical YAML *strings*, and that is now true of
// every one of them without exception: nothing in this library refuses a
// document over an unrecognised member, and an unrecognised interfaces[].type is
// reported through LoadOptions::on_warn at parse. The format owns the
// vocabulary, the schema closes each list for WRITERS, and consumers (e.g. a GPL
// host) map these strings into their own richer enums, own any UX such as
// warnings on unknown values, and refuse the ELEMENT they cannot act on;
// kKnownInterfaceTypes above is exported so they can. Net `class` is not in the
// list because the format owns no net-class vocabulary at all: `Net::net_class`
// is a free-form string with a default, not a closed set with an unlisted
// member. Paths are kept verbatim as written in the file -- this library never
// touches the filesystem for resolution; that is the consumer's responsibility.

struct Position3D {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

struct Rotation3D {
    double z = 0.0;
};

struct Dimensions3D {
    double width = 0.0;
    double height = 0.0;
    double thickness = 0.0;
};

struct AssemblyMeta {
    std::string name;            // required
    std::string description;
    std::string author;
    std::string created;
    std::string modified;
    std::string units;
    std::string assembly_gds;    // verbatim path
    std::string io_technology;
};

struct Technology {
    std::string id;              // map key in the YAML
    std::string description;
    std::string layer_properties;  // verbatim path to the .lyp
    // Optional path to a layer-stackup YAML this technology ships. Verbatim as
    // read: the consumer resolves it through the same ${VAR}/relative chain as
    // layer_properties, and an explicit value takes priority over whatever
    // stackup the consumer would otherwise look up for this id. Empty means the
    // field was absent.
    std::string stackup;
    double dbu = 0.001;
    bool has_dbu = false;        // whether dbu was present in the file
};

struct ConnectionStackLayer {
    std::string name;
    std::string material;
    double height = 0.0;
    double diameter = 0.0;
};

struct ConnectionStack {
    std::string id;              // map key in the YAML
    std::string description;
    std::vector<ConnectionStackLayer> layers;
};

struct ComponentArray {
    std::string pattern;
    int count_x = 0;
    int count_y = 0;
    double pitch_x = 0.0;
    double pitch_y = 0.0;
    Position3D start_position;
};

struct IOPad {
    std::string id;
    std::string io_class;        // canonical string, e.g. "wire_bond"
    std::string net;
    double pos_x = 0.0;
    double pos_y = 0.0;
    double size_x = 0.0;
    double size_y = 0.0;
    std::string layer;
};

struct Component {
    std::string id;              // required
    std::string type;            // required, canonical string ("die", ...)
    std::string technology;
    std::string connection;
    std::string layout;          // verbatim path
    // Both the legacy `top_cell` scalar and the `cells` sequence collapse here.
    // On write, a single entry is emitted as `top_cell` (back-compat), multiple
    // as `cells` -- mirroring the host writer.
    std::vector<std::string> cells;
    Position3D position;
    Rotation3D rotation;
    // Raw orientation string. The contract defines only "face_up" (default)
    // and "flip_chip" (see coord_frame_contract.md 2.4); "face_down" is a
    // non-canonical alias consumers may accept-with-warning. Empty means the
    // field was absent (treated as face_up downstream). The consumer validates
    // the value.
    std::string orientation;
    // Raw anchor string if the field was present; std::nullopt if absent. The
    // consumer validates the value and owns the "missing anchor" warning.
    std::optional<std::string> anchor;
    Dimensions3D dimensions;
    // Interposer die-attachment (BEOL-top) surface z, in the component's local
    // frame: the plane dies mount on (a die's position.z == this +
    // connection-stack height). std::nullopt when the field is absent, in
    // which case consumers fall back to dimensions.thickness as the mount
    // reference -- legacy files where thickness encoded the attachment
    // surface. When present, dimensions.thickness is the physical body
    // z-extent (the interposer substrate, extending downward from this
    // surface), decoupled from the mount plane. See coord_frame_contract.md
    // sections 3.2 / 3.4 / 5.5.
    std::optional<double> attachment_surface_z;
    std::optional<ComponentArray> array;
    // Insertion-ordered key/value metadata.
    std::vector<std::pair<std::string, std::string>> metadata;
    std::vector<IOPad> io_pads;
};

struct InterfaceEndpoint {
    std::string component;
    std::string surface;
    std::string port_layer;
};

struct InterfacePhysical {
    double pitch = 0.0;
    double diameter = 0.0;
    double height = 0.0;
};

struct Interface {
    std::string id;              // required
    std::string type;            // required, validated against the known set
    std::optional<InterfaceEndpoint> from;
    std::optional<InterfaceEndpoint> to;
    std::optional<InterfacePhysical> physical;
};

struct NetConnection {
    std::string component;
    std::string pin;
    std::string layer;
};

struct Net {
    std::string name;            // required
    std::string net_class = "signal";  // canonical string; default "signal"
    bool external = false;
    std::vector<NetConnection> connections;
};

struct Netlist {
    bool present = false;
    std::vector<Net> nets;
    std::string external_netlist;  // verbatim path
};

struct Interconnect {
    std::string adapter;                  // required when the block is present
    std::optional<Technology> technology; // optional PDK-backed identity
};

struct Metadata {
    bool present = false;
    bool finalize_required = false;
    std::string finalizer;
};

// Where the bytes in ChipletDocument::flow_yaml came from, which is what decides
// whether this document can be WRITTEN back.
//
//   Absent          no flow block, or a flow value authored in memory by this
//                   host (it owns the bytes, so it may write them).
//   Slice           the exact source slice the top-level block grammar
//                   delimited: flow rule 4 is satisfiable, dumps() appends it
//                   unchanged.
//   NotDelimitable  the document HAS a flow node the grammar cannot delimit (a
//                   flow-style document, a `flow :` key line, a quoted key at
//                   column zero anywhere in the file). Reading such a document
//                   is fine and the spec requires it: flow rule 1 says a reader
//                   that cannot parse the block MUST NOT reject the document.
//                   What is impossible is writing it back, so `flow_yaml` is
//                   empty and dumps() throws rather than drop the block or emit
//                   a node dump in its place. A host that re-authors the flow
//                   (assigns flow_yaml) can save again.
enum class FlowSource { Absent, Slice, NotDelimitable };

// Top-level parsed document. A faithful, round-trippable view of one .chiplet
// file. The `flow` build block is host-specific build configuration the spec
// leaves opaque, and it is kept as the exact source text rather than as a parsed
// node; consumers that care re-parse `flow_yaml` themselves.
struct ChipletDocument {
    std::string format_version;            // always "1.0" after a successful load
    Metadata metadata;                     // the _metadata block
    AssemblyMeta assembly;
    std::vector<Technology> technologies;          // insertion order preserved
    std::vector<ConnectionStack> connection_stacks; // insertion order preserved
    std::vector<Component> components;
    std::optional<Interconnect> interconnect;
    std::vector<Interface> interfaces;
    Netlist netlist;
    bool has_flow = false;
    // The `flow` block exactly as it stands in the source: the `flow:` key line
    // included, original line endings, no trailing-newline normalisation,
    // nothing stripped. Delimited by the top-level block grammar
    // (docs/CHIPLET_FORMAT_SPEC.md), which is what lets a host that did not
    // author the block re-emit it byte for byte, as flow rule 4 requires. It is
    // NOT a re-serialisation: a node dump re-quotes scalars (a source '0755'
    // comes back bare, and is then an integer to the next PyYAML reader) and
    // drops comments. dumps() writes these bytes back unchanged.
    //
    // Empty when `flow_source` is NotDelimitable: the block is in the document
    // but its bytes were never captured, and inventing them is exactly what this
    // field exists to prevent.
    std::string flow_yaml;
    FlowSource flow_source = FlowSource::Absent;

    // Non-fatal reader notes (a same-major higher-minor format_version, an
    // unrecognised member of a closed vocabulary). Per-document, so there is no
    // global state and a GUI host can surface them however it likes; never
    // written to stderr by the library. NON-NORMATIVE convenience: the channel
    // a consumer gates on is LoadOptions::on_warn, which receives every event
    // undeduplicated and is the one the Python reference mirrors.
    std::vector<std::string> warnings;

    // Convenience lookup; returns nullptr if no technology with that id exists.
    const Technology* technology(const std::string& id) const;
};

struct LoadOptions {
    bool allow_intermediate = false;  // accept _metadata.finalize_required files
    bool validate = true;             // run semantic validation after parsing
    // The single NORMATIVE channel for non-fatal reader notes: every event,
    // undeduplicated, in the order it happened, and it is what a consumer counts
    // or gates on. Never called with a fatal condition (those throw), never
    // stderr. ChipletDocument::warnings carries the same notes and is
    // non-normative CONVENIENCE for a host that would rather read a vector than
    // set a callback; so is the Python reference's stdlib `warnings` emission,
    // which is deduplicated per version and is a process-global the host
    // configures. A consumer that needs the events sets this.
    //
    // A note about an unrecognised member of a closed vocabulary is produced at
    // PARSE, so it arrives with validate = false as well. That is deliberate: a
    // consumer running with validation off is the one most likely to meet a
    // document from a newer minor.
    std::function<void(const std::string&)> on_warn;
};

struct DumpOptions {
    bool validate = true;             // validate before serializing
};

// Parse a .chiplet document from a YAML string. Throws ChipletFormatError on a
// malformed or unsupported document.
ChipletDocument loads(const std::string& text, const LoadOptions& opts = {});

// Read and parse a .chiplet file. Throws ChipletFormatError on I/O or parse
// failure.
ChipletDocument load(const std::string& path, const LoadOptions& opts = {});

// Serialize a document to a canonical YAML string (semantic, not byte-exact to
// the GPL host writers).
std::string dumps(const ChipletDocument& doc, const DumpOptions& opts = {});

// Serialize a document to a file.
void dump(const ChipletDocument& doc, const std::string& path,
          const DumpOptions& opts = {});

// The top-level block grammar's key sequence, as a public verdict.
//
// A host that writes a .chiplet on its own (the studio's save path) needs the
// same question the writer here asks of its own output: does the text it is
// about to write split into exactly the top-level keys it meant to write, in
// order? Before this struct existed the splitter was reachable only through
// `flow_source` and `dumps()`, so every host re-implemented the grammar or
// trusted its serializer. This is a THIN WRAPPER over the reader's own
// splitter, the one `loads()` and `dumps()` run; there is no second grammar.
//
//   keys            the top-level keys in document order, PREAMBLE EXCLUDED
//                   (lines before the first key line own no key). Meaningful
//                   only while `not_splittable` is empty.
//   not_splittable  empty when the document splits. Otherwise the reason it
//                   cannot be split, in the splitter's own words (a quoted key
//                   at column zero, an unattributable line at column zero),
//                   and `keys` MUST NOT be read: the blocks behind them
//                   mis-attribute those bytes. A host treats a non-empty value
//                   as a refusal, never as "no keys" and never as success.
//
// What this is NOT. It is not a loader and makes no semantic claim: a document
// carrying a forbidden line break (NEL, U+2028, U+2029, a lone CR) splits
// perfectly well here and is refused by loads(); a document that is not YAML
// at all may split. And the split alone does not validate an emission: a
// key-shaped continuation line inside a multi-line quoted scalar is a key line
// to this grammar and a scalar to the parser, so `keys` can name a key the
// parser never sees. The host compares `keys` against the list it INTENDED to
// write, obtained independently, and refuses on any difference or any order
// change; agreement between the two is the property, not a non-empty result.
struct TopLevelKeys {
    std::vector<std::string> keys;
    std::string not_splittable;
};

// Split `text` with the reader's own top-level block grammar and report the key
// sequence. Throws ChipletFormatError for a REPEATED top-level key, exactly as
// loads() does: that document is ill-formed (PyYAML reads the last value,
// yaml-cpp the first, and yaml-cpp reports both keys), so there is no sequence
// to hand back. Every other verdict is in the returned struct.
TopLevelKeys split_top_level_keys(const std::string& text);

// Parser-attested, independently movable root entries. Offsets are half-open
// UTF-8 BYTE offsets into the unchanged input. This is not schema validation or
// permission to save. Unsupported/malformed documents return no partial data.
struct CapturedTopLevelBlock {
    std::string key;
    std::size_t byte_start = 0;
    std::size_t byte_end = 0;
    std::string raw;
};

struct TopLevelBlockCapture {
    bool safe = false;
    std::string preamble;
    std::vector<CapturedTopLevelBlock> blocks;
    std::string reason_code;
    std::string reason;
    std::optional<std::size_t> byte_offset;
};

TopLevelBlockCapture capture_top_level_blocks(const std::string& text);

// The lowest format minor that can carry every member `doc` uses, or "" when no
// member postdates 1.0. dumps() never stamps below it.
std::string content_format_floor(const ChipletDocument& doc);

// Validate the semantic invariants that survive into the struct model
// (format_version, intermediate guard, assembly.name, component id/type,
// interface id and non-empty type, netlist net.name). Structural checks (a
// section being a map vs a sequence) happen during parsing. WHICH interface type
// is not checked here or anywhere else in this library: the vocabulary binds
// writers and the schema enforces it, and an unrecognised member is reported
// through on_warn and carried. Throws ChipletFormatError on the first violation.
// Set allow_intermediate to accept finalize_required documents.
void validate(const ChipletDocument& doc, bool allow_intermediate = false,
              const std::function<void(const std::string&)>& on_warn = {});

}  // namespace chiplet_format_io

#endif  // CHIPLET_FORMAT_IO_HPP
