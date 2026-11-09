# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The Verilog closure reader over the shapes PDK models and LibreLane netlists
have, and the constructs it refuses rather than skips."""
import pytest

from chiplet_format_io.verilog_closure import (UNIT, VerilogClosureError, active_text, closure, is_name,
                                               split_statements, strip, units)

UDP = """\
`timescale 1ns/10ps
`ifdef _udp_def_mux_
`else
`define _udp_def_mux_
primitive mux_udp (q, a, b, s);
\toutput q;
\tinput a, b, s;
\ttable
\t\t0 ? 0 : 0;
\t\t1 ? 0 : 1;
\tendtable
endprimitive
`endif
"""
CELLS = """\
// module fake_in_a_comment (a); ghost g0 (a); endmodule
`timescale 1ns/10ps
`celldefine
module mux2 (X, A0, A1, S);
\toutput X;
\tinput A0, A1, S;
\twire w;
\tmux_udp (w, A0, A1, S);
\tbuf (X, w);
\tspecify
\t\t(A0 => X) = (0.0, 0.0);
\tendspecify
endmodule
`endcelldefine
module spare (Y);
\toutput Y;
`ifdef SPARE_BEHAVIOUR
\tinitial $display("never read");
`else
\tassign Y = 1'b0;
`endif
endmodule
"""
NETLIST = """\
(* top = 1 *)
module top (a, b, s, y);
  input a, b, s;
  output y;
  wire \\esc[0] ;
  mux2 #(.P(1)) \\u.mux[0] (.X(\\esc[0] ), .A0(a), .A1(b), .S(s));
  buf_tree u1 (.A(\\esc[0] ), .Y(y));
  assign y2 = "a string with module x ; endmodule";
endmodule
"""


def test_units_read_models_and_netlists_and_the_closure_walks_them():
    defined = units([("udp.v", UDP), ("cells.v", CELLS), ("top.v", NETLIST)])
    assert defined == [{"mux_udp": frozenset()},
                       {"mux2": frozenset({"mux_udp"}), "spare": frozenset()},
                       {"top": frozenset({"mux2", "buf_tree"})}]
    assert closure("top", defined) == ([0, 1, 2], {"buf_tree": "top"})
    assert closure("mux2", defined) == ([0, 1], {})


def test_a_define_carries_into_the_next_source():
    """The second copy of the primitive is guarded out by the first, as one
    compile of both files would read them."""
    defined = units([("udp.v", UDP), ("udp_again.v", UDP)])
    assert defined == [{"mux_udp": frozenset()}, {}]


@pytest.mark.parametrize("text, reason", [
    ('`include "cells.v"\nmodule m (a);\n input a;\nendmodule\n', "`include is a directive or macro"),
    ("module m (a);\n input a;\n `WIDTH w0 (a);\nendmodule\n", "`WIDTH is a directive or macro"),
    ("module m (a);\n input a;\n always @(a) $display(a);\nendmodule\n", "holds always, which"),
    ("module m (a);\n input a;\n initial $readmemh(\"f\", mem);\nendmodule\n", "holds initial, which"),
    ("module m (a);\n input a;\n generate\n endgenerate\nendmodule\n", "generate endgenerate"),
    ("module m (a);\n input a;\n defparam u.P = 1;\nendmodule\n", "holds defparam, which"),
    ("module m (a, y);\n input a;\n output y;\n always_comb y = a;\nendmodule\n", "holds always_comb, which"),
    ("wire stray;\nmodule m (a);\n input a;\nendmodule\n", "text outside a module"),
    ("module m (a);\n input a;\nendmodule\nwire stray;\n", "text outside a module"),
    ("`ifdef X\nmodule m (a);\n input a;\nendmodule\n", "an `ifdef is not closed"),
    ("`endif\n", "`endif without `ifdef"),
    ("`define LONG a \\\n b\n", "runs over several lines"),
    ("module m (a);\n input a;\nendprimitive\n", "a module with no name or no endmodule"),
    ("module m (a);\n input a\nendmodule\n", "text with no closing ';'"),
    ("module m (a);\nendmodule\nmodule m (b);\nendmodule\n", "m is defined twice"),
])
def test_refuses_what_it_does_not_read(text, reason):
    with pytest.raises(VerilogClosureError, match=reason):
        units([("bad.v", text)])


