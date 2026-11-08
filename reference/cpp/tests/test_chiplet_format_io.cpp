// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 IHP GmbH
//
// Tests for the chiplet_format_io C++ reference reader/writer. Dependency-clean:
// a tiny hand-rolled assert harness, no test framework. Mirrors the coverage of
// the Python reference tests (reference/python/tests/test_chiplet_format_io.py).

#include "chiplet_format_io/chiplet_format_io.hpp"

// yaml-cpp is used here only to read the JSON oracle (JSON is YAML), never to
// judge what the reader produced.
#include <yaml-cpp/yaml.h>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>

namespace cfio = chiplet_format_io;

namespace {

int g_failures = 0;
int g_checks = 0;

void check(bool cond, const std::string& what) {
    ++g_checks;
    if (!cond) {
        ++g_failures;
        std::cerr << "  FAIL: " << what << "\n";
    }
}

// Run `fn`; pass the check iff it does NOT throw.
template <typename Fn>
void check_no_throw(Fn fn, const std::string& what) {
    ++g_checks;
    try {
        fn();
        return;
    } catch (const cfio::ChipletFormatError& e) {
        std::cerr << "  FAIL (unexpected throw): " << what << ": " << e.what()
                  << "\n";
    } catch (...) {
        std::cerr << "  FAIL (unexpected throw): " << what << "\n";
    }
    ++g_failures;
}

// Run `fn`; pass the check iff it throws ChipletFormatError.
template <typename Fn>
void check_throws(Fn fn, const std::string& what) {
    ++g_checks;
    bool threw = false;
    try {
        fn();
    } catch (const cfio::ChipletFormatError&) {
        threw = true;
    } catch (...) {
        // wrong exception type
    }
    if (!threw) {
        ++g_failures;
        std::cerr << "  FAIL (expected throw): " << what << "\n";
    }
}

// "U+2028" -> the UTF-8 bytes of that code point. The oracle names its subjects
// by code point, and a test that needs the raw character has to be able to build
// it rather than carry a second copy of the set.
std::string utf8_of(const std::string& code_point) {
    const unsigned int cp =
        static_cast<unsigned int>(std::stoul(code_point.substr(2), nullptr, 16));
    std::string out;
    if (cp < 0x80) {
        out += static_cast<char>(cp);
    } else if (cp < 0x800) {
        out += static_cast<char>(0xC0 | (cp >> 6));
        out += static_cast<char>(0x80 | (cp & 0x3F));
    } else {
        out += static_cast<char>(0xE0 | (cp >> 12));
        out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
        out += static_cast<char>(0x80 | (cp & 0x3F));
    }
    return out;
}

std::string read_file(const std::string& path) {
    std::ifstream f(path);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

const std::string kExamplesDir = CHIPLET_EXAMPLES_DIR;
const std::string kHeaderFile = CHIPLET_HEADER_FILE;
const std::string kSourceFile = CHIPLET_SOURCE_FILE;

// Where the shared top-level block grammar oracle lives. CMake derives a default
// from this source tree; the CHIPLET_BLOCK_ORACLE environment variable overrides
// it at RUN time, which is what lets a binary built in one checkout be measured
// against another checkout's oracle instead of the absolute path baked into it.
std::string oracle_path() {
    const char* env = std::getenv("CHIPLET_BLOCK_ORACLE");
    if (env != nullptr && *env != '\0') return std::string(env);
    return std::string(CHIPLET_BLOCK_ORACLE_DEFAULT);
}

// The oracle, parsed once. Reached only after main() has established that the
// file is there: an absent oracle must FAIL and name the path, never let the
// grammar tests report green over cases they never read.
const YAML::Node& block_oracle() {
    static const YAML::Node node = YAML::LoadFile(oracle_path());
    return node;
}

// The version policy's verdict oracle, on the same terms: a compile-time default
// derived from this source tree, overridable at run time with
// CHIPLET_VERSION_ORACLE, and checked for existence in main() before any case
// runs. JSON is a subset of YAML, so yaml-cpp reads the file the Python side
// reads with json.load.
std::string version_oracle_path() {
    const char* env = std::getenv("CHIPLET_VERSION_ORACLE");
    if (env != nullptr && *env != '\0') return std::string(env);
    return std::string(CHIPLET_VERSION_ORACLE_DEFAULT);
}

const YAML::Node& version_oracle() {
    static const YAML::Node node = YAML::LoadFile(version_oracle_path());
    return node;
}

// The carry-rule oracle, on the same terms as the other two: a
// compile-time default from this source tree, CHIPLET_VOCABULARY_ORACLE
// overrides at run time, and main() fails the run if the file is not there
// rather than letting the cross product report green over cells it never read.
// The fixtures it names are read from the same directory it lives in.
std::string vocabulary_oracle_path() {
    const char* env = std::getenv("CHIPLET_VOCABULARY_ORACLE");
    if (env != nullptr && *env != '\0') return std::string(env);
    return std::string(CHIPLET_VOCABULARY_ORACLE_DEFAULT);
}

const YAML::Node& vocabulary_oracle() {
    static const YAML::Node node = YAML::LoadFile(vocabulary_oracle_path());
    return node;
}

const std::string kFixturesDir = CHIPLET_FIXTURES_DIR;

void test_roundtrip_canonical_example() {
    const std::string path = kExamplesDir + "/interposer_demo_design.chiplet";
    cfio::ChipletDocument first = cfio::load(path);
    check(first.format_version == cfio::SUPPORTED_FORMAT_VERSION,
          "example format_version is 1.0");

    // dump is idempotent after the first normalization (the C++ analog of the
    // Python dict round-trip equality).
    const std::string d1 = cfio::dumps(first);
    cfio::ChipletDocument second = cfio::loads(d1);
    const std::string d2 = cfio::dumps(second);
    check(d1 == d2, "load->dump is a semantic fixed point on the example");
}

void test_all_example_chiplets_parse() {
    int count = 0;
    for (const auto& entry : std::filesystem::directory_iterator(kExamplesDir)) {
        if (entry.path().extension() == ".chiplet") {
            ++count;
            cfio::LoadOptions opts;
            opts.allow_intermediate = true;
            cfio::ChipletDocument doc = cfio::load(entry.path().string(), opts);
            check(doc.format_version == cfio::SUPPORTED_FORMAT_VERSION,
                  "example " + entry.path().filename().string() + " is v1.0");
        }
    }
    check(count > 0, "found at least one example .chiplet");
}

void test_missing_format_version_rejected() {
    check_throws([] { cfio::loads("assembly:\n  name: x\n"); },
                 "missing format_version rejected");
}

void test_unsupported_version_rejected() {
    check_throws(
        [] { cfio::loads("format_version: \"2.0\"\nassembly:\n  name: x\n"); },
        "unsupported format_version rejected");
}

void test_assembly_name_required() {
    check_throws(
        [] { cfio::loads("format_version: \"1.0\"\nassembly:\n  units: um\n"); },
        "assembly.name required");
}

void test_component_requires_id_and_type() {
    check_throws(
        [] {
            cfio::loads(
                "format_version: \"1.0\"\nassembly:\n  name: a\n"
                "components:\n- type: die\n");
        },
        "component without id rejected");
}

void test_intermediate_refused_then_allowed() {
    const std::string doc =
        "format_version: \"1.0\"\n"
        "_metadata:\n  finalize_required: true\n"
        "assembly:\n  name: a\n";
    check_throws([&] { cfio::loads(doc); },
                 "intermediate refused by default");
    cfio::LoadOptions opts;
    opts.allow_intermediate = true;
    cfio::ChipletDocument parsed = cfio::loads(doc, opts);
    check(parsed.assembly.name == "a", "intermediate accepted with flag");
    check(parsed.metadata.finalize_required, "finalize_required preserved");
}

void test_dump_roundtrips_components_order() {
    const std::string path = kExamplesDir + "/interposer_demo_design.chiplet";
    cfio::ChipletDocument doc = cfio::load(path);
    std::vector<std::string> ids;
    for (const auto& c : doc.components) ids.push_back(c.id);
    cfio::ChipletDocument reloaded = cfio::loads(cfio::dumps(doc));
    std::vector<std::string> ids2;
    for (const auto& c : reloaded.components) ids2.push_back(c.id);
    check(ids == ids2, "component order preserved across dump/load");
    check(ids.size() >= 2, "example has the expected components");
}

// Inverted by the carry-rule ruling, and the parity assertion is RESTATED rather
// than deleted, because parity is what the original test was really about. It
// asserted that an unknown type throws here, so that a document refused by one
// reference reader was refused by the other. The verdict moved, the property did
// not: the two readers must still agree on this document, and now they agree by
// CARRYING it. The Python twin is
// conformance/test_interface_types.py::test_the_python_validator_carries_an_unknown_type.
void test_unknown_interface_type_is_carried_not_refused() {
    const std::string doc =
        "format_version: \"1.0\"\nassembly:\n  name: a\n"
        "interfaces:\n- id: i1\n  type: bogus_bond\n";
    cfio::ChipletDocument parsed;
    // Through check_no_throw, not a bare call: a reader that still refuses must
    // make this test FAIL and let the cross product below run and fail too. An
    // unhandled throw here would abort the binary and hide every cell after it,
    // which is the wrong fix being caught in the least useful possible way.
    check_no_throw([&] { parsed = cfio::loads(doc); },
                   "an unknown interface type is not refused");
    check(parsed.interfaces.size() == 1 &&
              parsed.interfaces[0].type == "bogus_bond",
          "and the string is carried through verbatim");
    // The type is still REQUIRED; carrying an unknown one is not the same as
    // accepting a missing one, and collapsing the two would delete rule 4.
    check_throws([&] {
        cfio::loads("format_version: \"1.0\"\nassembly:\n  name: a\n"
                    "interfaces:\n- id: i1\n");
    }, "an interface with no type at all is still refused");
    check_throws([&] {
        cfio::ChipletDocument d;
        d.format_version = cfio::SUPPORTED_FORMAT_VERSION;
        d.assembly.name = "a";
        cfio::Interface iface;
        iface.id = "i1";
        d.interfaces.push_back(iface);
        cfio::validate(d);
    }, "and validate() still refuses an empty type");
}

// The cross product of the carry rule, run against the SHARED oracle
// conformance/fixtures/unknown_vocabulary_cases.json. The Python cells are in
// conformance/test_unknown_vocabulary_roundtrip.py, over the same documents and
// the same cells, so the two implementations are measured against one file and
// never against each other. Axes: two documents (the plain one, and the one
// whose interface meets a pad with a KNOWN io_class, which is rule 8 and was the
// third refusal site), validate on and off, load and load-then-dump.
void test_unknown_vocabulary_is_carried_across_the_cross_product() {
    const YAML::Node& oracle = vocabulary_oracle();
    const std::string unknown = oracle["unknown_type"].as<std::string>();
    const int notes_expected = oracle["expect"]["notes_per_load"].as<int>();
    int cells = 0;
    for (const auto& doc : oracle["documents"]) {
        const std::string file = doc["file"].as<std::string>();
        const std::string iface_id = doc["interface"].as<std::string>();
        const std::string text = read_file(kFixturesDir + "/" + file);
        check(!text.empty(), file + ": the fixture is where the oracle says");
        for (const auto& cell : oracle["cells"]) {
            ++cells;
            const bool validate = cell["validate"].as<bool>();
            const std::string path = cell["path"].as<std::string>();
            const std::string tag =
                file + " validate=" + (validate ? "true" : "false") + " " + path;

            std::vector<std::string> notes;
            cfio::LoadOptions opts;
            opts.validate = validate;
            opts.on_warn = [&notes](const std::string& m) {
                notes.push_back(m);
            };
            cfio::ChipletDocument parsed;
            check_no_throw([&] { parsed = cfio::loads(text, opts); },
                           tag + ": loads without refusing");

            const cfio::Interface* found = nullptr;
            for (const auto& iface : parsed.interfaces) {
                if (iface.id == iface_id) found = &iface;
            }
            check(found != nullptr && found->type == unknown,
                  tag + ": the unknown type is carried verbatim");

            // Exactly one note per load on the NORMATIVE channel, under either
            // setting of the flag. The note is produced at parse for that
            // reason: a consumer running with validate=false is the one most
            // likely to meet a document from a newer minor.
            check(static_cast<int>(notes.size()) == notes_expected,
                  tag + ": one note per load on on_warn");
            check(!notes.empty() &&
                      notes[0].find(unknown) != std::string::npos,
                  tag + ": and the note names the type");

            // Undeduplicated: two loads in one process, two notes.
            check_no_throw([&] { cfio::loads(text, opts); },
                           tag + ": loads a second time");
            check(static_cast<int>(notes.size()) == 2 * notes_expected,
                  tag + ": the note is not deduplicated");

            if (path == "load_then_dump") {
                cfio::DumpOptions dopts;
                dopts.validate = validate;
                std::string written;
                check_no_throw([&] { written = cfio::dumps(parsed, dopts); },
                               tag + ": writes back without refusing");
                check(written.find(unknown) != std::string::npos,
                      tag + ": the writer re-emits the type verbatim");
                cfio::ChipletDocument again;
                check_no_throw([&] { again = cfio::loads(written, opts); },
                               tag + ": and the written file loads again");
                const cfio::Interface* twice = nullptr;
                for (const auto& iface : again.interfaces) {
                    if (iface.id == iface_id) twice = &iface;
                }
                check(twice != nullptr && twice->type == unknown,
                      tag + ": with the same string");
            }
        }
    }
    check(cells == 8, "the oracle still carries the whole cross product");

    // The vocabulary is exported so a consumer can refuse the ELEMENT, which is
    // what makes the MINOR label true rather than aspirational. A C++ consumer
    // could not do it while the list lived in the translation unit.
    check(cfio::kKnownInterfaceTypes.size() == 5,
          "the known-type vocabulary is reachable from the header");
    check(!cfio::is_known_interface_type(unknown) &&
              cfio::is_known_interface_type("micro_bump"),
          "and the membership test with it");
}

// One document per member of the closed vocabulary of validation rule 4. That
// the four copies of the LIST agree (this reader, the schema, the spec prose,
// the Python constant) is checked by conformance/test_interface_types.py on the
// text; what a text comparison cannot show is that the reader actually accepts
// each member, which is this. solder_bump is the member added at 1.1.
void test_every_known_interface_type_is_accepted() {
    for (const std::string& type : {std::string("micro_bump"),
                                    std::string("copper_pillar"),
                                    std::string("tsv"),
                                    std::string("wire_bond"),
                                    std::string("solder_bump")}) {
        // Stamped at the member's own minor: solder_bump is 1.1.
        const std::string stamp = type == "solder_bump" ? "1.1" : "1.0";
        const std::string doc =
            "format_version: \"" + stamp + "\"\nassembly:\n  name: a\n"
            "interfaces:\n- id: i1\n  type: " + type + "\n";
        check_no_throw([&] { cfio::loads(doc); },
                       "interface type " + type + " accepted");
    }
}

// solder_bump is format 1.1: refused in a 1.0 document, and the lossy writer,
// which otherwise stamps SUPPORTED_FORMAT_VERSION, stamps 1.1 when it writes
// one, so a 1.1 document read and written back is never labelled 1.0.
// assembly.units is closed to "um" and must be understood: any other value,
// a present empty one included, is refused; absent reads as the default.
void test_units_are_closed_to_um() {
    const std::string head = "format_version: \"1.1\"\nassembly:\n  name: a\n";
    check_no_throw([&] { cfio::loads(head); }, "absent units read as um");
    check_no_throw([&] { cfio::loads(head + "  units: um\n"); }, "um reads");
    for (const std::string& bad : {std::string("mm"), std::string("\"\""),
                                   std::string("UM"), std::string("[um]")}) {
        check_throws([&] { cfio::loads(head + "  units: " + bad + "\n"); },
                     "units " + bad + " is refused");
    }
    cfio::LoadOptions lax;
    lax.validate = false;
    check_no_throw([&] { cfio::loads(head + "  units: mm\n", lax); },
                   "validate = false does not judge units");
}

void test_solder_bump_is_held_to_its_minor() {
    check(cfio::content_format_floor(cfio::ChipletDocument{}).empty(),
          "an empty document has no content floor");
    check_throws([] {
        cfio::loads("format_version: \"1.0\"\nassembly:\n  name: a\n"
                    "interfaces:\n- id: i1\n  type: solder_bump\n");
    }, "solder_bump in a 1.0 document is refused");
    cfio::ChipletDocument doc = cfio::loads(
        "format_version: \"1.6\"\nassembly:\n  name: a\n"
        "interfaces:\n- id: i1\n  type: solder_bump\n");
    check(cfio::content_format_floor(doc) == "1.1",
          "solder_bump raises the content floor to 1.1");
    const cfio::ChipletDocument back = cfio::loads(cfio::dumps(doc));
    check(back.format_version == "1.1",
          "the lossy writer stamps the content floor, not 1.0");
    cfio::LoadOptions plain;
    plain.validate = false;
    cfio::ChipletDocument micro = cfio::loads(
        "format_version: \"1.6\"\nassembly:\n  name: a\n"
        "interfaces:\n- id: i1\n  type: micro_bump\n", plain);
    check(cfio::loads(cfio::dumps(micro)).format_version ==
              cfio::SUPPORTED_FORMAT_VERSION,
          "with no later member the lossy writer still stamps SUPPORTED");
}

// The flow block is the exact source slice, which is what flow rule 4 needs and
// what the header has always promised. The predecessor of these tests asserted
// !flow_yaml.empty() under the label "captured verbatim", which cannot tell a
// re-serialisation from the source text: it passed just as happily when the
// field held YAML::Dump(root["flow"]), comments gone and scalars re-quoted.
//
// The cases come from conformance/fixtures/top_level_blocks_cases.json, the same
// file the Python reference and the KiCad plugin's merge splitter run, so the
// three implementations are measured against one oracle and never against each
// other.
void test_flow_block_is_the_exact_source_slice() {
    const YAML::Node& oracle = block_oracle();
    int cases = 0;
    for (const auto& c : oracle["splits"]) {
        // A splitter-only document (a repeated top-level key) is not one a
        // reader is required to load.
        if (c["loadable"] && !c["loadable"].as<bool>()) continue;
        const std::string name = c["name"].as<std::string>();
        std::string expected;
        bool has_flow = false;
        for (const auto& b : c["blocks"]) {
            if (b["key"].as<std::string>() == "flow") {
                expected = b["text"].as<std::string>();
                has_flow = true;
            }
        }
        if (!has_flow) continue;
        ++cases;
        cfio::ChipletDocument parsed = cfio::loads(c["doc"].as<std::string>());
        check(parsed.has_flow, name + ": flow block detected");
        check(parsed.flow_source == cfio::FlowSource::Slice,
              name + ": the flow bytes are recorded as a source slice");
        check(parsed.flow_yaml == expected,
              name + ": flow_yaml is the source slice, byte for byte");
    }
    check(cases >= 5, "the oracle still carries the flow split cases");
}

// Rule 4 end to end: a document this writer did not author goes out with the
// same bytes it came in with. A node dump fails this on the comment alone.
void test_flow_block_is_re_emitted_byte_for_byte() {
    const YAML::Node& oracle = block_oracle();
    const std::string doc =
        oracle["splits"][0]["doc"].as<std::string>();
    cfio::ChipletDocument parsed = cfio::loads(doc);
    const std::string written = cfio::dumps(parsed);
    check(written.find(parsed.flow_yaml) != std::string::npos,
          "dumps() writes the flow slice verbatim");
    cfio::ChipletDocument reloaded = cfio::loads(written);
    check(reloaded.has_flow, "flow block survives dump/load");
    check(reloaded.flow_yaml == parsed.flow_yaml,
          "flow block round-trips byte for byte");
    check(parsed.flow_yaml.find("# export first") != std::string::npos,
          "the case under test carries the comment a node dump would drop");
}

// The writer's key grammar, the C++ half of it. The Python reference could emit a document its
// OWN splitter mis-attributes: a top-level key it cannot write bare came out as
// an explicit key (`? "a\Lb"` with a separate `: x: 1` line) or as `a b:`, both
// at column zero and neither a key line, so the split reported two top-level
// keys where the parse reported three. The generated mapping uses literal key
// names, but the appended flow source is caller-controlled. The writer checks
// its complete output; the corpus below measures its ordinary generated path,
// and the malicious source-slice test measures the appended path.
//
// The splitter is not a public symbol, so the measurement goes through the two
// places its verdict IS observable from outside. First, `flow_source`: a document
// whose bytes carry an unattributable line at column zero is not splittable at
// all, and a flow block in a file that is not splittable comes back as
// NotDelimitable rather than Slice. Every fixture is given a flow block for the
// probe, including the ones that had none, so the whole corpus reaches it.
// Second, the emitted text itself: with the document splittable, the split keys
// ARE the column-zero key lines, and they must be exactly what the parser reads
// back from the same bytes, in the same order.
void test_the_writer_output_splits_into_exactly_the_keys_it_wrote() {
    int documents = 0;
    for (const auto& entry :
         std::filesystem::directory_iterator(kFixturesDir)) {
        if (entry.path().extension() != ".chiplet") continue;
        const std::string name = entry.path().filename().string();
        std::ifstream file(entry.path());
        std::stringstream buffer;
        buffer << file.rdbuf();
        cfio::LoadOptions lo;
        lo.validate = false;
        cfio::ChipletDocument doc;
        try {
            doc = cfio::loads(buffer.str(), lo);
        } catch (const cfio::ChipletFormatError&) {
            continue;  // a fixture the reader refuses has no writer output
        }
        // A flow block for every document, re-authored where there was none or
        // where the source had no slice, so the not-splittable probe below runs
        // over the whole corpus instead of the two fixtures that carry one.
        doc.has_flow = true;
        doc.flow_yaml = "flow:\n  steps: []\n";
        doc.flow_source = cfio::FlowSource::Slice;

        cfio::DumpOptions dop;
        dop.validate = false;
        const std::string text = cfio::dumps(doc, dop);
        ++documents;

        // (1) The writer's output is splittable. If any line it wrote at column
        // zero were unattributable, the whole file would be, and the flow block
        // would come back with no slice.
        const cfio::ChipletDocument reloaded = cfio::loads(text, lo);
        check(reloaded.flow_source == cfio::FlowSource::Slice,
              name + ": the writer's output is splittable");
        check(reloaded.flow_yaml == "flow:\n  steps: []\n",
              name + ": and the flow block comes back byte for byte");

        // (2) The split keys are exactly the keys written, in order. With (1)
        // established, every line at column zero is attributable, so the split
        // keys are the column-zero key lines and nothing else; this walks them
        // and compares against what the PARSER reads from the same bytes.
        std::vector<std::string> split_keys;
        std::size_t start = 0;
        while (start < text.size()) {
            const std::size_t nl = text.find('\n', start);
            const std::size_t end =
                (nl == std::string::npos) ? text.size() : nl + 1;
            std::string content = text.substr(start, end - start);
            start = end;
            if (!content.empty() && content.back() == '\n') content.pop_back();
            if (!content.empty() && content.back() == '\r') content.pop_back();
            if (content.find_first_not_of(" \t") == std::string::npos ||
                content[0] == ' ' ||
                content[0] == '#') {
                continue;
            }
            const std::size_t colon = content.find(':');
            check(colon != std::string::npos && colon > 0 &&
                      content.substr(0, colon).find_first_of(" \t") == std::string::npos &&
                      content[0] != '-',
                  name + ": every column-zero line the writer emits is a key "
                         "line, not a sequence entry and not a key it had to "
                         "spell some other way (" + content + ")");
            if (colon == std::string::npos || colon == 0) continue;
            split_keys.push_back(content.substr(0, colon));
        }

        std::vector<std::string> parsed_keys;
        for (const auto& kv : YAML::Load(text)) {
            parsed_keys.push_back(kv.first.as<std::string>());
        }
        check(split_keys == parsed_keys,
              name + ": the split recovers exactly the top-level keys the "
                     "parser reads, in order");
        check(!parsed_keys.empty(), name + ": and there are some");
    }
    check(documents >= 8,
          "the writer-output corpus is not empty (" +
              std::to_string(documents) + " documents)");
}

void test_writer_refuses_extra_or_unattributable_flow_source() {
    cfio::ChipletDocument doc = cfio::loads(
        "format_version: \"1.0\"\nassembly:\n  name: demo\n");
    doc.has_flow = true;
    doc.flow_source = cfio::FlowSource::Slice;
    // The check is unconditional: validate=false cannot waive output ownership.
    for (const bool validate : {false, true}) {
        cfio::DumpOptions opts;
        opts.validate = validate;
        for (const std::string& slice : {
                 std::string("flow:\n  steps: []\na b: value\n"),
                 std::string("flow:\n  steps: []\nnetlist:\n  nets: []\n"),
                 std::string("flow:\n  note: \"first\nnetlist: x\n  last\"\n")}) {
            doc.flow_yaml = slice;
            check_throws([&] { cfio::dumps(doc, opts); },
                         "writer refuses a flow slice with extra or unattributable keys");
            try {
                cfio::dumps(doc, opts);
            } catch (const cfio::ChipletFormatError& error) {
                check(std::string(error.what()).find("line ") != std::string::npos,
                      "writer refusal identifies the offending output line");
            }
        }
        doc.flow_yaml = "flow:\n  # preserve this comment\n  steps: []\n";
        const std::string text = cfio::dumps(doc, opts);
        const cfio::ChipletDocument again = cfio::loads(text);
        check(again.flow_yaml == doc.flow_yaml,
              "a valid supplied flow slice survives the post-check byte for byte");
    }
}

// A quoted key at column zero is valid YAML and is NOT a top-level key line, so
// a splitter would hand the block to the preceding key, whose owner regenerates
// it away on the next export. That makes the document NOT SPLITTABLE, which is
// not the same as invalid: flow rule 1 says a reader must not reject a document
// over a block it cannot handle, so it LOADS. The guarantee that actually breaks
// is the write one, and that is where it is charged.
void test_quoted_key_at_column_zero_loads_and_may_refuse_to_write() {
    const YAML::Node& oracle = block_oracle();
    check(oracle["version"] && oracle["version"].as<int>() >= 2,
          "the oracle states its version, so a stale copy can say so");
    int cases = 0;
    bool has_unattributable = false;
    bool has_explicit_key = false;
    bool has_space_key = false;
    bool has_non_space_tab_blank = false;
    for (const auto& c : oracle["refuse"]) {
        const std::string name = c["name"].as<std::string>();
        const std::string doc = c["doc"].as<std::string>();
        if (c["kind"].as<std::string>() ==
            "unattributable_line_at_column_zero") {
            has_unattributable = true;
            has_explicit_key |= doc.find("\n? ") != std::string::npos;
            has_space_key |= doc.find("\na b:") != std::string::npos;
            has_non_space_tab_blank |= doc.find("\n\xC2\xA0\n") != std::string::npos;
        }
        // Which implementation owes the refusal is a FIELD, never the group.
        // This reader is only ever the "reader" half of it, and reads "loads"
        // as it always did; the check here is that the two say the same thing,
        // so a row that reaches a splitting host cannot mean one verdict there
        // and another here.
        std::set<std::string> refused_by;
        for (const auto& who : c["refused_by"]) {
            refused_by.insert(who.as<std::string>());
        }
        check(!refused_by.empty(), name + ": says who refuses it");
        check((refused_by.count("reader") == 1) == !c["loads"].as<bool>(),
              name + ": refused_by and loads agree about the reader");
        if (!c["loads"].as<bool>()) {
            check_throws([&] { cfio::loads(doc); }, name + ": refused at load");
            continue;
        }
        ++cases;
        check_no_throw([&] { cfio::loads(doc); }, name + ": still loads");
        cfio::ChipletDocument parsed = cfio::loads(doc);
        check(!parsed.assembly.name.empty(), name + ": and is read normally");
        if (refused_by.count("splitter")) {
            std::string probe = doc;
            if (!parsed.has_flow) {
                if (probe.empty() || probe.back() != '\n') probe += '\n';
                probe += "flow:\n  steps: []\n";
            }
            const cfio::ChipletDocument split_probe = cfio::loads(probe);
            check(split_probe.has_flow, name + ": splitter probe has a flow node");
            check(split_probe.flow_source == cfio::FlowSource::NotDelimitable,
                  name + ": splitter obligation refuses to delimit the source");
            check(split_probe.flow_yaml.empty(), name + ": probe invents no slice");
            check_throws([&] { cfio::dumps(split_probe); },
                         name + ": probe refuses to write an unavailable slice");
        }
        if (c["writes"].as<bool>()) {
            check_no_throw([&] { cfio::dumps(parsed); },
                           name + ": no flow block, so it writes back");
        } else {
            check(parsed.flow_source == cfio::FlowSource::NotDelimitable,
                  name + ": its flow block has no source slice");
            check(parsed.flow_yaml.empty(), name + ": and no invented bytes");
            check_throws([&] { cfio::dumps(parsed); },
                         name + ": refuses to write the flow block back");
        }
    }
    check(cases >= 3, "the oracle still carries the refuse cases");
    check(has_unattributable, "oracle requires the unattributable refusal kind");
    check(has_explicit_key, "oracle requires the explicit-key writer spelling");
    check(has_space_key, "oracle requires the bare key with a space");
    check(has_non_space_tab_blank, "oracle requires a non-SPACE/TAB blank line");
}

// The PUBLIC key-sequence API, measured against the same oracle the
// private splitter is. Three claims. (1) Every document the oracle says splits
// comes back splittable with exactly the oracle's keys, preamble excluded, in
// order. (2) Every document a SPLITTER refuses comes back in the form that
// document calls for: a repeated top-level key throws, as loads() does; the rest
// report `not_splittable` with NO keys, and still load when the oracle says so.
// (3) The API is not a loader and the split alone is not an emission check: a
// reader-only refusal (forbidden line break) splits here and is refused by
// loads(); a key-shaped continuation inside a quoted scalar is a key to this
// grammar and a scalar to the parser, so the host must compare against the keys
// it intended to write. The Python reference runs the same rows in
// conformance/test_top_level_keys.py.
void test_split_top_level_keys_matches_the_oracle() {
    const YAML::Node& oracle = block_oracle();
    int splits = 0, raises = 0, reports = 0, reader_only = 0;
    for (const auto& c : oracle["splits"]) {
        ++splits;
        const std::string name = c["name"].as<std::string>();
        std::vector<std::string> expected;
        for (const auto& b : c["blocks"]) {
            const std::string key = b["key"].as<std::string>();
            if (!key.empty()) expected.push_back(key);  // "" is the preamble
        }
        cfio::TopLevelKeys verdict;
        check_no_throw([&] { verdict = cfio::split_top_level_keys(
                                 c["doc"].as<std::string>()); },
                       name + ": a split document does not throw");
        check(verdict.not_splittable.empty(), name + ": reported splittable");
        check(verdict.keys == expected,
              name + ": exactly the oracle's keys, preamble excluded, in order");
    }
    // Refusals: the FIELD says who refuses, never the group name.
    std::vector<YAML::Node> splitter_cases;
    for (const auto& c : oracle["refuse"]) splitter_cases.push_back(c);
    for (const auto& c : oracle["not_delimitable"]) splitter_cases.push_back(c);
    for (const auto& c : splitter_cases) {
        const std::string name = c["name"].as<std::string>();
        const std::string doc = c["doc"].as<std::string>();
        std::set<std::string> refused_by;
        for (const auto& who : c["refused_by"]) {
            refused_by.insert(who.as<std::string>());
        }
        if (!refused_by.count("splitter")) {
            if (refused_by == std::set<std::string>{"reader"}) {
                // (3) not a loader: the grammar has an answer for these bytes.
                ++reader_only;
                cfio::TopLevelKeys verdict;
                check_no_throw([&] { verdict = cfio::split_top_level_keys(doc); },
                               name + ": reader-only refusal still splits");
                check(verdict.not_splittable.empty() && !verdict.keys.empty(),
                      name + ": splittable with keys");
                check_throws([&] { cfio::loads(doc); },
                             name + ": and loads() is where it is refused");
            }
            continue;
        }
        if (c["kind"].as<std::string>() == "repeated_top_level_key") {
            ++raises;
            check_throws([&] { cfio::split_top_level_keys(doc); },
                         name + ": a repeated top-level key throws");
            check_throws([&] { cfio::loads(doc); },
                         name + ": as loads() does");
            continue;
        }
        ++reports;
        cfio::TopLevelKeys verdict;
        check_no_throw([&] { verdict = cfio::split_top_level_keys(doc); },
                       name + ": a not-splittable document does not throw");
        check(!verdict.not_splittable.empty(), name + ": reports why");
        check(verdict.keys.empty(), name + ": and hands back no keys");
        check(verdict.not_splittable.find("docs/CHIPLET_FORMAT_SPEC.md") !=
                  std::string::npos,
              name + ": the reason cites the grammar");
        if (c["loads"] && c["loads"].as<bool>()) {
            check_no_throw([&] { cfio::loads(doc); },
                           name + ": splittability is not loadability");
        }
    }
    check(splits >= 5, "the oracle still carries the split cases");
    check(raises >= 1, "the oracle still carries a repeated-key case");
    check(reports >= 3, "the oracle still carries not-splittable cases");
    check(reader_only >= 1, "the oracle still carries a reader-only refusal");

    // The document a host must not be able to bless with the API alone: a
    // continuation line of a double-quoted scalar, key-shaped at column zero.
    // Valid YAML; two keys to the parser, three to the grammar.
    const std::string phantom =
        "format_version: \"1.0\"\nassembly:\n  name: \"demo\nflow: injected\"\n";
    const YAML::Node parsed = YAML::Load(phantom);
    check(parsed.size() == 2 && parsed["assembly"]["name"].as<std::string>() ==
                                    "demo flow: injected",
          "phantom premise: the parser reads two keys and one folded scalar");
    const cfio::TopLevelKeys verdict = cfio::split_top_level_keys(phantom);
    const std::vector<std::string> grammar_sees{"format_version", "assembly",
                                                "flow"};
    const std::vector<std::string> intended{"format_version", "assembly"};
    check(verdict.not_splittable.empty(), "phantom: splittable to the grammar");
    check(verdict.keys == grammar_sees,
          "phantom: the grammar names a key the parser never sees");
    check(verdict.keys != intended,
          "phantom: a host comparing against its intended keys refuses it");

    // Order is part of the verdict.
    const cfio::TopLevelKeys swapped =
        cfio::split_top_level_keys("assembly:\n  name: d\nformat_version: \"1.0\"\n");
    check(swapped.keys == std::vector<std::string>{"assembly", "format_version"},
          "order: same set, different order, different emission");
    // The preamble is never a key.
    const cfio::TopLevelKeys preamble = cfio::split_top_level_keys(
        "# comment first\n\nformat_version: \"1.0\"\nassembly:\n  name: d\n");
    check(preamble.keys == std::vector<std::string>{"format_version", "assembly"},
          "preamble: lines before the first key own no key");
}

// A flow block the grammar cannot delimit: a flow-style document, or a key line
// spelled `flow :`, which YAML reads as the key `flow` and the grammar does not
// see at all. Either way the block has no slice, so re-emitting it byte for byte
// is impossible. The document still LOADS (flow rule 1), the missing slice is
// recorded rather than papered over, and the refusal lands on dumps(): the two
// alternatives there are dropping the block and emitting a node dump in the
// place the source text belongs, and both are silent lies.
void test_flow_block_the_grammar_cannot_delimit_loads_but_does_not_write() {
    const YAML::Node& oracle = block_oracle();
    int cases = 0;
    for (const auto& c : oracle["not_delimitable"]) {
        ++cases;
        const std::string name = c["name"].as<std::string>();
        const std::string doc = c["doc"].as<std::string>();
        check(c["kind"] && c["kind"].as<std::string>() ==
                  "unattributable_line_at_column_zero",
              name + ": records the splitter refusal kind");
        std::set<std::string> refused_by;
        for (const auto& who : c["refused_by"]) {
            refused_by.insert(who.as<std::string>());
        }
        check(refused_by == std::set<std::string>{"splitter"},
              name + ": explicitly assigns refusal to the splitter alone");
        check_no_throw([&] { cfio::loads(doc); }, name + ": loads");
        cfio::ChipletDocument parsed = cfio::loads(doc);
        check(parsed.has_flow, name + ": the flow node is seen");
        check(parsed.flow_source == cfio::FlowSource::NotDelimitable,
              name + ": recorded as having no source slice");
        check(parsed.flow_yaml.empty(), name + ": no invented bytes");
        check_throws([&] { cfio::dumps(parsed); },
                     name + ": refuses to write it back");

        // The way out the refusal names: the host re-authors the block, and now
        // owns the bytes it is asking to have written. A refusal with no way out
        // is a document a host can open and never save.
        cfio::ChipletDocument reauthored = parsed;
        reauthored.flow_yaml = "flow:\n  steps: []\n";
        check_no_throw([&] { cfio::dumps(reauthored); },
                       name + ": saves once the host re-authors the flow");
        cfio::ChipletDocument reloaded = cfio::loads(cfio::dumps(reauthored));
        check(reloaded.has_flow &&
                  reloaded.flow_source == cfio::FlowSource::Slice,
              name + ": and the saved file has a delimitable flow block");
    }
    check(cases >= 2, "the oracle still carries the not_delimitable cases");

    // Without a flow block, both spellings of the same document are fine: the
    // grammar only has to delimit what is there.
    cfio::ChipletDocument parsed =
        cfio::loads("{format_version: \"1.0\", assembly: {name: a}}\n");
    check(!parsed.has_flow && parsed.assembly.name == "a",
          "a flow-style document without a flow block still loads");
    check(parsed.flow_source == cfio::FlowSource::Absent,
          "and reports no flow source at all");
    check_no_throw([&] { cfio::dumps(parsed); },
                   "and writes back without complaint");
}

// The format's line breaks are LF and CRLF. A character a YAML parser breaks a
// line on and the top-level block grammar does not makes a document ill-formed,
// and both reference readers refuse it on the TEXT, before their parser sees it.
// Measured on PyYAML 6.0.3 and yaml-cpp 0.8.0: the smuggle shape gives PyYAML a
// second top-level key and throws here, and the plain-scalar shape loads here
// and throws in PyYAML. So this cannot be checked as "loads() throws": yaml-cpp
// throws on half of these cases anyway, with a message about a map value that
// names neither the character nor the reason. The message is the assertion.
//
// The SET is not written down here. It comes off the oracle, which the Python
// side derives by running PyYAML over a code-point range; a hand-written count
// in this file is what let the rule ship with three of its four members.
void test_forbidden_line_breaks_are_refused_with_a_text_level_reason() {
    const YAML::Node& oracle = block_oracle();
    int cases = 0;
    int expected = 0;
    std::set<std::string> code_points;
    std::map<std::string, int> shapes;
    for (const auto& c : oracle["refuse"]) {
        if (c["kind"].as<std::string>() == "forbidden_line_break") ++expected;
    }
    for (const auto& c : oracle["refuse"]) {
        if (c["kind"].as<std::string>() != "forbidden_line_break") continue;
        ++cases;
        code_points.insert(c["code_point"].as<std::string>());
        ++shapes[c["code_point"].as<std::string>()];
        const std::string name = c["name"].as<std::string>();
        const std::string doc = c["doc"].as<std::string>();
        const std::string code_point = c["code_point"].as<std::string>();
        const std::string line = "line " + c["line"].as<std::string>();
        for (bool validate : {true, false}) {
            cfio::LoadOptions opts;
            opts.validate = validate;
            std::string message;
            try {
                cfio::loads(doc, opts);
            } catch (const cfio::ChipletFormatError& e) {
                message = e.what();
            }
            const std::string tag =
                name + (validate ? " (validate on)" : " (validate off)");
            check(message.find(code_point) != std::string::npos,
                  tag + ": the refusal names " + code_point);
            check(message.find(line) != std::string::npos,
                  tag + ": the refusal names the " + line);
            check(message.find("LF and CRLF") != std::string::npos,
                  tag + ": and says what the line-break set is");
            check(message.find("YAML parse error") == std::string::npos,
                  tag + ": and is the format's refusal, not yaml-cpp's");
        }
    }
    check(cases == expected,
          "every forbidden_line_break row in the oracle was measured");
    check(code_points.size() >= 4,
          "the oracle carries all four members of the line-break set");
    for (const std::string& cp : code_points) {
        check(shapes[cp] >= 2,
              cp + ": the oracle carries both shapes of the disagreement");
    }
}

// The splits group's LOAD verdict, both halves of it. "loadable": false is an
// OBLIGATION to refuse and its absence an obligation to load; as a permission
// ("no reader is required to load this") it let a row claim a refusal that never
// happened while every test stayed green, and it let a forbidden-character case
// be parked under splits where no load verdict would ever reach it.
void test_the_splits_load_verdict_is_executed_both_ways() {
    const YAML::Node& oracle = block_oracle();
    int loading = 0, refusing = 0;
    cfio::LoadOptions opts;
    opts.validate = false;
    for (const auto& c : oracle["splits"]) {
        const std::string name = c["name"].as<std::string>();
        const std::string doc = c["doc"].as<std::string>();
        const bool loadable = !c["loadable"] || c["loadable"].as<bool>();
        if (loadable) {
            ++loading;
            check_no_throw([&] { cfio::loads(doc, opts); },
                           name + ": no load flag, so it loads");
        } else {
            ++refusing;
            check_throws([&] { cfio::loads(doc, opts); },
                         name + ": flagged, so it is refused at load");
        }
    }
    check(loading > 0 && refusing > 0,
          "both halves of the splits load flag are exercised");

    // The way out of the line-break rule, and the control that tells a rule from
    // a blanket ban: the refusal is on the RAW bytes, so the escaped spelling
    // loads and carries the value.
    for (const auto& c : oracle["splits"]) {
        if (c["name"].as<std::string>() !=
            "escaped_line_break_in_a_double_quoted_scalar_is_ordinary_text") {
            continue;
        }
        const std::string doc = c["doc"].as<std::string>();
        check(doc.find("\xE2\x80\xA8") == std::string::npos,
              "the control carries no raw code point");
        cfio::ChipletDocument parsed;
        check_no_throw([&] { parsed = cfio::loads(doc); },
                       "the escaped spelling loads");
        check(parsed.assembly.name == "demo\xE2\x80\xA8x",
              "and carries U+2028 in the value");
    }
}

// CR is the conditional member, so it is measured from both sides on a real
// document rather than only through the refuse rows: a CRLF file is ordinary,
// and the same file with one LF taken out of a terminator is refused. Without
// this, "refuse every CR" would pass every other check in this file.
void test_crlf_survives_the_carriage_return_rule() {
    const YAML::Node& oracle = block_oracle();
    std::string crlf;
    for (const auto& c : oracle["splits"]) {
        if (c["name"].as<std::string>().find("crlf") != std::string::npos) {
            crlf = c["doc"].as<std::string>();
        }
    }
    check(!crlf.empty() && crlf.find("\r\n") != std::string::npos,
          "the oracle still carries a CRLF document");
    cfio::ChipletDocument parsed;
    check_no_throw([&] { parsed = cfio::loads(crlf); },
                   "a CRLF document loads");
    check(parsed.assembly.name == "demo",
          "and its values are untouched by the carriage-return rule");

    std::string broken = crlf;
    const std::size_t at = broken.find("\r\n");
    broken.erase(at + 1, 1);
    std::string message;
    try {
        cfio::loads(broken);
    } catch (const cfio::ChipletFormatError& e) {
        message = e.what();
    }
    check(message.find("U+000D") != std::string::npos,
          "the same document with one LF removed is refused, naming U+000D");
    check(message.find("line 1") != std::string::npos,
          "and naming the line the CR is on");
}

// The writer half of the same rule, and it needs doing rather than asserting:
// yaml-cpp writes NEL, U+2028 and U+2029 as RAW bytes in every style,
// double-quoted included (measured on 0.8.0), so a document a host built with
// one in a field would be written out as a file neither reference reader will
// open again. yaml-cpp 0.7 also emits CR raw, unlike the observed 0.8 behavior;
// the generated-mapping retry must provide the same round-trip guarantee on both.
//
// The subjects come off the ORACLE, so a fifth member of the set arrives here as
// a failing check rather than as a silently untested one.
void test_the_writer_escapes_what_the_reader_refuses() {
    const YAML::Node& oracle = block_oracle();
    std::set<std::string> code_points;
    for (const auto& c : oracle["refuse"]) {
        if (c["kind"].as<std::string>() != "forbidden_line_break") continue;
        code_points.insert(c["code_point"].as<std::string>());
    }
    check(code_points.size() >= 4, "the oracle names the whole set");
    for (const std::string& cp : code_points) {
        const std::string raw = utf8_of(cp);
        cfio::ChipletDocument doc;
        doc.format_version = cfio::SUPPORTED_FORMAT_VERSION;
        doc.assembly.name = "demo" + raw + "x";
        const std::string text = cfio::dumps(doc);
        check(text.find(raw) == std::string::npos,
              cp + ": no raw code point reaches the file");
        // The SPELLING of the escape is the emitter's, not the format's, and the
        // two references differ: PyYAML writes \N, \L and \P, and yaml-cpp
        // writes \x85 for NEL and \L and \P for the other two. Both name the
        // same character and both round-trip, and the header has always said the
        // two writers are semantically equivalent, not byte-identical. What is
        // asserted here is the property: a backslash escape, and the value back.
        check(text.find("\\") != std::string::npos,
              cp + ": it is written as an escape");
        cfio::ChipletDocument reloaded;
        check_no_throw([&] { reloaded = cfio::loads(text); },
                       cp + ": the file this writer produced loads");
        check(reloaded.assembly.name == doc.assembly.name,
              cp + ": and the value round-trips unchanged");
        // And the other writer rule is not paid for with this one: the pass that
        // escapes quotes every string, and the top-level keys go back to bare
        // before the bytes leave, or the file this writer produced could not be
        // split by the grammar it defines.
        check(text.rfind("format_version:", 0) == 0,
              cp + ": the top-level keys are still bare");
        check(text.find("\nassembly:") != std::string::npos,
              cp + ": every one of them, not just the first");
    }
    // The ordinary document is untouched by the rule: the retry that quotes
    // every string runs only when generated output contains a forbidden break.
    cfio::ChipletDocument plain;
    plain.format_version = cfio::SUPPORTED_FORMAT_VERSION;
    plain.assembly.name = "demo";
    check(cfio::dumps(plain).find("\"name\":") == std::string::npos,
          "a document without a forbidden line break keeps its nested keys bare");
}

void test_writer_cr_retry_preserves_fields_and_flow() {
    cfio::ChipletDocument doc;
    doc.format_version = cfio::SUPPORTED_FORMAT_VERSION;
    doc.assembly.name = "actual\rCR";
    doc.assembly.description = "actual\r\nCRLF versus literal \\r";
    cfio::Component component;
    component.id = "U1";
    component.type = "die";
    component.metadata = {{"key\rCR", "value\rCR"},
                          {"literal\\r", "literal\\r"}};
    doc.components.push_back(component);

    const std::string text = cfio::dumps(doc);
    check(text.find('\r') == std::string::npos,
          "generated mapping has no raw CR in keys or values");
    cfio::ChipletDocument reloaded;
    check_no_throw([&] { reloaded = cfio::loads(text); },
                   "CR-bearing generated fields load");
    check(reloaded.assembly.name == doc.assembly.name,
          "actual CR in assembly name round-trips");
    check(reloaded.assembly.description == doc.assembly.description,
          "scalar CRLF and literal backslash-r remain distinct");
    check(reloaded.components.size() == 1,
          "CR-bearing metadata retains its component");
    if (reloaded.components.size() == 1) {
        check(reloaded.components.front().metadata == component.metadata,
              "metadata keys and values preserve actual CR and literal escapes");
    }

    doc.has_flow = true;
    doc.flow_source = cfio::FlowSource::Slice;
    doc.flow_yaml = "flow:\r\n  # preserve source CRLF\r\n  steps: []\r\n";
    const std::string with_flow = cfio::dumps(doc);
    const std::size_t flow_at = with_flow.find("flow:\r\n");
    check(flow_at != std::string::npos,
          "quoted retry retains the CRLF flow key line");
    if (flow_at != std::string::npos) {
        check(with_flow.substr(flow_at) == doc.flow_yaml,
              "flow source remains byte-identical after generated CR retry");
        check(with_flow.substr(0, flow_at).find('\r') == std::string::npos,
              "only preserved flow source contains raw CRLF");
    }
    cfio::ChipletDocument with_flow_reloaded;
    check_no_throw([&] { with_flow_reloaded = cfio::loads(with_flow); },
                   "escaped generated CR and preserved source CRLF load together");
    check(with_flow_reloaded.flow_yaml == doc.flow_yaml,
          "reader retains the exact CRLF flow slice");
    check(with_flow_reloaded.assembly.description == doc.assembly.description,
          "generated CRLF scalar survives alongside CRLF flow source");
}

// A document assembled in memory, not read from a file, may still carry the
// pre-slice spelling: the flow VALUE with no key line. dumps() wraps that
// through the node tree so the output keeps a `flow:` key. Lossy, which is why
// the reader no longer produces it, but never silently dropped.
void test_hand_built_flow_value_without_a_key_line_still_emits() {
    cfio::ChipletDocument doc;
    doc.format_version = cfio::SUPPORTED_FORMAT_VERSION;
    doc.assembly.name = "a";
    doc.has_flow = true;
    doc.flow_yaml = "steps:\n  - name: export\n";
    cfio::ChipletDocument reloaded = cfio::loads(cfio::dumps(doc));
    check(reloaded.has_flow, "hand-built flow value survives dump/load");
    check(reloaded.flow_yaml.rfind("flow:", 0) == 0,
          "and comes back as a proper flow block, key line included");
}

// Validation rule 8: a pad's io_class must allow the type of the interface it
// takes part in, and the pad set of an endpoint is scoped by port_layer. The
// TABLE is compared with the spec and with the Python constant by
// conformance/test_pad_usage_compatibility.py; what only a run can show is the
// reader's behaviour, which is here. Documents are hand-built rather than read
// from the corpus: a fixture is a document, not the specification.
std::string pad_usage_doc(const std::string& io_class,
                          const std::string& pad_layer,
                          const std::string& iface_type,
                          const std::string& port_layer) {
    return "format_version: \"1.0\"\nassembly:\n  name: rule 8\n"
           "components:\n"
           "- id: interposer\n  type: interposer\n  io_pads:\n"
           "  - id: P1\n    io_class: " + io_class + "\n"
           "    position: {x: 0.0, y: 0.0}\n    layer: " + pad_layer + "\n"
           "- id: U1\n  type: die\n"
           "interfaces:\n- id: link0\n  type: " + iface_type + "\n"
           "  from: {component: U1, port_layer: " + port_layer + "}\n"
           "  to: {component: interposer, port_layer: " + port_layer + "}\n";
}

void test_pad_usage_rule_refuses_a_mismatched_pad() {
    check_throws([] {
        cfio::loads(pad_usage_doc("wire_bond", "TopMetal2", "copper_pillar",
                                  "TopMetal2"));
    }, "a wire_bond pad under a copper_pillar interface is refused");

    // The same pad and the same interface, one layer apart: the endpoint's pad
    // set is scoped by port_layer, so this one is not in it.
    check_no_throw([] {
        cfio::loads(pad_usage_doc("wire_bond", "Metal4", "copper_pillar",
                                  "TopMetal2"));
    }, "a mismatched pad on another layer is not in the pad set");

    // A row of the table, positive.
    check_no_throw([] {
        cfio::loads(pad_usage_doc("flipped_bump", "TopMetal2", "copper_pillar",
                                  "TopMetal2"));
    }, "a flipped_bump pad under a copper_pillar interface is accepted");

    // solder_bump is in the flipped_bump row, which is the 1.1 member
    // meeting the pad usage table.
    check_no_throw([] {
        std::string doc = pad_usage_doc("flipped_bump", "TopMetal2",
                                        "solder_bump", "TopMetal2");
        doc.replace(doc.find("\"1.0\""), 5, "\"1.1\"");  // its minor
        cfio::loads(doc);
    }, "a flipped_bump pad under a solder_bump interface is accepted");

    // An endpoint whose component carries no inline pads (every die endpoint)
    // is out of scope by decision, until interfaces get a pad binding.
    check_no_throw([] {
        cfio::loads("format_version: \"1.0\"\nassembly:\n  name: a\n"
                    "components:\n- id: U1\n  type: die\n"
                    "interfaces:\n- id: link0\n  type: copper_pillar\n"
                    "  from: {component: U1, port_layer: TopMetal2}\n");
    }, "a die endpoint carries no pads and is not checked");

    // The refusal names what a designer needs to act on.
    bool named = false;
    try {
        cfio::loads(pad_usage_doc("wire_bond", "TopMetal2", "copper_pillar",
                                  "TopMetal2"));
    } catch (const cfio::ChipletFormatError& e) {
        const std::string what = e.what();
        named = what.find("link0") != std::string::npos &&
                what.find("P1") != std::string::npos &&
                what.find("wire_bond") != std::string::npos &&
                what.find("copper_pillar") != std::string::npos &&
                what.find("rule 8") != std::string::npos;
    }
    check(named, "the rule 8 refusal names the interface, the pad, the class "
                 "and the type");

    // And the writer will not emit one either: validate() carries the rule, so a
    // document built in memory cannot be saved into the corpus.
    cfio::ChipletDocument doc;
    doc.format_version = cfio::SUPPORTED_FORMAT_VERSION;
    doc.assembly.name = "a";
    cfio::Component interposer;
    interposer.id = "interposer";
    interposer.type = "interposer";
    cfio::IOPad pad;
    pad.id = "P1";
    pad.io_class = "wire_bond";
    pad.layer = "TopMetal2";
    interposer.io_pads.push_back(pad);
    doc.components.push_back(interposer);
    cfio::Interface iface;
    iface.id = "link0";
    iface.type = "copper_pillar";
    cfio::InterfaceEndpoint to;
    to.component = "interposer";
    to.port_layer = "TopMetal2";
    iface.to = to;
    doc.interfaces.push_back(iface);
    check_throws([&] { cfio::dumps(doc); },
                 "the writer refuses to emit a rule 8 violation");
}

void test_interconnect_adapter_and_technology() {
    const std::string doc =
        "format_version: \"1.0\"\nassembly:\n  name: a\n"
        "interconnect:\n  adapter: vendorx\n"
        "  technology:\n    description: vendorx pillars\n    dbu: 0.001\n";
    cfio::ChipletDocument parsed = cfio::loads(doc);
    check(parsed.interconnect.has_value(), "interconnect parsed");
    check(parsed.interconnect->adapter == "vendorx", "adapter value");
    check(parsed.interconnect->technology.has_value(),
          "interconnect technology parsed");
    check(parsed.technology("vendorx") != nullptr,
          "interconnect technology is discoverable via lookup");
}

// technologies.<id>.stackup is the path to a stackup YAML a technology ships
// itself. This pins the emit as much as the parse: a consumer that re-saves a
// file whose technology declares one must not drop it, which is exactly what
// this reference did before the field was documented.
void test_technology_stackup_roundtrip() {
    const std::string doc =
        "format_version: \"1.0\"\nassembly:\n  name: a\n"
        "technologies:\n"
        "  intm4tm2:\n"
        "    description: interposer\n"
        "    layer_properties: ./tech/intm4tm2.lyp\n"
        "    stackup: ${INTERPOSER_PDK_ROOT}/libs.tech/chiplet_studio/intm4tm2.stackup.yaml\n"
        "    dbu: 0.001\n"
        "  sg13g2:\n"
        "    description: die\n"
        "    dbu: 0.001\n";
    cfio::ChipletDocument parsed = cfio::loads(doc);

    const cfio::Technology* interposer = parsed.technology("intm4tm2");
    check(interposer != nullptr, "technology intm4tm2 parsed");
    if (interposer) {
        // Verbatim, not resolved: the ${VAR} is the consumer's to expand.
        check(interposer->stackup ==
                  "${INTERPOSER_PDK_ROOT}/libs.tech/chiplet_studio/"
                  "intm4tm2.stackup.yaml",
              "stackup is kept verbatim");
    }

    // Absence stays absence, and must not become an emitted empty key.
    const cfio::Technology* die = parsed.technology("sg13g2");
    check(die != nullptr, "technology sg13g2 parsed");
    if (die) {
        check(die->stackup.empty(), "a technology without a stackup has none");
    }

    const std::string emitted = cfio::dumps(parsed);
    cfio::ChipletDocument reloaded = cfio::loads(emitted);
    const cfio::Technology* interposer2 = reloaded.technology("intm4tm2");
    check(interposer2 != nullptr && interposer2->stackup == interposer->stackup,
          "stackup survives dump/load");
    const cfio::Technology* die2 = reloaded.technology("sg13g2");
    check(die2 != nullptr && die2->stackup.empty(),
          "a technology without a stackup does not gain one on a round trip");
}

// True if any `#include` directive in `text` mentions `token`. Prose comments
// that merely name a library (e.g. documenting the dependency-clean intent) do
// not count -- only actual include directives.
bool has_include_of(const std::string& text, const std::string& token) {
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line)) {
        const std::size_t hash = line.find('#');
        if (hash == std::string::npos) continue;
        if (line.find("include", hash) == std::string::npos) continue;
        if (line.find(token, hash) != std::string::npos) return true;
    }
    return false;
}

