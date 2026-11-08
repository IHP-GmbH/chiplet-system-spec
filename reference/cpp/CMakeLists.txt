# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
#
# Build for the chiplet_format_io C++ reference reader/writer. The only
# dependency is yaml-cpp (MIT). No Qt, no KLayout, no pcbnew.

cmake_minimum_required(VERSION 3.16)
# VERSION is the reader release, kept equal to READER_RELEASE in the header and
# to chiplet_format_io.__version__ on the Python side.
project(chiplet_format_io LANGUAGES CXX VERSION 1.21.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(yaml-cpp REQUIRED)

# yaml-cpp 0.8 exports the target yaml-cpp::yaml-cpp; older packages export the
# bare `yaml-cpp` target or only the YAML_CPP_LIBRARIES/INCLUDE_DIR variables.
if(TARGET yaml-cpp::yaml-cpp)
  set(_yamlcpp_target yaml-cpp::yaml-cpp)
elseif(TARGET yaml-cpp)
  set(_yamlcpp_target yaml-cpp)
else()
  set(_yamlcpp_target ${YAML_CPP_LIBRARIES})
endif()

add_library(chiplet_format_io STATIC src/chiplet_format_io.cpp)
target_include_directories(chiplet_format_io
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(chiplet_format_io PUBLIC ${_yamlcpp_target})
if(DEFINED YAML_CPP_INCLUDE_DIR)
  target_include_directories(chiplet_format_io PRIVATE ${YAML_CPP_INCLUDE_DIR})
endif()

# Tests are on by default; turn off with -DCHIPLET_FORMAT_IO_BUILD_TESTS=OFF.
option(CHIPLET_FORMAT_IO_BUILD_TESTS "Build chiplet_format_io tests" ON)
if(CHIPLET_FORMAT_IO_BUILD_TESTS)
  enable_testing()
  add_executable(test_chiplet_format_io tests/test_chiplet_format_io.cpp)
  target_link_libraries(test_chiplet_format_io PRIVATE chiplet_format_io)
  # The repository root, derived from THIS source directory rather than from
  # CMAKE_SOURCE_DIR, so the paths below still point at this repository when
  # reference/cpp is pulled into a superproject with add_subdirectory().
  get_filename_component(_chiplet_spec_root "${CMAKE_CURRENT_SOURCE_DIR}/../.."
                         ABSOLUTE)
  target_compile_definitions(test_chiplet_format_io PRIVATE
    CHIPLET_EXAMPLES_DIR="${_chiplet_spec_root}/examples"
    CHIPLET_HEADER_FILE="${CMAKE_CURRENT_SOURCE_DIR}/include/chiplet_format_io/chiplet_format_io.hpp"
    CHIPLET_SOURCE_FILE="${CMAKE_CURRENT_SOURCE_DIR}/src/chiplet_format_io.cpp"
    # The shared top-level block grammar oracle. The Python reference and the
    # KiCad plugin's merge splitter run this same file; three implementations of
    # one grammar are compared to it, never to each other. This is the DEFAULT
    # path; the CHIPLET_BLOCK_ORACLE environment variable overrides it at run
    # time, so a binary built in one checkout can be measured against another
    # checkout's oracle. A missing oracle fails the run and names the path it
    # looked for: green over tests that never ran is worse than red.
    CHIPLET_BLOCK_ORACLE_DEFAULT="${_chiplet_spec_root}/conformance/fixtures/top_level_blocks_cases.json"
    # The version policy's verdict oracle, on the same terms (default here,
    # CHIPLET_VERSION_ORACLE overrides at run time, absence fails the run):
    # the Python reference runs the same rows in conformance/test_version_policy.py.
    CHIPLET_VERSION_ORACLE_DEFAULT="${_chiplet_spec_root}/conformance/fixtures/version_policy_cases.json"
    # The carry-rule oracle: the cross product of implementation,
    # LoadOptions::validate and load vs load-then-dump, over the same documents
    # conformance/test_unknown_vocabulary_roundtrip.py runs. Same terms again
    # (default here, CHIPLET_VOCABULARY_ORACLE overrides, absence fails the run),
    # plus the fixture directory, because these cells read whole documents rather
    # than inline strings: the interesting one is the document whose interface
    # meets an interposer pad, and inlining that would put a fixture in two files.
    CHIPLET_VOCABULARY_ORACLE_DEFAULT="${_chiplet_spec_root}/conformance/fixtures/unknown_vocabulary_cases.json"
    CHIPLET_FIXTURES_DIR="${_chiplet_spec_root}/conformance/fixtures")
  add_test(NAME chiplet_format_io COMMAND test_chiplet_format_io)
  add_executable(test_root_capture tests/test_root_capture.cpp)
  target_link_libraries(test_root_capture PRIVATE chiplet_format_io)
  target_compile_definitions(test_root_capture PRIVATE
    CHIPLET_CAPTURE_FIXTURES="${_chiplet_spec_root}/conformance/fixtures")
  add_test(NAME chiplet_root_capture COMMAND test_root_capture)
endif()