def test_a_reached_name_two_sources_define_refuses():
    defined = units([("cells.v", CELLS), ("cells_again.v", CELLS.replace("`timescale 1ns/10ps\n", ""))])
    with pytest.raises(VerilogClosureError, match="mux2 is defined by 2 sources"):
        closure("mux2", defined)
    # Unreached, the duplicate does not matter.
    assert closure("nothing", defined) == ([], {"nothing": "nothing"})


def test_two_needed_sources_defining_one_name_refuse_even_unreached():
    """A compile reads whole files, so a name both needed sources define is a
    clash whether or not the design reaches it."""
    spare_again = ("module spare (Y);\n output Y;\nendmodule\n"
                   "module top (a);\n input a;\n mux2 u0 (a);\nendmodule\n")
    defined = units([("udp.v", UDP), ("cells.v", CELLS), ("top.v", spare_again)])
    with pytest.raises(VerilogClosureError, match="spare is defined by 2 sources: cells.v, top.v"):
        closure("top", defined, ["udp.v", "cells.v", "top.v"])


@pytest.mark.parametrize("text, defined", [
    # elsif picks the first true branch; the later one is skipped.
    ("`define B\n`ifdef A\nmodule a (x);\n input x;\nendmodule\n`elsif B\nmodule b (x);\n input x;\nendmodule\n"
     "`else\nmodule c (x);\n input x;\nendmodule\n`endif\n", {"b"}),
    ("`ifndef A\nmodule n (x);\n input x;\nendmodule\n`endif\n", {"n"}),
    ("`define A\n`undef A\n`ifdef A\nmodule a (x);\n input x;\nendmodule\n`endif\n", set()),
    # A define inside an inactive branch does not take effect, and nesting holds.
    ("`ifdef NO\n`define A\n`ifdef A\nmodule a (x);\n input x;\nendmodule\n`endif\n`endif\n"
     "`ifdef A\nmodule b (x);\n input x;\nendmodule\n`endif\n", set()),
    # `undef ends after its name and a `timescale may share a line with another directive.
    ("`define A\n`undef A module u (x);\n input x;\nendmodule\n", {"u"}),
    ("`timescale 1ns/1ps `ifdef A\nmodule a (x);\n input x;\nendmodule\n`endif\n"
     "module t (x);\n input x;\nendmodule\n", {"t"}),
])
def test_conditionals_and_line_directives(text, defined):
    assert set(units([("m.v", text)])[0]) == defined


def test_an_escaped_name_may_hold_comment_and_quote_characters():
    netlist = 'module top (a);\n input a;\n wire \\u//nc ;\n cell \\u"q (.A(\\u//nc ));\nendmodule\n'
    assert units([("top.v", netlist)]) == [{"top": frozenset({"cell"})}]


def test_a_comment_separates_the_tokens_around_it():
    netlist = "module top (a);\n input a;\n cell/*c*/u0 (.A(a));\nendmodule\n"
    assert units([("top.v", netlist)]) == [{"top": frozenset({"cell"})}]


def test_the_tokenizer_reads_as_units_does():
    """Another reader of the same netlists splits them with the same names."""
    text = strip('`define A\n`ifdef A\nmodule m (a); // gone\n cell u0 (.A(a), .S("x;y"));\nendmodule\n`endif\n')
    body = UNIT.search(active_text(text, set(), "m.v")).group(2)
    assert [statement[:2] for statement in split_statements(body, "m.v")] == [["m", "("], ["cell", "u0"]]
    assert is_name("u0") and is_name("\\u.mux[0]") and not is_name("0u") and not is_name("(")