void test_source_has_no_gpl_or_qt_dependency() {
    const std::string header = read_file(kHeaderFile);
    const std::string source = read_file(kSourceFile);

    // No GPL/Qt headers anywhere. (pcbnew is a Python module, included for
    // symmetry with the Python test's intent.)
    for (const std::string& banned : {std::string("klayout"), std::string("pcbnew"),
                                      std::string("Q")}) {
        check(!has_include_of(header, banned), "header includes no '" + banned + "' header");
        check(!has_include_of(source, banned), "source includes no '" + banned + "' header");
    }

    // The public header must stay dependency-clean: not even yaml-cpp leaks
    // through the API surface (it is confined to the .cpp).
    check(!has_include_of(header, "yaml"), "header does not include yaml-cpp");
}

const cfio::Component* find_component(const cfio::ChipletDocument& doc,
                                      const std::string& id) {
    for (const auto& c : doc.components) {
        if (c.id == id) return &c;
    }
    return nullptr;
}

bool near(double a, double b) { return (a > b ? a - b : b - a) < 1e-6; }

// attachment_surface_z is the interposer's die-mount plane, decoupled from the
// physical body thickness. This pins the parse AND the emit: without the writer
// change a consumer that re-saves the file would silently drop the mount
// reference (the fixed-point test above cannot catch that -- the field is gone
// on the first load, so d1 already lacks it).
void test_attachment_surface_z_roundtrip() {
    const std::string path = kExamplesDir + "/interposer_demo_design.chiplet";
    cfio::ChipletDocument doc = cfio::load(path);

    const cfio::Component* interposer = find_component(doc, "interposer");
    check(interposer != nullptr, "example has an interposer component");
    if (interposer) {
        check(interposer->attachment_surface_z.has_value(),
              "interposer carries attachment_surface_z");
        check(near(interposer->attachment_surface_z.value_or(0.0), 10.8103),
              "interposer attachment_surface_z is 10.8103");
        // thickness is now the physical body, decoupled from the mount plane.
        check(near(interposer->dimensions.thickness, 300.0),
              "interposer thickness is the physical body, not the mount ref");
    }

    // A die carries no attachment surface -> nullopt (consumers fall back to
    // thickness). Absence is representable, not coerced to 0.
    const cfio::Component* die = find_component(doc, "U1");
    check(die != nullptr, "example has die U1");
    if (die) {
        check(!die->attachment_surface_z.has_value(),
              "a die has no attachment_surface_z");
    }

    // The value survives dump->load: the writer must emit it.
    cfio::ChipletDocument reloaded = cfio::loads(cfio::dumps(doc));
    const cfio::Component* interposer2 = find_component(reloaded, "interposer");
    check(interposer2 != nullptr &&
              interposer2->attachment_surface_z.has_value() &&
              near(interposer2->attachment_surface_z.value(), 10.8103),
          "attachment_surface_z survives dump/load");
}

