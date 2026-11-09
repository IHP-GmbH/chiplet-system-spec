<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 IHP GmbH
-->
# chiplet-system-description

A typed Python eDSL for chiplet-on-interposer system descriptions. You author a
`System` (dies, a silicon interposer, typed D2D links, power/ground ties) and it
elaborates to the `.chiplet` document the interposer place-and-route flow already
consumes. It is an authoring surface that compiles down to `.chiplet`, not a new
format and not a new engine.

Its checks run at authoring time, before any physical step: interface identity,
version, lane count and direction at `connect()`, and pin binding, bump geometry
and the interconnect method's pitch at `build()`. None of them is visible in the
flattened `.chiplet`, which is why they live here. A source `system.chiplet` is
read, checked (`chiplet-system-description check`) and written by
`chiplet_system_description.source`, and an edited system schematic comes back
through the schematic import. From format 1.3 a source may be unplaced (dies
with no `at`, a carrier with no `size`, external entries waiting in the root
`unplaced.pins`): it reads, `check` reports each unplaced thing as a note, the
elaborator refuses it until `packaging-flow place` fills it in, and
`source.patch` writes the placement back byte for byte.

## Layout

- `src/chiplet_system_description/` -- the frozen `System` model, the `SystemBuilder` facade,
  the typed error hierarchy, and elaboration to `.chiplet`.
- `src/chiplet_system_description/connectivity.py`, `schematic_import.py` -- the schematic
  import: an edited system schematic's connectivity form, written by a
  tool's front end, read against the design it was drawn from, or refused by
  name. `packaging-flow edit <run> --sheet <form>` writes the result into the
  run's `system.chiplet`. Form 1.1 adds the blank sheet: one a person drew
  from the die symbol library against a given source with no die-to-die
  wiring, which the import reads by reference designator and part. Form 1.2
  adds the classic sheet, drawn with no given source. Each symbol may carry
  `key` (`/` plus its uuid, the root sheet left out), `value` and `footprint`
  verbatim (`~` stays `~`) and `fields`, every symbol field as a raw string.
  The four are read only at 1.2. A classic sheet requires them. At 1.0 and
  1.1, `key`, `value` and `footprint` are ignored and `fields` is malformed.
  At 1.2 a blank or generated symbol carries all four or none. The import takes `Die`,
  `Method`, `Thickness_um` and `Edge` out of `fields`. An `EXT_PAD` is
  `J1`, `J2`, ... The `chiplet_system` power symbols give the rails with
  their class and volts, and no-connect flags give the pins left open. A
  net name is the netlist's own text, escaped forms and a one-pin
  `unconnected-(...)` net included; nothing here unescapes one. The form
  checks only their shape; the import judges them. Read again against the
  form that produced the current source, a symbol whose `key` changed
  reference is `reference_changed`. A port whose pads share one name, left
  open, is `open_shared_pads`. The contract's pads and ports are checked
  against the inventory (`pad_numbers`, the pad sets compared directly, then
  the port a pad serves). A difference is `contract_pads_differ`, and it names
  the first differing pairs. Symbol pins are the inventory's pad names. A die
  whose inventory has a pad on no port, or states no port, is `unported_pads`.
  `sheet_net_names` maps each net the planner names
  onto one sheet net. A net that leaves the carrier needs exactly one `EXT_PAD`
  on its sheet net, and a link or an alone pad needs none. A split, a merge, a
  missing rail, a J on a link, an alone pad drawn to a J, or two J's on a tie
  is `not_one_to_one`. A sheet net whose die pins are not all claimed is
  `unclaimed_pins`. A ref that is neither a die nor an `EXT_PAD` is
  `foreign_ref`. A planner refusal is `not_expressible` and returns no partial
  map. Refusals are collected together; `unwritable_net_name` does not hide one.
  The form has no schema file of its own; its
  shape is this paragraph and `connectivity.py`. `classic_import.py`
  reads a classic sheet
  into its project file (the source holding the carrier, registry, external
  profile, fill keep-out and flow): `read_classic_sheet` checks the form
  against the die folders its `Die` fields name and returns the system, and
  `classic_edits` (which `schematic_edits` calls for a classic form) gives the
  patch edits that write its dies, links, rails, open lines and external
  entries, an `EXT_PAD` with no `Edge` waiting in `unplaced.pins`. A re-import
  keeps each die's placement while its folder is the same, and a bus the file
  writes while the sheet still draws it; the same sheet read again writes
  nothing.
