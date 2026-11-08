// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 IHP GmbH
// Shared authored capture expectations, not a parser-generated slice oracle.
#include "chiplet_format_io/chiplet_format_io.hpp"
#include <yaml-cpp/yaml.h>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace cf = chiplet_format_io;
namespace {
int checks = 0, failures = 0;
void check(bool condition, const std::string& message) {
    ++checks;
    if (!condition) { ++failures; std::cerr << "FAIL: " << message << '\n'; }
}
std::string read(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("Missing required fixture: " + path);
    return std::string(std::istreambuf_iterator<char>(input), {});
}
std::string unhex(const std::string& text) {
    if (text.size() % 2) throw std::runtime_error("Odd fixture text_hex");
    const auto digit = [](char c) -> unsigned {
        if (c >= '0' && c <= '9') return unsigned(c - '0');
        if (c >= 'a' && c <= 'f') return unsigned(c - 'a' + 10);
        if (c >= 'A' && c <= 'F') return unsigned(c - 'A' + 10);
        throw std::runtime_error("Invalid fixture text_hex");
    };
    std::string bytes;
    for (std::size_t i = 0; i < text.size(); i += 2)
        bytes.push_back(char(16 * digit(text[i]) + digit(text[i+1])));
    return bytes;
}
void unsafe(const cf::TopLevelBlockCapture& result, const std::string& text,
            const std::string& label) {
    check(!result.safe, label + " refused");
    check(result.preamble.empty() && result.blocks.empty(), label + " no partial capture");
    check(!result.reason.empty() && !result.reason_code.empty(), label + " diagnostic");
    check(!result.byte_offset || *result.byte_offset <= text.size(), label + " bounded offset");
}
void shared_fixture() {
    const char* override_path = std::getenv("CHIPLET_CAPTURE_ORACLE");
    const std::string path = override_path ? override_path :
        std::string(CHIPLET_CAPTURE_FIXTURES) + "/root_capture_cases.json";
    const auto oracle = YAML::Load(read(path));
    check(oracle["version"].as<int>() == 1, "capture oracle version");
    check(oracle["cases"].IsSequence() && oracle["cases"].size() > 0, "nonempty capture oracle");
    std::set<std::string> names;
    std::size_t positive = 0, negative = 0;
    for (const auto& row : oracle["cases"]) {
        const auto name = row["name"].as<std::string>();
        check(names.insert(name).second, "unique fixture name " + name);
        check(bool(row["text"]) != bool(row["text_hex"]), name + " one input representation");
        const auto text = row["text_hex"] ? unhex(row["text_hex"].as<std::string>()) : row["text"].as<std::string>();
        const auto result = cf::capture_top_level_blocks(text);
        if (!row["safe"].as<bool>()) {
            ++negative;
            unsafe(result, text, name);
            if (result.byte_offset && row["error_byte_offset_if_present"])
                check(*result.byte_offset == row["error_byte_offset_if_present"].as<std::size_t>(),
                      name + " precise error offset when reported");
            continue;
        }
        ++positive;
        check(result.safe, name + " safe: " + result.reason);
        check(result.reason.empty() && result.reason_code.empty() && !result.byte_offset,
              name + " success has no diagnostic");
        const auto expected_preamble = row["preamble"].as<std::string>();
        check(result.preamble == expected_preamble, name + " authored preamble");
        check(row["blocks"].IsSequence() && row["blocks"].size() > 0, name + " authored blocks present");
        check(result.blocks.size() == row["blocks"].size(), name + " exact block count");
        std::size_t offset = expected_preamble.size();
        std::string authored = expected_preamble, captured = result.preamble;
        for (std::size_t i = 0; i < row["blocks"].size(); ++i) {
            const auto expected = row["blocks"][i];
            const auto raw = expected["raw"].as<std::string>();
            const auto end = offset + raw.size();
            if (expected["byte_start"]) check(expected["byte_start"].as<std::size_t>() == offset, name + " explicit expected start");
            if (expected["byte_end"]) check(expected["byte_end"].as<std::size_t>() == end, name + " explicit expected end");
            if (i < result.blocks.size()) {
                const auto& block = result.blocks[i];
                check(block.key == expected["key"].as<std::string>(), name + " authored key");
                check(block.raw == raw, name + " authored slice");
                check(block.byte_start == offset && block.byte_end == end, name + " exact UTF-8 byte range");
                check(text.substr(offset, end-offset) == block.raw, name + " original bytes");
                captured += block.raw;
            }
            offset = end; authored += raw;
        }
        check(authored == text, name + " oracle reconstructs original");
        check(captured == text, name + " capture reconstructs original");
        if (row["load_refuses"] && row["load_refuses"].as<bool>()) {
            bool refused = false;
            try { (void)cf::loads(text); } catch (const cf::ChipletFormatError&) { refused = true; }
            check(refused, name + " existing load policy still refuses");
        }
    }
    check(positive > 0 && negative > 0, "both fixture verdicts exercised");
}

void independent_controls() {
    const std::vector<std::pair<std::string, std::size_t>> tab_preambles{
        {" \t\n # lead\na: 1\n", 1}, {"\t\na: 1\n", 0},
        {"  \t# lead\na: 1\n", 2}, {"# \xC3\xA9\n \t\na: 1\n", 6},
        {"# \xC3\xA9\r\n \t\r\na: 1\r\n", 7}};
    for (const auto& item : tab_preambles) {
        const auto result = cf::capture_top_level_blocks(item.first);
        unsafe(result, item.first, "pre-root indentation tab profile");
        check(result.reason_code == "preamble_tab", "explicit capture profile diagnostic");
        check(result.byte_offset && *result.byte_offset == item.second,
              "exact indentation tab byte offset");
    }
    // Removing parser attestation makes this lexical key list look safe.
    const std::string phantom = "outer: \"text\nphantom: still scalar\"\nactual: value\n";
    unsafe(cf::capture_top_level_blocks(phantom), phantom, "phantom parser boundary");
    for (const auto& text : std::vector<std::string>{
             "", "a: &local [1]\n", "a: ! value\n", "a: !!str value\n",
             "a: [1,\n", "a: 1\na: 2\n", "a: \"x\n---\ny\"\n"})
        unsafe(cf::capture_top_level_blocks(text), text, "independent unsupported control");
    const std::string multibyte = "# \xCF\x80\r\na: \"\xC3\xA9\"\r\nb: 2";
    const auto exact = cf::capture_top_level_blocks(multibyte);
    check(exact.safe && exact.blocks.size() == 2, "UTF-8/CRLF capture");
    if (exact.blocks.size() == 2) {
        check(exact.blocks[0].byte_start == 6 && exact.blocks[1].byte_start == 15,
              "independent literal byte positions after multibyte text");
    }
    for (const std::string text : {
             std::string("format_version: '9.0'\nassembly: {name: invalid}\n"),
             std::string("format_version: '1.0'\n_metadata: {finalize_required: true}\nassembly: {name: intermediate}\n")}) {
        check(cf::capture_top_level_blocks(text).safe, "capture is not format/lifecycle validation");
        bool refused = false;
        try { (void)cf::loads(text); } catch (const cf::ChipletFormatError&) { refused = true; }
        check(refused, "existing load guard remains authoritative");
    }
    const auto all = cf::capture_top_level_blocks(read(std::string(CHIPLET_CAPTURE_FIXTURES) + "/v1_0_all_blocks.chiplet"));
    std::vector<std::string> keys;
    for (const auto& block : all.blocks) keys.push_back(block.key);
    check(all.safe && keys == std::vector<std::string>{"format_version", "assembly", "interposer", "interconnect",
          "technologies", "connection_stacks", "components", "interfaces", "netlist", "flow"}, "actual all-blocks key inventory");
}
}  // namespace

int main() {
    try { shared_fixture(); independent_controls(); }
    catch (const std::exception& error) { check(false, std::string("Unexpected failure: ") + error.what()); }
    std::cout << checks << " checks, " << failures << " failures\n";
    return failures ? 1 : 0;
}