// --- H-B: tolerant format_version policy (parity with the Python matrix) ---

// The since-minor rule, run over the shared oracle with each row's own map, so
// the rule is measured while kMemberSince is still small. The Python twin is
// conformance/test_member_since.py; both are compared to the file, never to
// each other.
void test_member_since_matches_the_oracle() {
    const std::string path = kFixturesDir + "/member_since_cases.json";
    YAML::Node oracle;
    try {
        oracle = YAML::LoadFile(path);
    } catch (const YAML::Exception&) {
        ++g_checks;
        ++g_failures;
        std::cerr << "  FAIL: no member-since oracle at " << path << "\n";
        return;
    }
    int ran = 0;
    for (const auto& c : oracle["cases"]) {
        std::vector<std::string> storage;
        for (const auto& row : c["since"]) {
            for (const auto& cell : row) storage.push_back(cell.as<std::string>());
        }
        std::vector<cfio::MemberSince> since;
        for (std::size_t i = 0; i + 2 < storage.size(); i += 3) {
            since.push_back({storage[i].c_str(), storage[i + 1].c_str(),
                             storage[i + 2].c_str()});
        }
        const std::string name = c["name"].as<std::string>();
        const std::string declared = c["declared"].as<std::string>();
        const std::string field = c["field"].as<std::string>();
        const std::string member = c["member"].as<std::string>();
        const std::string verdict = c["verdict"].as<std::string>();
        ++ran;
        if (verdict == "refuse") {
            check_throws([&] {
                cfio::check_member_since(declared, field, member, since);
            }, "member-since oracle: " + name);
        } else {
            bool threw = false;
            try {
                cfio::check_member_since(declared, field, member, since);
            } catch (...) {
                threw = true;
            }
            check(!threw, "member-since oracle: " + name);
        }
    }
    check(ran >= 5, "the member-since oracle ran its rows");
}