- `src/chiplet_system_description/source/compare.py` -- `compare_nets`: two sources
  compared as the systems they elaborate to, one coded difference per connection that
  differs, ports named as the dies serve them. `scope="connections"` compares only
  what connects (nets, rails with their volts and routing requirement, external pad
  counts, die identity), so an unplaced classic file compares with a placed reference.
- `src/chiplet_system_description/interposer_reference.py` -- the interposer LVS reference
  netlist written from a `.chiplet`: one subckt named after the carrier's top
  cell, one port per net that lands on the carrier, and the design's SHA-256 in
  its header. `python -m chiplet_system_description.interposer_reference --help`.
- `src/chiplet_system_description/_vendor/chiplet_format_io/` -- a byte-identical copy of the
  shared reference reader/writer module; the single `.chiplet` read/write path.
  It is imported relatively through `src/chiplet_system_description/_cfio.py` under this
  package's own name, never placed on `sys.path` and never registered as
  `chiplet_format_io`, which stays the installed reference's name. The
  reference's `die_contract.py` and `pad_numbers.py` are vendored with it:
  symbol pin numbers have one placement, and the classic import calls it.
  Schema admission is not called, because this tree has no schema. The
  reference's modules for what a die flow and its readers share
  (`agreement`, `eda_views`, `pins`, `ready_die_names`, `transistor_view`,
  `verilog_closure`) are not vendored; nothing here reads them.
  `technology_package`, the technology package validator, is not vendored
  either. `technology` is vendored: the system joins a die's PDK to its technology
  through it. `sheet_names` is vendored: both sheet imports read a net name
  with the rule the generator writes it with. The identity test pins those
  exclusions, so a module the reference adds is a decision here, never a
  silent gap.
- `examples/two_die_ihp.py` -- the canonical system. Its dies mount on the
  interposer PDK's `beol.attachment_surface_z_um`, read under
  `INTERPOSER_PDK_ROOT`; the tests use a byte copy of that descriptor
  (`tests/fixtures/interposer_pdk`).
- `tests/` -- golden-equivalence, the non-redundancy control, route parity
  (strict-live), and the vendored/fixture identity checks.

## Running

From `description/` in the chiplet-system-spec repository:

```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"                # the line a runner uses
pip install -e ../../interposer-pnr     # optional sibling: without it its cases skip
chiplet-system-description check path/to/system.chiplet   # tiers 0 and 1 over a source
INTERPOSER_PDK_ROOT=../../interposer python examples/two_die_ihp.py --out build/two_die_ihp.chiplet
python -m pytest -q                     # Tier 1: hermetic, starts no tool
HCDP_STRICT_LIVE=1 python -m pytest -m live_openroad   # the live cases alone
HCDP_STRICT_LIVE=1 scripts/checkpoint.sh   # Tier 2: strict-live route parity
```

The spec is the repository this package lives in: the tests read the reference
reader and the conformance cases from `..`, through no variable, and never skip
them. The siblings (`interconnect_pdk`, `interposer-pnr`) are read where their
own route points first: `INTERCONNECT_PDK_ROOT` and the checkout an importable
`interposer_pnr` comes from. A set variable is the sibling and never falls back.
Without one, a sibling is read under `HCDP_ROOT` when it is set, and beside this
repository otherwise. They are optional at run
time: the cases that read one carry `needs_sibling_sources` and skip when it is
absent. Under `HCDP_CI=1` nothing is looked for beside this repository, and a case
whose sibling neither route finds fails. A run with no sibling selects
`-m "not live_openroad and not needs_sibling_sources"`.

## License

Apache-2.0. SPDX headers are present in every file.
