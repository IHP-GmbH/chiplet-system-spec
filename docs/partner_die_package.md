<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 IHP GmbH
-->

# Partner die package

A die whose layout stays with its owner enters an assembly as a black box: its
pads, their nets and roles, its outline and thickness, and what its owner says
about it. The owner hands that over as one file, the partner package, and the
flow derives the ready-die files from it. The package is the black-box pad map
of [`schemas/blackbox_padmap.schema.json`](../schemas/blackbox_padmap.schema.json)
at version `"1.1"`. It is not a second pad-map format: 1.1 adds keys and moves
none, so every 1.0 pad map stays valid and every reader of 1.0 keeps reading it.

The two sections after the key table, on the writer and on what the flow says,
are informative: they describe the tool that consumes the package, which is
documented with its own code in the chiplet flow. The format is what this page
specifies.

## What a die owner states

The die seen pad side up, in its own frame, in um. `die.bbox_um` is the outline
in that frame, at any origin; nothing is shifted. A flip at assembly is the
assembly's convention, as for every ready die.

Ids of the owner's own for the process and the technology: a PDK id, the release
of it the die was designed with, and the die's technology id. Never a layer
number, a layer name or a rule value of that process. The package needs none,
and the files the flow derives carry only the pad vocabulary of
[`schemas/chiplet_pads.json`](../schemas/chiplet_pads.json).

Per pad: its name, unique across the package, its centre and size, its net, its
direction and its role. For a power or ground pad the net is its supply domain;
there is no separate domain key. A function that spans several pads, a rail, is
one `port` with an `ordinal` per pad; a signal port holds one pad.

The supply volts of every power and ground net, ground at 0 and power above 0,
and no net in both roles.

A signoff statement: what the owner checked on its own layout, with which tool
and with what result, and the digest of the layout it checked. `issued_by` is a
role or an organisation, never a person, because the record that carries the
statement travels with the run's evidence.

Words are the die contract's closed sets, spelled as below. Any other spelling
(`SIGNAL`, `inout`) is refused by name; nothing is guessed.

## What the package states

"Writer" says what the partner-die writer requires; the schema itself requires
only what 1.0 required, so a 1.1 key absent from a file is a refusal of the
writer, not of the schema. Values in the last column are placeholders.

| key | writer | type, rule | placeholder |
| --- | --- | --- | --- |
| `version` | required | `"1.1"` | `"1.1"` |
| `chiplet_name` | required | string; the die's name and the top cell of the derived layout | `"example_partner_die"` |
| `die.bbox_um` | required | `[x_min, y_min, x_max, y_max]`, positive extent | `[0, 0, 1500, 1500]` |
| `die.thickness_um` | required | number above 0 | `250` |
| `process.pdk` | required | non-empty string, the owner's own id | `"owner-pdk"` |
| `process.pdk_version` | required | non-empty string | `"owner-pdk-1.0"` |
| `process.technology` | required | non-empty string, the die's own technology id | `"owner-tech"` |
| `pads[].name` | required | non-empty string, unique across the package | `"PAD_IN"` |
| `pads[].x_um`, `pads[].y_um` | required | number, pad centre | `120`, `750` |
| `pads[].w_um`, `pads[].h_um` | required | number, pad size | `60`, `60` |
| `pads[].net` | required | non-empty string; a power or ground pad's net is its supply domain | `"data_in"` |
| `pads[].direction` | required | `input`, `output`, `bidir`, `analog`, `none` | `"input"` |
| `pads[].role` | required | `signal`, `power`, `ground`, `no_connect` | `"signal"` |
| `pads[].port`, `pads[].ordinal` | optional | string and integer from 0, both or neither; absent means the pad name and 0 | `"VDD"`, `1` |
| `supply_volts` | required | `{net: volts}`, exactly the power and ground nets; ground 0, power above 0 | `{"VDD": 1.8, "VSS": 0}` |
| `signoff.statement` | required | non-empty string | `"Physical checks run on the layout named below."` |
| `signoff.issued_by` | required | a role or an organisation, never a person | `"physical-verification"` |
| `signoff.issued_at` | required | ISO 8601, UTC | `"2026-01-15T10:00:00Z"` |
| `signoff.layout_sha256` | required | 64 lowercase hex digits; the layout the owner checked | 64 hex digits |
| `signoff.checks[].check` | required | `drc`, `lvs`, `other` | `"drc"` |
| `signoff.checks[].tool` | required | non-empty string | `"owner-drc"` |
| `signoff.checks[].result` | required | `pass`, `fail`, `unresolved` | `"pass"` |
| `signoff.checks[].run_at` | required | ISO 8601, UTC | `"2026-01-15T09:00:00Z"` |
| `cdxml_ref` | optional | `{mpn, opn, version, uri, sha256}` | see below |
| `3dblox_ref` | optional | `{chiplet, uri, sha256}` | see below |
| `_provenance` | optional | object, free | `{"generator": "hand-written"}` |