void test_implemented_minor_reads_silently() {
    // 1.1 is the highest minor this reader implements, so it does not warn,
    // although the assembly stamp is still "1.0".
    const std::string text = "format_version: \"1.1\"\nassembly:\n  name: a\n";
    std::vector<std::string> sink;
    cfio::LoadOptions opts;
    opts.on_warn = [&sink](const std::string& m) { sink.push_back(m); };
    cfio::ChipletDocument doc = cfio::loads(text, opts);
    check(doc.format_version == "1.1", "the implemented minor is accepted");
    check(doc.warnings.empty() && sink.empty(),
          "the implemented minor reads without a warning");
}

void test_source_document_is_refused_whatever_the_options() {
    // A top-level `system` block makes a SOURCE document (format 1.1), which
    // this reader refuses before any option is consulted.
    const std::string text = read_file(kFixturesDir + "/v1_1_system_source.chiplet");
    check(!text.empty(), "the source fixture is readable");
    for (int mode = 0; mode < 4; ++mode) {
        cfio::LoadOptions opts;
        opts.allow_intermediate = (mode & 1) != 0;
        opts.validate = (mode & 2) == 0;
        ++g_checks;
        bool right = false;
        try {
            cfio::loads(text, opts);
        } catch (const cfio::SourceDocumentError& e) {
            right = std::string(e.what()).find("source .chiplet") !=
                    std::string::npos;
        } catch (...) {
            right = false;
        }
        if (!right) {
            ++g_failures;
            std::cerr << "  FAIL: source document not refused with "
                         "SourceDocumentError in mode " << mode << "\n";
        }
    }
}

void test_an_assembly_never_carries_unplaced() {
    // The root `unplaced` is a source's (format 1.3); refused under the
    // validate gate, intermediate files allowed or not.
    const std::string text = read_file(kFixturesDir + "/v1_3_unplaced_assembly.chiplet");
    check(!text.empty(), "the unplaced assembly fixture is readable");
    cfio::LoadOptions intermediate;
    intermediate.allow_intermediate = true;
    for (const cfio::LoadOptions& opts : {cfio::LoadOptions(), intermediate}) {
        ++g_checks;
        bool right = false;
        try {
            cfio::loads(text, opts);
        } catch (const cfio::ChipletFormatError& e) {
            right = std::string(e.what()).find("belongs to a source") !=
                    std::string::npos;
        }
        if (!right) {
            ++g_failures;
            std::cerr << "  FAIL: an assembly carrying unplaced is not refused\n";
        }
    }
    cfio::LoadOptions plain;
    plain.validate = false;
    check_no_throw([&] { cfio::loads(text, plain); },
                   "an assembly carrying unplaced loads unvalidated");
}

void test_higher_minor_warns_and_accepts() {
    const std::string text = "format_version: \"1.6\"\nassembly:\n  name: a\n";
    std::vector<std::string> sink;
    cfio::LoadOptions opts;
    opts.on_warn = [&sink](const std::string& m) { sink.push_back(m); };
    cfio::ChipletDocument doc = cfio::loads(text, opts);
    check(doc.format_version == "1.6",
          "same-major higher minor is accepted");
    check(!doc.warnings.empty(),
          "higher minor records a per-document warning");
    check(sink.size() == 1, "higher minor fires the on_warn sink exactly once");
}