In both references `sha256` is 64 lowercase hex digits. A `uri` that names a
file beside the package is a path relative to the directory holding the package,
with forward slashes, no absolute path or drive, no `..`, no `~`, no variable and
no link that leads out of that directory; the writer checks that file's digest.
A URL is carried as data and not checked.

A complete package with the placeholders above:

```json
{
  "version": "1.1",
  "chiplet_name": "example_partner_die",
  "die": {"bbox_um": [0, 0, 1500, 1500], "thickness_um": 250},
  "process": {"pdk": "owner-pdk", "pdk_version": "owner-pdk-1.0", "technology": "owner-tech"},
  "pads": [
    {"name": "PAD_IN", "x_um": 120, "y_um": 750, "w_um": 60, "h_um": 60,
     "net": "data_in", "direction": "input", "role": "signal"},
    {"name": "PAD_OUT", "x_um": 1380, "y_um": 750, "w_um": 60, "h_um": 60,
     "net": "data_out", "direction": "output", "role": "signal"},
    {"name": "PAD_VDD_A", "x_um": 600, "y_um": 1380, "w_um": 60, "h_um": 60,
     "net": "VDD", "direction": "bidir", "role": "power", "port": "VDD", "ordinal": 0},
    {"name": "PAD_VDD_B", "x_um": 900, "y_um": 1380, "w_um": 60, "h_um": 60,
     "net": "VDD", "direction": "bidir", "role": "power", "port": "VDD", "ordinal": 1},
    {"name": "PAD_VSS", "x_um": 750, "y_um": 120, "w_um": 60, "h_um": 60,
     "net": "VSS", "direction": "bidir", "role": "ground"}
  ],
  "supply_volts": {"VDD": 1.8, "VSS": 0},
  "signoff": {
    "statement": "Physical checks run on the layout named below.",
    "issued_by": "physical-verification",
    "issued_at": "2026-01-15T10:00:00Z",
    "layout_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "checks": [
      {"check": "drc", "tool": "owner-drc", "result": "pass", "run_at": "2026-01-15T09:00:00Z"},
      {"check": "lvs", "tool": "owner-lvs", "result": "unresolved", "run_at": "2026-01-15T09:30:00Z"}
    ]
  },
  "_provenance": {"generator": "hand-written"}
}
```

## From the package to a ready die

Informative. The chiplet flow turns the package into a ready die, the four-file
die folder every die enters an assembly as, with the command

```
chiplet-die-flow emit-partner-die --package <file> --output <dir> [--report <file>]
```

The writer checks every rule above, including those that span keys, and writes
the four ready-die files into `<dir>`: `layout.gds`, a pads-only layout drawn on
the pad vocabulary by the black-box generator; `pins.json`; `die-contract.json`;
and `build-record.json`, which carries the process, the die outline and
thickness, the package's digest and file name, the supply volts, the signoff
and both references, verbatim. A refusal names the key and the pad and leaves no
directory behind. `--report` writes the command, the package digest, the digest
of each file written, which pad vocabulary was read, and the refusal if there
was one, so a caller reads the result without parsing standard output.

## What the flow then says about the die

Informative. The flow never says more about the die than its package says.

- The die's internals stay unverified: the flow records them as `not_claimed`,
  and every verdict that includes the die says so.
- The signoff is recorded, never judged. It is not an attestation: nothing binds
  the owner's deck or evidence by digest, and the digest in `layout_sha256` is
  never compared with the derived layout.