void test_lower_and_higher_major_rejected() {
    check_throws([] {
        cfio::loads("format_version: \"2.0\"\nassembly:\n  name: a\n");
    }, "2.0 (higher major) rejected");
    check_throws([] {
        cfio::loads("format_version: \"0.9\"\nassembly:\n  name: a\n");
    }, "0.9 (lower major) rejected");
}

void test_malformed_version_rejected() {
    check_throws([] {
        cfio::loads("format_version: \"1\"\nassembly:\n  name: a\n");
    }, "\"1\" (no minor) rejected");
    check_throws([] {
        cfio::loads("format_version: \"1.0.0\"\nassembly:\n  name: a\n");
    }, "\"1.0.0\" (three parts) rejected");
}

void test_typed_writer_stamps_supported_for_higher_minor() {
    // The C++ struct writer is lossy (unknown top-level keys are not carried),
    // so a "1.6" input is written back as the supported "1.0" -- the OPPOSITE of
    // the Python passthrough writer, and the impl-class distinction the
    // conformance manifest encodes.
    const std::string text = "format_version: \"1.6\"\nassembly:\n  name: a\n";
    cfio::LoadOptions opts;
    opts.allow_intermediate = true;
    cfio::ChipletDocument doc = cfio::loads(text, opts);
    cfio::ChipletDocument reloaded = cfio::loads(cfio::dumps(doc));
    check(reloaded.format_version == cfio::SUPPORTED_FORMAT_VERSION,
          "typed/lossy writer stamps SUPPORTED for a higher-minor input");
}

// The reader RELEASE is declared and well formed. Its VALUE is compared with the
// Python reference in conformance/test_version_policy.py, the only place that
// sees both languages; here we pin that the constant exists, is a three-part
// MAJOR.MINOR.PATCH, and does not quietly disappear in a refactor.
void test_reader_release_is_declared() {
    const std::string release = cfio::READER_RELEASE;
    check(!release.empty(), "READER_RELEASE is not empty");
    std::istringstream parts(release);
    std::string part;
    int count = 0;
    bool numeric = true;
    while (std::getline(parts, part, '.')) {
        ++count;
        if (part.empty()) {
            numeric = false;
            continue;
        }
        for (char ch : part) {
            if (ch < '0' || ch > '9') {
                numeric = false;
            }
        }
    }
    check(count == 3, "READER_RELEASE is MAJOR.MINOR.PATCH");
    check(numeric, "READER_RELEASE components are numeric");
}

}  // namespace

// The version policy's transition window, driven by
// conformance/fixtures/version_policy_cases.json: the same rows the Python
// reference runs in conformance/test_version_policy.py, so the two references
// are measured against one file instead of against each other.
//
// What this cannot run, and says so rather than dropping it: a row whose
// declared value is not a JSON string. check_contract_version takes a
// std::string, so a bare number, a null or a list has already been resolved by
// the loader before this reader is called; those rows carry declared_kind and
// are Python-only. Every other row runs here.
void test_version_policy_oracle() {
    const YAML::Node& oracle = version_oracle();
    int ran = 0;
    int skipped = 0;
    for (const auto& c : oracle["cases"]) {
        const std::string name = c["name"].as<std::string>();
        const std::string kind = c["declared_kind"].as<std::string>();
        if (kind != "string") { ++skipped; continue; }
        const std::string declared = c["declared"].as<std::string>();
        const std::string verdict = c["verdict"].as<std::string>();
        std::vector<std::string> accepted;
        for (const auto& a : c["accepted"]) {
            accepted.push_back(a.as<std::string>());
        }
        ++ran;
        if (verdict == "call_error") {
            // A programming error in the SET: std::invalid_argument, and
            // deliberately NOT ChipletFormatError, so a consumer's typo never
            // reads as a bad artifact. check_throws would accept the wrong one.
            ++g_checks;
            bool right = false;
            try {
                cfio::check_contract_version(declared, accepted, "io_pads.json");
            } catch (const cfio::ChipletFormatError&) {
                right = false;
            } catch (const std::invalid_argument&) {
                right = true;
            } catch (...) {
                right = false;
            }
            if (!right) {
                ++g_failures;
                std::cerr << "  FAIL (expected a call-time error): " << name
                          << "\n";
            }
            continue;
        }
        if (verdict == "refuse") {
            ++g_checks;
            std::string message;
            bool threw = false;
            try {
                cfio::check_contract_version(declared, accepted, "io_pads.json");
            } catch (const cfio::ChipletFormatError& e) {
                threw = true;
                message = e.what();
            } catch (...) {
            }
            bool named = threw;
            if (c["names_majors"]) {
                for (const auto& m : c["names_majors"]) {
                    if (message.find(m.as<std::string>()) == std::string::npos) {
                        named = false;
                    }
                }
                for (const std::string& spelling : accepted) {
                    if (message.find(spelling) == std::string::npos) {
                        named = false;
                    }
                }
            }
            if (!named) {
                ++g_failures;
                std::cerr << "  FAIL (refusal): " << name << ": " << message
                          << "\n";
            }
            continue;
        }
        // accept and accept_warn: same value back, and the warning fires for
        // exactly one of the two.
        std::vector<std::string> warned;
        std::string got;
        try {
            got = cfio::check_contract_version(
                declared, accepted, "io_pads.json",
                [&warned](const std::string& m) { warned.push_back(m); });
        } catch (const std::exception& e) {
            ++g_checks;
            ++g_failures;
            std::cerr << "  FAIL (unexpected throw): " << name << ": "
                      << e.what() << "\n";
            continue;
        }
        check(got == c["normalized"].as<std::string>(),
              name + ": normalized version");
        check(warned.size() == (verdict == "accept_warn" ? 1u : 0u),
              name + ": the higher-minor event is reported exactly when it is one");
    }
    check(ran >= 20, "the version oracle still carries its rows");
    check(skipped > 0,
          "the Python-only rows are still in the oracle, not quietly dropped");
}