- The die is built elsewhere as far as assembly admission is concerned, so
  admitting it still requires an admission statement (reason, scope, what is not
  verified, the responsible role), as for every black box.
- `die.thickness_um` is the owner's declaration of the die's thickness, the
  value an assembly's own statement for the die is held against.
- The supply volts reach the die contract's supply domains. Whether they match
  the rail the assembly feeds them from is a separate check.

## Mapping against CDXML, 3Dblox and LEF

Informative. The package keeps its own key names; this table says, key by key,
which field of three neighbouring descriptions means the same thing in the same
unit, so that a converter from any of them knows where to take each value and
what to refuse. No converter is provided in this repository, and the package is
not claimed to be aligned with any of the three. "No counterpart" means the
meaning or the unit differs.

Sources, public only. CDXML: `cdxml.xsd` at opencomputeproject/ODSA-CDXML
commit `5a725e4`; line numbers are that file's. 3Dblox: what OpenROAD's reader
takes, `src/odb/src/3dblox/dbvParser.cpp` (`.3dbv`) and
`src/odb/src/3dblox/bmapParser.cpp` (`.bmap`) at The-OpenROAD-Project/OpenROAD
commit `2c56926971`; a statement that 3Dblox has or lacks something is a
statement about that reader. LEF 5.8: as OpenROAD's parser reads it,
`src/odb/src/lef/lef/lef.y` at the same OpenROAD commit.

| key (1.1), unit | CDXML | 3Dblox | LEF 5.8 | for a converter |
| --- | --- | --- | --- | --- |
| `chiplet_name` | `cdxml/id` :72 (`mpn` :73 is the part number) | the `ChipletDef` key | `MACRO` | a free name |
| `die.bbox_um` width and height, um | `mech/width` :102, `mech/length` :113, integers with an optional free-text `unit`, typ/min/max | `design_area`, um | `SIZE`, LEF units | take `typ`; refuse a value without a unit or not in um |
| `die.thickness_um`, um | `mech/thickness` :124, same shape | `thickness`, um | no counterpart | as above |
| `pads[].name`, unique per pad | `io/pin/pnum` :191 | `.bmap` column 1, `bumpInstName` | `PIN` | the per-pad id |
| `pads[].port` | `io/pin/pname` :192 | `.bmap` column 5, `portName`, repeats across bumps | no counterpart | the function name; several pads may share it |
| `pads[].x_um`, `pads[].y_um`, um, centre | `io/pin/position/{x,y}` :256-260, integers, no fixed unit | `.bmap` columns 3 and 4, `x y`, um, centre | from `PORT` geometry | refuse a value without a unit or not in um |
| `pads[].w_um`, `pads[].h_um`, um | `io/pin/diameter` :237, no fixed unit, a circle | from the LEF master of the bump cell (`.bmap` column 2) | `RECT` | a diameter becomes a square of that side, declared as such |
| `pads[].net` | `io/pin/netlist_name` :196 | `.bmap` column 6, `netName` (`-` is none) | no counterpart | `-` refuses: a net is required |
| `pads[].direction`, closed set | `io/pin/dir` :194, a free string | no counterpart | `DIRECTION` | refuse any string outside the set; no guess |
| `pads[].role`, closed set | `io/pin/sig_type` :193, a free string | no counterpart | `USE` | refuse any string outside the set; no guess |
| `supply_volts`, V | no counterpart (`v_max` :207 is an absolute maximum per pin; `v_modes` :313 are names) | no counterpart | no counterpart | from the die owner only |
| `signoff` | no counterpart (`checklist` `Status` :343 tracks deliverables; `authors` :80 are people) | no counterpart | no counterpart | from the die owner only |
| `cdxml_ref`, `3dblox_ref` | `Includes/Include/{File, Version}` :29-36, no hash | file paths, no hash | no counterpart | the converter computes `sha256` |

No counterpart either way: CDXML `vdd_pin` and `gnd_pin` (:197-198, the reference
supply of a signal pin, not a domain) and `mech_type` (:195); 3Dblox
`bumpCellType` (a LEF master name), `pmap` (parsed and ignored), a region's
`side` and an instance's `orient` (facts of the assembly, not of the die). Per-pad
maximum voltage, bump type and min/max envelopes are not carried, because nothing
in the flow reads them; a converter declares their loss.