// The .chiplet entry point reads the accepted SET, not a private copy of one
// supported major. Its Python twin swaps the tuple to prove the same thing; here
// the constant is a compile-time array, so what is checked is the shape of the
// refusal it produces from that array.
void test_format_version_refusal_names_every_accepted_major() {
    check(cfio::ACCEPTED_FORMAT_VERSIONS.size() >= 1,
          "the reader accepts at least one major");
    std::string message;
    try {
        cfio::check_format_version("2.0");
    } catch (const cfio::ChipletFormatError& e) {
        message = e.what();
    }
    for (const char* entry : cfio::ACCEPTED_FORMAT_VERSIONS) {
        check(message.find(entry) != std::string::npos,
              std::string("the refusal names the accepted version ") + entry);
    }
    const bool one = cfio::ACCEPTED_FORMAT_VERSIONS.size() == 1;
    check(message.find(one ? "supports major " : "supports majors ")
              != std::string::npos,
          "the refusal names the accepted majors in the shared phrasing");
}

// Pins the hazard rather than a behaviour of ours, and says so. This reader
// decodes the escape \N to a BARE 0x85, which is not valid UTF-8 by itself, so a
// document that spells U+0085 that way arrives here as a malformed string while
// comparing equal to nothing in particular. Our own C++ writer never produces it
// (yaml-cpp escapes NEL as \x85 on its own), but PyYAML picks \N unprompted, so
// this check exists to keep the Python reference writer's override honest: if
// anyone removes it, the Python conformance test fails and this one explains
// why. It is a fact about yaml-cpp 0.8.0, and if a future yaml-cpp fixes it this
// check fails, which is the right prompt to revisit the Python override.
static void test_nel_escape_hazard_is_pinned() {
    cfio::LoadOptions opts;
    opts.validate = false;
    const cfio::ChipletDocument bad =
        cfio::loads("format_version: \"1.0\"\nassembly:\n  name: \"a\\Nb\"\n", opts);
    check(bad.assembly.name == std::string("a\x85" "b"),
          "this reader decodes \\N to a bare 0x85, which is why the writers do "
          "not use it");
    check(bad.assembly.name != std::string("a\xC2\x85" "b"),
          "and that is NOT the two bytes U+0085 encodes to");

    // The spelling both readers agree on, and the two that were always fine.
    const cfio::ChipletDocument good =
        cfio::loads("format_version: \"1.0\"\nassembly:\n  name: \"a\\x85b\"\n", opts);
    check(good.assembly.name == std::string("a\xC2\x85" "b"),
          "\\x85 decodes to U+0085 here, as it does in PyYAML");
    for (const char* pair : {"\\L", "\\P"}) {
        const std::string doc = std::string("format_version: \"1.0\"\nassembly:\n"
                                            "  name: \"a") + pair + "b\"\n";
        const cfio::ChipletDocument d = cfio::loads(doc, opts);
        check(d.assembly.name.size() == 5,
              "LS and PS decode to their three UTF-8 bytes, unchanged");
    }
}

int main() {
    std::cout << "chiplet_format_io C++ reference tests\n";

    // The grammar tests are driven by the shared oracle. If it is not where we
    // looked, say so and stop: several tests below would otherwise be skipped by
    // an exception escaping main, and a run that never opened the oracle must
    // never look like a run that agreed with it.
    const std::string oracle = oracle_path();
    if (!std::filesystem::exists(oracle)) {
        std::cerr << "  FAIL: top-level block grammar oracle not found at "
                  << oracle << "\n"
                  << "        (set CHIPLET_BLOCK_ORACLE to the path of "
                     "conformance/fixtures/top_level_blocks_cases.json)\n";
        std::cout << "1 checks, 1 failures\nFAILED\n";
        return 1;
    }

    const std::string version_cases = version_oracle_path();
    if (!std::filesystem::exists(version_cases)) {
        std::cerr << "  FAIL: version policy oracle not found at "
                  << version_cases << "\n"
                  << "        (set CHIPLET_VERSION_ORACLE to the path of "
                     "conformance/fixtures/version_policy_cases.json)\n";
        std::cout << "1 checks, 1 failures\nFAILED\n";
        return 1;
    }

    const std::string vocabulary_cases = vocabulary_oracle_path();
    if (!std::filesystem::exists(vocabulary_cases)) {
        std::cerr << "  FAIL: unknown-vocabulary oracle not found at "
                  << vocabulary_cases << "\n"
                  << "        (set CHIPLET_VOCABULARY_ORACLE to the path of "
                     "conformance/fixtures/unknown_vocabulary_cases.json)\n";
        std::cout << "1 checks, 1 failures\nFAILED\n";
        return 1;
    }

    test_roundtrip_canonical_example();
    test_all_example_chiplets_parse();
    test_missing_format_version_rejected();
    test_unsupported_version_rejected();
    test_assembly_name_required();
    test_component_requires_id_and_type();
    test_intermediate_refused_then_allowed();
    test_dump_roundtrips_components_order();
    test_attachment_surface_z_roundtrip();
    test_unknown_interface_type_is_carried_not_refused();
    test_unknown_vocabulary_is_carried_across_the_cross_product();
    test_every_known_interface_type_is_accepted();
    test_pad_usage_rule_refuses_a_mismatched_pad();
    test_flow_block_is_the_exact_source_slice();
    test_flow_block_is_re_emitted_byte_for_byte();
    test_the_writer_output_splits_into_exactly_the_keys_it_wrote();
    test_writer_refuses_extra_or_unattributable_flow_source();
    test_quoted_key_at_column_zero_loads_and_may_refuse_to_write();
    test_forbidden_line_breaks_are_refused_with_a_text_level_reason();
    test_crlf_survives_the_carriage_return_rule();
    test_the_splits_load_verdict_is_executed_both_ways();
    test_the_writer_escapes_what_the_reader_refuses();
    test_writer_cr_retry_preserves_fields_and_flow();
    test_flow_block_the_grammar_cannot_delimit_loads_but_does_not_write();
    test_split_top_level_keys_matches_the_oracle();
    test_hand_built_flow_value_without_a_key_line_still_emits();
    test_interconnect_adapter_and_technology();
    test_technology_stackup_roundtrip();
    test_member_since_matches_the_oracle();
    test_solder_bump_is_held_to_its_minor();
    test_units_are_closed_to_um();
    test_implemented_minor_reads_silently();
    test_source_document_is_refused_whatever_the_options();
    test_an_assembly_never_carries_unplaced();
    test_higher_minor_warns_and_accepts();
    test_lower_and_higher_major_rejected();
    test_malformed_version_rejected();
    test_typed_writer_stamps_supported_for_higher_minor();
    test_reader_release_is_declared();
    test_version_policy_oracle();
    test_format_version_refusal_names_every_accepted_major();
    test_source_has_no_gpl_or_qt_dependency();
    test_nel_escape_hazard_is_pinned();

    std::cout << g_checks << " checks, " << g_failures << " failures\n";
    if (g_failures == 0) {
        std::cout << "OK\n";
        return 0;
    }
    std::cout << "FAILED\n";
    return 1;
}
