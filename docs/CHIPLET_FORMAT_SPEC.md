<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 IHP GmbH

This specification is published under the Apache License 2.0. See README.md.
-->

# Chiplet File Format Specification

**Version:** 1.5
**File Extension:** `.chiplet`
**Format:** YAML

## Overview

The `.chiplet` format is a YAML-based specification for describing 3D chiplet package assemblies. It defines the complete structure of a multi-die assembly including component placement, technology definitions, and layout references.

## Scope and relationship to other standards

`.chiplet` is a **physical-assembly layout** format. It answers *where each die,
interposer, and substrate sits in one shared coordinate frame, how each die is
z-mounted onto its interconnect, and which GDS/OASIS body and layer properties
each component carries*; the inputs an assembly viewer and an assembly DRC flow
need. It is the interchange pivot between PCB-style design entry and a 3D
assembly/DRC environment.

`.chiplet` is deliberately **not** a chiplet IP datasheet or a marketplace /
sourcing format. It does **not** model a chiplet's electrical, functional,
power, thermal, PHY/D2D-protocol, or test characterization. Those belong to a
part-description standard, a different layer of the stack where standards
already exist:

- **CDXML** (Chiplet Data Exchange in XML), a per-chiplet, machine-readable
  datasheet: pinout, mechanical envelope, electrical/ESD ratings, and D2D
  interface type. It was developed in **OCP / ODSA** and published under
  CC0-1.0, and its capabilities were folded into **JEDEC's JEP30** PartModel,
  a separate JEDEC standard that is now the active vehicle for this part-model
  data. CDXML describes *what a chiplet is*; by its own scope it carries no
  inter-die placement (its only coordinates are pad positions *within* a single
  part).

One other format family describes multi-die physical assemblies - the same
layer as `.chiplet`, bound to a different abstraction level:

- **3Dblox**, originated by TSMC and being standardized as **IEEE P3537**,
  with an open-source (BSD-3) implementation in OpenROAD (ingestion, an
  automatic assembly linter, a 3D viewer). 3Dblox binds an assembly to the
  **P&R abstraction** (LEF/DEF, Verilog netlists, Liberty views) for
  multi-die EDA and design-space exploration; it references no artwork files.
  `.chiplet` binds the same assembly layer to the **mask level** (GDS/OASIS
  bodies, `.lyp` layer properties, per-layer interconnect metallurgy with
  method identity, fab DRC parameters) for assembly signoff and fabrication
  hand-off. The overlap on the assembly core (multi-die placement, z,
  thickness, flip, per-die technology) is real and is deliberate interop
  territory: see [`3dblox_interop.md`](./3dblox_interop.md) for a
  field-by-field mapping and the
  [`3dblox_ref`](#3dblox_ref-proposed-extension) proposed extension below.

The three are **stages of one flow**, not competitors:

| | Part description (CDXML -> JEP30) | Assembly for P&R / exploration (3Dblox -> P3537) | `.chiplet` |
|---|---|---|---|
| Answers | *what is this part* (datasheet) | *how dies stack and connect*, over P&R views | *placed where, z-mounted how, mask-level DRC-ready* |
| Coordinates | per-pin pad x/y within one die | per-die 3D placement + bump maps over LEF geometry | per-component placement in a shared interposer frame; bodies as GDS/OASIS |
| Stage | part selection / sourcing | multi-die EDA / design-space exploration | physical assembly + fabrication signoff DRC |

A natural pipeline is: select a part described by CDXML / JEP30 -> place and
z-mount it in a `.chiplet` assembly -> run assembly DRC, with a 3Dblox view of
the same assembly derivable (via the mapping in the interop appendix) for
P&R-level exploration and linting. To make
these handoffs explicit, a `.chiplet` component may carry an optional
[`cdxml_ref`](#cdxml_ref-proposed-extension) citing the part it instantiates,
and an optional [`3dblox_ref`](#3dblox_ref-proposed-extension) citing the
3Dblox `ChipletDef` that describes the same die at the P&R level. The
canonical source of truth for each layer stays where it belongs: part data in
CDXML / JEP30, P&R views in LEF/DEF/3Dblox, mask-level placement in
`.chiplet` - a derived `.3dbx` assembly file is an export artifact, never a
second source of truth for placement (see the interop appendix).

## File Structure

A `.chiplet` file always carries `format_version` and an `assembly` block; every
other top-level block is optional. The format defines thirteen top-level keys in
total: the reference reader recognizes twelve, and a source reader the
thirteenth, `unplaced` (format 1.3). The full list, with required/optional
status, is the [Root Level Keys](#root-level-keys) table below. The most common skeleton is:

```yaml
format_version: "1.0"   # required

assembly:               # required
  # Assembly metadata

technologies:           # optional
  # Technology definitions

components:              # optional
  # Component list
```

---

## Root Level Keys

| Key | Required | Type | Description |
|-----|----------|------|-------------|
| `format_version` | **YES** | String | Quoted `"MAJOR.MINOR"`; an assembly is written `"1.0"`, a source the lowest minor its content needs (`"1.1"` to `"1.3"`). Readers are tolerant of a same-major higher minor (warn), reject a different major. See the validation rules below. |
| `assembly` | **YES** | Object | Assembly metadata |
| `technologies` | NO | Map | Technology definitions |
| `connection_stacks` | NO | Map | Named interconnect stacks (see [Connection Stacks](#connection-stacks)) |
| `components` | NO | Array | List of components |
| `interposer` | NO | Object | Interposer-axis adapter: which interposer PDK rule set the assembly is verified against (see [Interposer](#interposer)) |
| `interconnect` | NO | Object | Interconnect-axis adapter and optional technology (see [Interconnect](#interconnect)) |
| `interfaces` | NO | Array | Typed die-to-die / bond interfaces (see [Interfaces](#interfaces)) |
| `netlist` | NO | Object | Optional assembly netlist (see [Netlist](#netlist)) |
| `flow` | NO | Object | Optional, opaque build/flow block (see [Flow](#flow)) |
| `system` | NO | Object | Format 1.1. Makes the document a SOURCE, not an assembly (see [System](#system-format-11)) |
| `_metadata` | NO | Object | Intermediate-file marker (see [Intermediate files](#intermediate-files-_metadata)) |
| `unplaced` | NO | Object | Format 1.3, sources only, written after `system`: the external entries no edge carries yet (see [Format 1.3: the unplaced source](#format-13-the-unplaced-source)) |

Key order is not significant: readers are key-driven and accept the sections in
any order. (The reference writer emits them in the order `format_version`,
`_metadata`, `assembly`, `technologies`, `interconnect`, `connection_stacks`,
`components`, `interfaces`, `netlist`, `flow`; the KiCad exporter emits
`interposer` right after `assembly`, before `interconnect`.)

### Machine-readable schema

[`schemas/chiplet.schema.json`](../schemas/chiplet.schema.json) is the
machine-readable form of this document and is **normative for structure**: which
root keys exist (the table above, with `additionalProperties: false`, so an
undeclared root key is a schema error), which block each key holds, the key set
and types of every block this document defines by key list, and the closed
vocabularies (component `anchor`, component `orientation`, `interfaces[].type`,
`io_pads[].io_class`, the adapter-id pattern).

A closed vocabulary binds **writers**. A producer emits a member of the list and
nothing else, and the schema is where that is enforceable. It does not bind the
reference readers, which carry every enum-like field as the string the document
wrote and report an unrecognised member on their warn channel: a reader that
refuses the DOCUMENT over an unrecognised member turns every future addition to
one of these lists into a MAJOR for everyone downstream, when the policy makes it
a MINOR precisely because a consumer can refuse the ELEMENT that carries it and
end up incomplete rather than wrong (see [`VERSION_POLICY.md`](./VERSION_POLICY.md),
"What bumps what"). So the schema is stricter than the readers here on purpose,
and the readers export their vocabulary
(`chiplet_format_io.KNOWN_INTERFACE_TYPES`, the C++ `kKnownInterfaceTypes`) so a
consumer has the list to refuse an element against.

A member added to a closed vocabulary after 1.0 is tagged with the minor that
added it, in an `x-since` object beside the enum (`{"member": "1.1"}`); a member
with no tag is since 1.0, so the tag appears only from the first member added by
a later minor. JSON Schema validators ignore the keyword, so the schema alone
does not refuse a member below its minor; the reference validators do (rule 1),
reading the same map, which they export as `MEMBER_SINCE` (Python) and
`kMemberSince` (C++).

The **reference reader stays normative for semantics**: the tolerant
`format_version` policy, the `_metadata.finalize_required` refusal, the absent
`anchor` default-and-warn, the `1e5` um leak guard, and every cross-reference
check. A document can therefore be schema-valid and still be refused by a reader
(for example a different major, which is a policy question, not a structural
one), and in documented cases the reader is deliberately the more tolerant of the
two: it carries an undeclared root key additively, it coerces an unquoted numeric
`format_version` through `str()` for back-compat, and it carries an
`interfaces[].type` outside the closed list. Every such case is pinned as a
fixture in `conformance/`, where a *new* disagreement between the schema and the
reader fails the gate.

---

## Assembly Section

The `assembly` section contains metadata about the chiplet package.

### Fields

| Key | Required | Type | Default | Description |
|-----|----------|------|---------|-------------|
| `name` | **YES** | String | - | Display name of the assembly |
| `description` | NO | String | `""` | Long-form description |
| `author` | NO | String | `""` | Author/designer name |
| `created` | NO | String | `""` | Creation date (ISO 8601) |
| `modified` | NO | String | `""` | Last modification date (ISO 8601) |
| `units` | NO | String | `"um"` | Unit of every length in the document. Closed to `um` (format 1.1), which is also the normative default when the key is absent. A reader MUST refuse any other value: it does not convert, and a length read in the wrong unit is wrong, not incomplete. |
| `assembly_gds` | NO | String | `""` | Path to a merged/flattened GDS of the whole assembly, when one is produced |
| `io_technology` | NO | String | `""` | Technology id used for assembly-level I/O pads |

### Example

```yaml
assembly:
  name: "HBM3 Package Assembly"
  description: "High-bandwidth memory stack with logic die"
  author: "Design Team"
  created: "2024-01-15"
  modified: "2024-02-20"
  units: "um"
```

---

## Technologies Section

The `technologies` section defines fabrication processes used by components. Each technology is identified by a unique ID (the map key).

### Fields

| Key | Required | Type | Default | Description |
|-----|----------|------|---------|-------------|
| `description` | NO | String | `""` | Technology description |
| `layer_properties` | NO | String | `""` | Path to KLayout `.lyp` file |
| `stackup` | NO | String | `""` | Path to a layer-stackup YAML this technology ships |
| `dbu` | NO | Float | `0.001` | Database unit in micrometers |

### Example

```yaml
technologies:
  tsmc_n5:
    description: "TSMC 5nm FinFET"
    layer_properties: "./tech/tsmc_n5.lyp"
    dbu: 0.001

  interposer_65nm:
    description: "65nm Silicon Interposer"
    layer_properties: "./tech/interposer.lyp"
    stackup: "${INTERPOSER_PDK_ROOT}/libs.tech/chiplet_studio/intm4tm2.stackup.yaml"
    dbu: 0.001

  organic_substrate:
    description: "Organic substrate technology"
    dbu: 1.0
```

### Notes

- Technology IDs must be unique within the file
- `layer_properties` paths are resolved relative to the `.chiplet` file location
- `stackup` is resolved through the same chain as `layer_properties`: `${VAR}`
  expansion first, then an absolute path is taken as-is and a relative path is
  taken relative to the `.chiplet` file location. A writer round-trips the
  verbatim string it read, not the resolved one
- `stackup` lets a technology the consumer has no built-in stackup for still
  render with a real one. Where a consumer has its own lookup keyed on the
  technology id, an explicit `stackup` takes priority over it
- `dbu` (database unit) defines coordinate resolution in the referenced layout files

---

## Components Section

The `components` section is an array of component definitions. Each component represents a physical element in the assembly.

### Common Fields

| Key | Required | Type | Default | Description |
|-----|----------|------|---------|-------------|
| `id` | **YES** | String | - | Unique component identifier |
| `type` | **YES** | String | - | Component type (see below) |
| `technology` | NO | String | `""` | Reference to a technology ID |
| `anchor` | NO* | String | `bbox_center` | How the component's mesh is centered: `gds_origin` or `bbox_center`. *Required for new files; readers warn and default to `bbox_center` when absent. See the [coordinate-frame contract](./coord_frame_contract.md), section 2. |
| `layout` | NO | String | `""` | Path to GDS/OASIS layout file |
| `top_cell` | NO | String | `""` | Top cell name in layout (single-cell form) |
| `cells` | NO | String or Array | - | One or more cell names in the layout. `top_cell` is the single-cell shorthand; writers emit `top_cell` for one cell and `cells` for several. |
| `position` | NO | Object | `{x:0, y:0, z:0}` | 3D position of the component in the canonical frame. WHICH local point of the component it places is decided by `anchor:`, not by this field: the GDS bbox centre for `bbox_center`, the cell's own GDS (0, 0) for `gds_origin` (see [Coordinate frame](#coordinate-frame-anchor-and-z-mounting-normative)). |
| `rotation` | NO | Object | `{z:0}` | Rotation angles |
| `orientation` | NO | String | `face_up` | Mounting orientation of a die: `face_up`, `flip_chip`, or `face_down`. Absent is treated as `face_up`. (`die` / `die_array`.) |
| `connection` | NO | String | `""` | Interconnect method id this die mounts on (e.g. `cupillar_opt1`); references an entry in the interconnect method registry (`interconnect_methods.json`). Drives per-die z-mounting (contract section 3). (`die` / `die_array`.) |
| `dimensions` | NO | Object | `{width:0, height:0, thickness:0}` | Physical size |
| `attachment_surface_z` | NO | Float | - | **Interposer only.** Z of the die-attachment (BEOL-top) surface in the component-local frame: the plane dies mount on (`z_die = interposer.position.z + attachment_surface_z + connection height`). Decouples the mount reference from `dimensions.thickness` (the physical body). Optional in the schema; an interposer that seats dies without it has no mount plane, and nothing stands in for it. See the [coordinate-frame contract](./coord_frame_contract.md), sections 1.5 and 3. |
| `io_pads` | NO | Array | `[]` | Assembly-level I/O pads (e.g. wire-bond pads), nested under the `interposer`. See [io_pads](#io_pads-interposer-only). |
| `metadata` | NO | Map | `{}` | Custom key-value pairs |
| `array` | NO | Object | - | Array config (die_array only) |

### Component Types

| Type | Description |
|------|-------------|
| `die` | Single integrated circuit die |
| `die_array` | Array of identical dies (e.g., HBM stack) |
| `interposer` | Silicon interposer connecting multiple dies |
| `substrate` | Package substrate or carrier |

These four are the canonical/recommended values. The reference reader requires
only a non-empty `type` string and does not reject other values (see Validation
Rules).

### Position Object

```yaml
position:
  x: <float>    # X-coordinate in micrometers
  y: <float>    # Y-coordinate in micrometers
  z: <float>    # Z-coordinate (height) in micrometers
```

### Rotation Object

```yaml
rotation:
  z: <float>    # Rotation around Z-axis in degrees (0-360)
```

### Dimensions Object

```yaml
dimensions:
  width: <float>      # X-extent in micrometers
  height: <float>     # Y-extent in micrometers
  thickness: <float>  # Z-extent in micrometers
```

`thickness` is the physical Z-extent of the component body itself.
For dies it excludes the interconnect below the body: bump and pillar
heights are modeled by the referenced `connection_stacks` entry, and
the body extends upward from `position.z` (the seating plane; see
`coord_frame_contract.md` section 1.4).

For an **interposer**, `thickness` is the physical substrate body
(Si + BEOL), and the die-mount plane is given separately by
[`attachment_surface_z`](#common-fields); the body extends *downward*
from that surface. `thickness` is never a mount plane: an interposer
that seats dies without `attachment_surface_z` has none (an exporter
refuses the file; a viewer may show the dies on `thickness` as a display
guess, with a warning). See `coord_frame_contract.md` sections 1.5 and 3.4.

### Array Configuration (for `die_array` type)

```yaml
array:
  pattern: <string>        # "grid", "linear", or "custom"
  count:
    x: <integer>           # Elements in X direction
    y: <integer>           # Elements in Y direction
  pitch:
    x: <float>             # X spacing in micrometers
    y: <float>             # Y spacing in micrometers
  start_position:
    x: <float>             # First element X position
    y: <float>             # First element Y position
    z: <float>             # First element Z position
```

`start_position` is a `position3d`, the same object as `position`, and `anchor:`
governs it the same way, because the anchor applies **per element**:
`start_position` places the FIRST element's anchor point, and every element of
the array is placed by that same anchor, `pitch.x` / `pitch.y` apart. A
`die_array` with `anchor: gds_origin` therefore has element (i, j)'s own GDS
(0, 0) at `start_position + (i * pitch.x, j * pitch.y)`; with `bbox_center` the
same holds of each element's bbox centre. There is one definition of `anchor`
and its effect does not depend on the component's type (see
[`coord_frame_contract.md`](./coord_frame_contract.md) section 2.1, which
governs).

### Coordinate frame, anchor, and z-mounting (normative)

The geometry fields above (`position`, `anchor`, `connection`, and the nested
`io_pads`) are governed by a single normative companion document:

> **[`coord_frame_contract.md`](./coord_frame_contract.md)** is the canonical
> source of truth for the `.chiplet` coordinate frame, the `anchor:` convention,
> and the per-die z-mounting rule. Every writer and reader MUST conform to it.
> This section only summarizes; where the two documents could appear to differ,
> the contract governs.

Summary of what the contract fixes:

- **Frame.** All `position:` x/y are expressed in the **GDS-bbox-corner of the
  interposer top_cell**, y-up cartesian, micrometers. The frame fixes the
  ORIGIN; it does not fix which point of the component sits at `position:`.
  That is `anchor:`, immediately below: a `bbox_center` component has its GDS
  bbox centre placed there, and a `gds_origin` component has its own GDS (0, 0)
  placed there with no extra centering. Reading `position:` as the geometric
  centre unconditionally puts every `gds_origin` component half its own extent
  away from where the contract puts it.
- **`anchor:`** declares how a component's local mesh is centered:
  `gds_origin` (mesh built around the component's own GDS (0,0); used by dies)
  or `bbox_center` (mesh centered on the component's GDS bbox; used by
  interposers). New files MUST declare it; readers default to `bbox_center`
  and warn when it is absent. It applies PER ELEMENT, so on a `die_array` it
  governs `array.start_position` (the first element's anchor point) and every
  element after it, and its meaning never depends on the component's type.
- **`connection:`** selects a die's interconnect method (an id in
  `interconnect_methods.json`). Z-mounting is per-die:
  `z_die = interposer.position.z + mounting_surface + connection.total_height()`, so one assembly can
  mix methods. A die with no `connection:` mounts on the file's plane with
  connection height 0 (contract section 3.4).
- **Leak guard.** A reader MUST warn (or reject) when `|position.x|` or
  `|position.y|` exceeds `1e5` um, a heuristic that absolute (un-converted)
  coordinates have leaked through.

### orientation (die / die_array)

Optional string describing how a die is mounted. The reference reader treats it
as a canonical-string field with three recognized values: `face_up` (the
default), `flip_chip`, and `face_down`. An absent `orientation` is treated as
`face_up` downstream, and the reference C++ writer suppresses `face_up` on
output (it emits `orientation` only when the value is non-empty and not
`face_up`). The reader does not reject other strings, but writers should stay
within this vocabulary.

`orientation` records the assembly intent; the geometric effect of mirroring is
realized by the writer when it emits the layout (e.g. `gds_to_kicad
--flip-chip` mirrors X).

### io_pads (interposer only)

Assembly-level I/O pads (for example wire-bond pads) are listed under the
`interposer` component as `io_pads:`. Each pad is a point in the **same
canonical frame** as components (geometric center, um); pads do not declare
their own `anchor`. See the contract, section 6.

```yaml
io_pads:
  - id: J1                 # pad reference
    io_class: wire_bond    # die-side usage class: wire_bond | flipped_bump | tsv_bump
    net: VDD               # net name (free-form)
    position: {x: 100.0, y: 100.0}
    size: {x: 80.0, y: 80.0}   # optional 2D extent
    layer: TopMetal2       # layer the pad sits on
```

`io_class` is the die-side USAGE class of the pad, how it is attached, and it is
a **closed** vocabulary: `wire_bond`, `flipped_bump`, `tsv_bump`. It was
free-form until 2026-09-05, and every governed emitter already wrote nothing
else, so closing it took no producer's legal output away. The closure is the
SCHEMA's, and only the schema's: both reference readers accept an unknown
`io_class` string today (measured, not assumed) and rule 8 skips a pad whose
class has no row, while a consumer that maps the field onto an enum refuses the
document. The value of a closed vocabulary is exactly that: outside the three,
the field has no meaning any consumer in the flow can act on. It is not the interposer-side pad GEOMETRY class; that axis lives in the PDK
(`pad_classes`) and is joined to a usage class through the interconnect method,
never by this field's name. Adding a usage class is a MINOR bump that consumers
adopt before any producer emits it. `size` is optional; the reference C++ writer
emits it for every pad on output.

#### Usage class and interface type (normative)

A pad's usage class and the type of the interface it takes part in are two
closed vocabularies about one physical joint, so not every pairing exists. The
table is normative and is what [validation rule 8](#validation-rules) checks:

| `io_class` (usage) | allowed `interfaces[].type` |
|---|---|
| `wire_bond` | `wire_bond` |
| `flipped_bump` | `micro_bump`, `copper_pillar`, `solder_bump` |
| `tsv_bump` | `tsv` |

It relates USAGE to interface type and nothing else. The interposer-side pad
GEOMETRY class stays where it was, in the PDK (`pad_classes`), joined to a usage
class through the interconnect method; a row here is not a licence to skip that
join.

### cdxml_ref (proposed extension)

> **Not part of the v1.0 schema.** `cdxml_ref` is a *proposed, optional*
> extension, documented here so the interop hook is well-defined; it is not one
> of the component fields the reference libraries model.

A `die` / `die_array` may cite the part it instantiates in a part-description
standard (CDXML / JEDEC JEP30) instead of duplicating that part's IP/datasheet
data here (see
[Scope and relationship to other standards](#scope-and-relationship-to-other-standards)).
The field is **optional and additive**: readers that do not understand it MUST
ignore it; its presence never changes placement, the coordinate frame, or
z-mounting.

```yaml
cdxml_ref:
  mpn: "ACME-PHY-0001"               # manufacturer part number (illustrative)
  opn: "ACME-PHY-0001-R"             # ordering part number (optional)
  version: "1.0"                     # part-document version (optional)
  uri: "./parts/acme_phy.cdxml"      # path or URL to the part document (optional)
  sha256: "<hex>"                    # content hash for provenance (optional)
```

At least one of `mpn` or `uri` SHOULD be present so the reference resolves. All
keys are strings.

Reference-library behavior today: the Python reference reader preserves the
field (it keeps the full mapping); the C++ struct reader does not round-trip it.
Until first-class support lands, treat `cdxml_ref` as opt-in metadata and do not
rely on the C++ reference reader to preserve it across a load/dump cycle.

### 3dblox_ref (proposed extension)

> **Not part of the v1.0 schema.** Like
> [`cdxml_ref`](#cdxml_ref-proposed-extension), `3dblox_ref` is a *proposed,
> optional* extension, documented here so the interop hook is well-defined; it
> is not one of the component fields the reference libraries model.

A `die` / `die_array` may cite the **3Dblox `ChipletDef`** that describes the
same physical die at the P&R abstraction level (see
[Scope and relationship to other standards](#scope-and-relationship-to-other-standards)),
so a mask-level assembly and a P&R-level view of the same die stay linked
without duplicating either. The field is **optional and additive**: readers
that do not understand it MUST ignore it; its presence never changes placement,
the coordinate frame, or z-mounting.

```yaml
3dblox_ref:
  chiplet: "cpu_die"              # ChipletDef name inside the .3dbv (recommended)
  uri: "./views/cpu_die.3dbv"     # path or URL to the .3dbv file (optional)
  sha256: "<hex>"                 # content hash for provenance (optional)
```

At least one of `chiplet` or `uri` SHOULD be present so the reference resolves.
All keys are strings.

The reference is deliberately **component-level only** (a die cites the
`ChipletDef` in a `.3dbv`). An *assembly*-level reference - to a `.3dbx`,
which re-states placements and connections for the whole assembly - is **not**
proposed: two machine-readable placement sources for one assembly would create
a source-of-truth ambiguity that the part- and die-level references do not
have. A project that keeps a derived `.3dbx` export alongside a `.chiplet` may
record the export step in the opaque [`flow`](#flow) block; the export is a
build artifact, and the `.chiplet` file remains authoritative for placement
and z-mounting. [`3dblox_interop.md`](./3dblox_interop.md) defines the mapping
such an export follows.

Reference-library behavior today: identical to `cdxml_ref` - the Python
reference reader preserves the field; the C++ struct reader does not
round-trip it. Treat `3dblox_ref` as opt-in metadata.

### Intermediate files (`_metadata`)

A writer that cannot yet emit the canonical frame (notably KiCad's GUI export,
which does not read the interposer GDS) emits an **intermediate** file marked
with a top-level `_metadata` block:

```yaml
_metadata:
  frame: pcb-bbox-corner
  finalize_required: true
  finalizer: hyp_to_gds.py --update-chiplet-file
```

A reader MUST refuse to load a file with `_metadata.finalize_required: true`
unless explicitly allowed; the finalizer converts positions into the canonical
frame and strips the block. Canonical `.chiplet` files carry no `_metadata`
block. See the contract, section 4.

A source document (one with a top-level [`system`](#system-format-11) block)
carries the same marker for a different reason: its finalizer does not re-frame
an assembly, it derives one. It is written `finalize_required: true`, a
`finalizer` naming the elaborating tool, and no `frame` key, because every
position in a source is in the canonical frame already.

### Component Example

```yaml
components:
  - id: substrate
    type: substrate
    technology: organic_substrate
    dimensions:
      width: 50000
      height: 50000
      thickness: 1000
    position:
      x: 0
      y: 0
      z: 0

  - id: interposer
    type: interposer
    technology: interposer_65nm
    layout: "./layouts/interposer.gds"
    top_cell: "INTERPOSER_TOP"
    dimensions:
      width: 40000
      height: 40000
      thickness: 100
    position:
      x: 5000
      y: 5000
      z: 1000

  - id: logic_die
    type: die
    technology: tsmc_n5
    layout: "./layouts/logic.gds"
    top_cell: "LOGIC_TOP"
    dimensions:
      width: 10000
      height: 10000
      thickness: 50
    position:
      x: 15000
      y: 15000
      z: 1100
    rotation:
      z: 0
    metadata:
      vendor: "TSMC"
      part_number: "ABC-123"

  - id: hbm_stack
    type: die_array
    technology: tsmc_n5
    layout: "./layouts/hbm_die.gds"
    top_cell: "HBM_DIE"
    array:
      pattern: grid
      count:
        x: 2
        y: 2
      pitch:
        x: 6000
        y: 6000
      start_position:
        x: 25000
        y: 15000
        z: 1100
```

---

## Connection Stacks

Optional top-level map of named interconnect stacks. Each entry describes the
physical layers a die's bump/pillar stack is built from; a die selects one by id
through its `connection:` field. In the reference toolchain the same stacks are
also published in the `interconnect_methods.json` sidecar (the single source of
truth for the interconnect PDK), and a `.chiplet` may inline the resolved stacks
here for self-containment.

```yaml
connection_stacks:
  cupillar_opt1:
    description: "PacTech Cu pillar, 35 um opening"
    layers:
      - {name: CuPillar, material: Cu,   height: 28.0, diameter: 44.0}
      - {name: SnAgCap,  material: SnAg, height: 16.0, diameter: 44.0}
```

| Key | Required | Type | Description |
|-----|----------|------|-------------|
| `description` | NO | String | Human-readable description |
| `layers` | NO | Array | Ordered stack layers, bottom (interposer side) first |
| `layers[].name` | - | String | Layer name (matched against the interposer stackup for z-mounting) |
| `layers[].material` | - | String | Material, e.g. `Cu`, `SnAg`, `SAC305` |
| `layers[].height` | - | Float (um) | Layer height; the stack total drives z-mounting (contract section 3) |
| `layers[].diameter` | - | Float (um) | Body diameter |

## Interposer

Optional top-level block declaring the **interposer axis** for the assembly:
which interposer PDK rule set the assembly is verified against. It carries a
single required key, `adapter`.

```yaml
interposer:
  adapter: intm4tm2                # interposer adapter id (required when present)
```

| Key | Required | Type | Description |
|-----|----------|------|-------------|
| `adapter` | **YES** | String | Interposer adapter id, e.g. `intm4tm2` |

`adapter` is a **registry id that the consuming ADK resolves against its own
adapter registry; it is never a filesystem path**. It must match
`^[A-Za-z0-9_][A-Za-z0-9_.-]*(?![\s\S])` (so it carries no path separator and no leading
dot) and must not end in `.drc`. An id that is really a path, or a rule-deck
filename, ties a portable assembly document to one machine's directory layout;
a document that travels names *what* it needs, not *where* that thing sits on
the author's disk.

The block is written by the KiCad exporter (from the `INTERPOSER_ADAPTER`
project text variable, defaulting to `intm4tm2`), and read by the ADK assembly
DRC runner and by the assembly cockpit.

**A consumer that needs an adapter and does not find this key refuses**; it does
not fall back to a built-in default. A silently defaulted rule set means an
assembly is signed off against rules nobody chose, and the failure is invisible
in the output. Consumers that only *report* the axis (a viewer, a project
browser) may of course treat it as absent.

`interposer` and [`interconnect`](#interconnect) are two independent axes: the
first says which interposer rule set applies, the second which interconnect
(bump/pillar) rule set. Neither implies the other, and an assembly may declare
one, both, or neither.

## Interconnect

Optional top-level block declaring the interconnect axis for the assembly. It is
recognized **only when it carries an `adapter`** (a block without `adapter` is
ignored). The optional `technology` gives the interconnect a PDK-backed identity
(same shape as a `technologies` entry); it is **registered under the adapter id**,
so it resolves through the same technology lookup as any other technology.

```yaml
interconnect:
  adapter: vendorx                 # interconnect adapter id (required when present)
  technology:                      # optional, PDK-backed identity (id = adapter)
    description: "VendorX microbump"
    layer_properties: ./tech/vendorx.lyp
    dbu: 0.001
```

`adapter` follows the same rule as [`interposer.adapter`](#interposer): a
registry id the consumer resolves, matching `^[A-Za-z0-9_][A-Za-z0-9_.-]*(?![\s\S])` and
not ending in `.drc`, never a filesystem path.

## Interfaces

Optional top-level list of typed die-to-die / bond interfaces in the assembly.
Each interface has a required `id` and a required, non-empty `type`, drawn from
the closed vocabulary below.

**Known `type` values:** `micro_bump`, `copper_pillar`, `tsv`, `wire_bond`,
`solder_bump` (the C4-class reflowed solder ball, the interconnect manifest's
`sbump_sac305`). `solder_bump` is format 1.1 (`x-since` in the schema): a
document carrying it is stamped at least `"1.1"`, and one stamped `"1.0"` is out
of contract and refused by the reference validators (rule 1).

The list binds WRITERS and is closed by the schema. The reference readers carry
whatever string the document holds, report an unrecognised member on their warn
channel, and refuse nothing over it; a consumer that cannot act on one refuses
the ELEMENT that carries it, which is what keeps a future addition to this list a
MINOR (see [Machine-readable schema](#machine-readable-schema) and
[`VERSION_POLICY.md`](./VERSION_POLICY.md)).

```yaml
interfaces:
  - id: ucie_link_0
    type: micro_bump
    from: {component: U1, surface: top, port_layer: TopMetal2}
    to:   {component: interposer, surface: top, port_layer: Metal5}
    physical: {pitch: 45.0, diameter: 25.0, height: 24.0}
```

| Key | Required | Type | Description |
|-----|----------|------|-------------|
| `id` | **YES** | String | Unique interface id |
| `type` | **YES** | String | One of the known types above |
| `from` / `to` | NO | Object | Endpoints: `component`, `surface`, `port_layer` |
| `physical` | NO | Object | `pitch`, `diameter`, `height` (um) |

## Netlist

Optional top-level assembly netlist. Either inline `nets` or a path to an
`external_netlist`.

```yaml
netlist:
  external_netlist: ""             # optional path to an external netlist
  nets:
    - name: VDD
      class: power                 # net class; default "signal"
      external: true               # exposed at the assembly boundary
      connections:
        - {component: U1, pin: VDD, layer: TopMetal2}
        - {component: interposer, pin: P12, layer: Metal5}
```

| Key | Required | Type | Description |
|-----|----------|------|-------------|
| `nets[].name` | **YES** | String | Net name |
| `nets[].class` | NO | String | Net class; default `signal` |
| `nets[].external` | NO | Bool | `true` if the net is exposed at the assembly boundary |
| `nets[].connections` | NO | Array | `{component, pin, layer}` endpoints |
| `external_netlist` | NO | String | Path to an external netlist file |

An otherwise-empty `netlist` block (no `nets` and no `external_netlist`) is not
preserved on write.

## Flow

Optional, **opaque** build/flow block. It is host-specific build configuration
that the format leaves unspecified, and readers preserve it verbatim across a
load/dump cycle.

Four rules, because this block is the only place the format carries something a
host might act on:

1. A reader MAY parse it. A reader that cannot parse it MUST NOT reject the
   document: the content is unspecified, so failing to understand it is the
   expected case, and the document is still valid without an executable flow.
2. The block is not authorization. It names nothing a reader is required to run.
   A host that executes what it names is responsible for establishing that the
   block is one it or its operator intended, because a `.chiplet` can arrive
   from anywhere and the document itself is the least trustworthy thing in the
   room. Authorship is not a usable test: a re-export copies a foreign block
   into a freshly generated file, so "our tool wrote this" survives an exchange
   that "our operator meant this" does not.
3. Nothing else in the format may be made to depend on it. It stays removable.
4. A host that re-emits a flow block it did not author MUST re-emit it byte for
   byte, unless the user edited the flow through that host. In particular a host
   MUST NOT write back resolved values of `${...}` variables, and MUST NOT drop
   keys it does not model: the block is opaque precisely so it can carry what a
   given host does not understand, and a lossy round-trip defeats that. The
   reference readers deliver the block as raw text for this reason; a host with
   a parsed flow model keeps the original text beside it and writes the text.
   "The block" is the run of lines the
   [top-level block grammar](#top-level-block-grammar-normative) below assigns
   to `flow`, which is where byte for byte gets its meaning.

```yaml
flow:
  steps:
    - {name: export, tool: kicad}
    - {name: assemble, tool: hyp_to_gds}
```

## System (format 1.1)

Optional top-level block, and the one that changes what a document IS. A
document with a top-level `system` key is a **source**: it names the dies, where
they sit and how their ports are wired, and a tool (the elaborator) derives the
assembly from it and from each die's contract. A source is never an assembly.

A source carries exactly `format_version` (at least `"1.1"`), `_metadata`,
`assembly` (`name`, optional `description`), optionally `interposer` and `flow`,
and `system`. It carries none of the blocks the elaborator derives:
`technologies`, `connection_stacks`, `components`, `interconnect`, `interfaces`,
`netlist`. The schema states both rules, and the reference readers export the
second list as `DERIVED_BLOCKS`.

**Readers.** Every assembly reader refuses a source, whatever else it is told:
`allow_intermediate` and `validate = false` do not let one through, and the
refusal comes before the version check, so the message says what the file is.
The error is `SourceDocumentError`, a `ChipletFormatError`, in both references.
Only a source reader reads one; the Python reference has that path behind an
explicit `allow_source=True`, which nothing but the source tooling passes, and
the C++ reference has none. The reason is the reader written before 1.1: it warns
on the higher minor and carries an undeclared root key additively, so without the
`_metadata` marker it reads a source as an assembly with no components, and with
the marker, any caller that allows intermediate files still would.

**Keys.** `system` is a closed mapping, written in this order: `carrier`,
`registry`, `dies`, `buses`, `links`, `rails`, `alone`, `open`, `external`,
`fill_keepout`. Only `carrier` and `dies` are required, and from format 1.3
`dies` may be absent or empty (see [Format 1.3](#format-13-the-unplaced-source)).

| Key | Holds |
|-----|-------|
| `carrier` | `technology`, `top_cell`, `size: [w, h]` in um (optional from 1.3) |
| `registry` | interconnect registry root, relative to the file; absent means the consuming flow's own |
| `dies` | one item per instance: `id`, `die` (the folder holding its contract, relative), `at: [x, y]` (die centre, canonical frame, um; optional from 1.3), `rotation` (0, 90, 180 or 270, default 0; never written without `at`), `method` (interconnect method id), `thickness` (um, as written; absent is 0.0), `admission` (optional, format 1.4: a relative `.json` holding the die's admission, see [Format 1.4](#format-14-a-dies-admission)) |
| `buses` | `ends: [inst, inst]`, `ports`, optional `net_prefix`, `net_suffix` |
| `links` | `a`, `b` (port refs), optional `net` |
| `rails` | `name`, `class` (`power` or `ground`), `volts` (optional, format 1.2: the rail's nominal supply in volts, a positive number, on a `power` rail only), `width_um`, `amps`, `current_density_ma_per_um` (optional, format 1.5, on a rail of either class: the width it is routed at in um, or the current in A with the density in mA/um it is drawn at, never both forms, see [Format 1.5](#format-15-a-rails-routing-requirement)), `ports` |
| `alone` | port refs whose pads each get their own net |
| `open` | port refs that get no net and no connection |
| `external` | `profile` (a shipped profile name or a relative `.json`), `edges` (`north`, `south`, `west`, `east`, each at most once, each a list of port refs or rail names; optional from 1.3) |
| `fill_keepout` | `halo_um`, a map of layer name to um |

**Keys added after 1.1.** A key a later minor adds to the system block is
optional, and the minor that adds it is schema data (`x-since` on the property;
`SOURCE_KEY_SINCE` in the Python reference). A source stamped below that minor
is refused, and a writer stamps the highest minor among the keys it writes, so a
source that writes none stays `"1.1"`. A source reader reading a source stamped
ABOVE the minor it implements skips an item or `system` key it does not know and
reports it,
because the minor's promise is that a reader may skip it; at or below its own
minor an unknown key is still refused. The keys are `rails[].volts` (1.2),
`dies[].admission` (1.4), and `rails[].width_um`, `rails[].amps` and
`rails[].current_density_ma_per_um` (1.5). Format 1.3 adds no `system` key: it adds the root key `unplaced` and lets
a source leave keys out, and a writer stamps `"1.3"` only when the file uses one
of those, so the rule is the same one, the lowest minor the content needs.

A port reference is `<instance>.<port>`, where `<port>` is the die inventory's
port name, not a pad name. Inside a port or net name, `[a..b]` (inclusive,
decimal, either order) is the only shorthand and expands to one name per index.
Paths are relative to the file: no `${VAR}`, no absolute path, no `..` segment.

**Line rules** (held by the source reader on the text, like the
[top-level block grammar](#top-level-block-grammar-normative), because a schema
cannot state them): every item of `dies`, `buses`, `links`, `rails`, `alone` and
`open` is one line, a block-sequence entry holding a flow mapping (a port
reference for `alone` and `open`); the keys inside an item appear in the order
above; `external` is a block mapping and each edge one line holding a flow
sequence. A string value goes bare when it matches `[A-Za-z_][A-Za-z0-9_./-]*`
and is not a word a YAML 1.1 reader takes for a bool or a null (`y`, `n`, `yes`,
`no`, `true`, `false`, `on`, `off`, `null`, in any case form); every other string
is quoted, ranges and `format_version` included. The source reader reads both
forms and refuses a bare value outside the rule, giving the line to write with
that value quoted. This is the one exception to validation rule 7, and the
reason sits there.

**What the elaborator derives, and in what order.** Membership is always
derived; net NAMES are the author's, which is why the optional affixes exist.
- Every die port appears exactly once across `buses`, `links`, `rails`, `alone`,
  `open` and the port refs of `external`.
- A bus port has the same name on both ends (a differently named pair is a
  `link`) and is one typed link with one lane per pad, oriented output end
  first. Its net is `<net_prefix><port><net_suffix>`, lane-indexed.
- A link's net is `net`, default `<a instance>_<a port>`.
- An `alone` port gets one net per pad, `<instance>_<pad>`, emitted in inventory
  order (dies in `dies` order, ports in first-named order, pads by ordinal), so
  inserting or deleting an `alone` line never reorders the netlist. An `external`
  port ref is the same, emitted with the `alone` nets.
- A rail joins every pad of its ports into one net named `name`. A rail with one
  pad in total is emitted as a tie of its class, after the `alone` ties, in
  `rails` order.
- `buses`, `links` and `rails` are emitted in file order; io_pads are derived per
  edge, in the order the edges are written.
- Never written, always derived: die z, die dimensions and technology (from the
  contract), carrier position and thickness, pad coordinates, the netlist, every
  digest.

**Supply voltage (format 1.2, checked with the die folders).** The domains a
rail reaches are the `supply_domain` of each power or ground terminal of every
pad of every port it names, and their volts are the die contract's
`supply_domains` (die contract 1.2). Values compare as written, with no
tolerance, since they are nominal.
1. A power rail reaching domains of different volts is refused, naming each
   instance, domain and value.
2. A power rail whose `volts` is not that of the domains it reaches is refused.
3. A ground rail reaching a domain whose volts are not 0 is refused.
4. A power rail with no `volts`, a die whose contract declares no
   `supply_domains`, or a pad whose terminal names no domain is a note on the
   rail ("voltage not checked for <rail>: <what is missing>"), never a refusal,
   so a source or a die from before 1.2 checks as it did.

A die folder whose contract carries `supply_domains` that admission would refuse
(a 1.0 or 1.1 contract, or one breaking a supply-domain rule of
STRICT_ADMISSION_API.md revision 4, which `chiplet_format_io.supply_domains`
holds for both readers) is refused as unreadable. Rule 3 therefore meets only a pad the inventory serves as
ground whose terminal the contract names as power. The Python reference gives
the labels these checks rest on as
`chiplet_system_description.source.supply_pins(path)`: one row per supply pin on
a rail, sorted by instance then pad, `{instance, pad, port, domain, rail,
volts}`, where `volts` is the domain's as the die declares it, 0 on a ground
pin, and null when the die declares none, even where the rail states its own:
null is exactly the pin rule 4 leaves unchecked. The row shape is stable.

## Format 1.3: the unplaced source

A source drawn on a schematic sheet says what the system is, its dies and how
their ports are wired, and nothing about where anything sits. From format 1.3 a
source may leave its placement to `packaging-flow place`:

- a `dies` item may omit `at` and `rotation`, both or neither (a `rotation`
  without `at` is refused at every minor);
- `carrier` may omit `size`;
- `external` may omit `edges`;
- `system.dies` may be absent or empty (a project file before its first sheet
  import);
- the root key `unplaced`, written after `system`, holds `pins`: one line, a flow
  list of the external entries (port refs, ranges allowed, and rail names) that
  no edge carries yet. Each entry appears once across `unplaced.pins` and the
  edges, and `unplaced` needs an `external` block, which names the profile.

```yaml
format_version: "1.3"
# _metadata and assembly as in any source
system:
  carrier: {technology: intm4tm2, top_cell: CF_CARRIER}
  dies:
    - {id: U1, die: dies/DRIVER, method: wire_bond}
  external:
    profile: intm4tm2_wire_bond_v0
unplaced:
  pins: [U1.CLK, VDD, VSS]
```

Each of these in a source stamped below `"1.3"` is refused, and the finding
names the minor. `unplaced` is a source's key: a document that carries it
without a `system` block is refused at every stamp. A writer stamps `"1.3"` only when the file leaves something
unplaced; once placement fills every `at`, the `size`, and moves every entry of
`unplaced.pins` onto an edge row (deleting `unplaced`), the stamp drops back to
what the rest of the file needs.

**Who reads it.** The source reader (tier 0) reads an unplaced source. Tier 1,
the check against the die folders, refuses it, one finding per unplaced thing
(an unplaced die, a carrier with no size, unplaced pins, no dies), each naming
`packaging-flow place` as the fix, unless the caller opts in; then each is a
note, placement is checked for the placed dies only, the carrier outline only
when it has a size, and the entries of `unplaced.pins` count as external ports
do. `check` opts in, so it reports an unplaced source as notes. The elaborator,
and so `run` and `interposer-pnr`, always refuses one: there is no assembly
without positions.

**Why a root key.** A source reader before 1.3 refuses an unknown ROOT key at any
minor, while it skips an unknown item or `system` key of a higher minor with a
note (see [Keys added after 1.1](#system-format-11)). Had the unplaced entries
been a `system.external` key, such a reader would skip it and read on without
those externals: wrong, not incomplete. As a root key, it refuses the file.

## Format 1.4: a die's admission

A die this run does not build arrives with its producer's own results: a die
from a stock pad-ring flow, from another group, or from a foundry. From format
1.4 its `dies` item may name an admission file, `admission`, the last key of the
item: a relative path to a `.json` file under the path rule (not absolute, no
variable, no `..` segment), resolved against the source's folder. The file
holds one JSON object:

| Key | Holds |
|-----|-------|
| `reason` | a non-empty string: why the die is placed |
| `scope` | a non-empty string: what the admission covers, and what it does not |
| `not_verified` | a non-empty list of non-empty strings: the checks the run does not verify for this die |

Any other key is the file's own and is not read. The format has no key for who
admits the die; the consuming flow takes that when the run starts, and a key the
file adds for it is not read.

```yaml
format_version: "1.4"
# _metadata and assembly as in any source
system:
  carrier: {technology: intm4tm2, top_cell: CF_CARRIER, size: [3000, 2000]}
  dies:
    - {id: U1, die: dies/IO_RING, at: [1500, 1000], method: cupillar_opt1, admission: admission.json}
```

A source stamped below `"1.4"` that writes `admission` is refused, and the
finding names the minor. A writer stamps `"1.4"` only when a die names one.

**Who reads it.** The source reader (tier 0) holds the key to the path rule and
the `.json` suffix. Tier 1, which reads the die folders, reads the file too and
refuses, on that die's line, one that is absent, is not a JSON object, or lacks
one of the three keys or holds one of the wrong shape. Elaboration hands the
flow each admitted die's instance and file. What an
admission allows is the consuming flow's to decide; the format states no
verdict, and an admitted die is never one the run built.

**Why an item key.** A source reader before 1.4 skips `admission` with a note,
as it skips any item key of a higher minor, and reads on with the die not
admitted. Unlike the unplaced entries, that reading fails closed: a flow that
places a die it did not build only on an admission refuses the die, so no root
key is needed.

## Format 1.5: a rail's routing requirement

A rail with no requirement is routed at the narrowest width its layers allow,
which is right for a signal and wrong for a supply that carries current. From
format 1.5 a `rails` item of either class may state what it needs, in one of two
forms, written after `volts` and before `ports`:

| Key | Holds |
|-----|-------|
| `width_um` | the width the rail is routed at, um |
| `amps` | the current the rail carries, A; only with `current_density_ma_per_um` |
| `current_density_ma_per_um` | the current density it is drawn at, mA per um of width; only with `amps` |

Each value is a positive, finite number (a bool is not a number). A rail states
`width_um` or the pair, never both forms: `amps` without the density, the
density without `amps`, and `width_um` beside either are refused at every minor
that has the keys. The source never computes a width from the pair; the router
does, as `amps * 1000 / current_density_ma_per_um`.

```yaml
format_version: "1.5"
# _metadata and assembly as in any source
system:
  # carrier, dies and the other wiring as in any source
  rails:
    - {name: GND, class: ground, width_um: 20, ports: [U1.VSS, U2.VSS]}
    - {name: VDD, class: power, volts: 1.2, amps: 1.5, current_density_ma_per_um: 75, ports: [U1.VDD, U2.VDD]}
```

A source stamped below `"1.5"` that writes any of the three is refused, and the
finding names the minor. A writer stamps `"1.5"` only when a rail states one.

**Who reads it.** The source reader holds the pairing and value rules above, the
same refusals as a row of interposer-pnr's `net_requirements` (version 1), so
a rail the source refuses is one the router refuses for the same reason; the
schema states them too, all but finite, which a schema cannot say (the reader
also refuses an integer too large for a float). What the source does not
state is the router's to judge: the width it rounds to its grid, the maximum
width of each route layer (a deck rule, not a constant of the format), whether
the net exists and reaches two endpoints, and whether the backend routes a width
at all. A flow hands each rail that states a requirement to its router under
the rail's name.

**Why an item key.** A source reader before 1.5 skips the three keys with a
note, as it skips any item key of a higher minor, and a router fed that reading
draws the rail at the layer minimum: wrong, not incomplete, the reason 1.3 made
`unplaced` a root key. They stay item keys because the rail is the one place a
requirement belongs, and the only consumer that turns the reading into copper
fails closed: a flow that routes from a source checks that the readers it uses
carry the three keys, and refuses a reading that skipped any `rails` key, before
it builds anything. A root key would restate every rail name in a second place
that can drift from the first. See VERSION_POLICY.md, "Stamps per document
kind".

## Top-level block grammar (normative)

Flow rule 4, the merge tooling that carries a foreign block across a re-export,
and the block-ownership guard all need one answer from a document: which run of
lines belongs to which top-level key. That question is answered on the TEXT,
never on a parsed node tree, because a node tree re-quotes scalars and drops
comments on the way back out. It is answered here once, so that the merge
splitter in the KiCad plugin, the Python reference reader and the C++ reference
reader implement one grammar rather than three.

### Key line

A top-level block starts at a KEY LINE. A line is a key line if and only if its
content matches, anchored at both ends,

```
^([A-Za-z0-9_][A-Za-z0-9_.-]*):(?:\s.*)?
```

with capture group 1 as the key. Line CONTENT is the text up to the next LF with
one optional trailing CR removed, so a CRLF document reads exactly like an LF one;
the terminator is never part of what is matched.

Three notes, each of which has already cost this format family a defect:

- End the expression with `\Z` (Python `re`) or `(?![\s\S])` (portable, and the
  spelling `schemas/chiplet.schema.json` uses for the same reason), or match the
  whole content (`std::regex_match`, which needs no end anchor). NEVER `$`: in
  Python `$` also matches before a trailing newline, so a `$`-anchored
  implementation and an ECMA-262 one read different grammars from the same
  expression.
- ECMAScript `.` excludes CR as well as LF, unlike Python's, so a `std::regex`
  implementation writes `[^\n]` where the expression above writes `.`. On a line,
  which by definition carries no LF, the two mean the same thing.
- A line ends at LF, and nowhere else. Python's `str.splitlines()` also breaks on
  CR, VT, FF, U+0085, U+2028 and U+2029, so a splitter built on it grows a
  top-level block out of any quoted scalar carrying one of those: an
  `assembly.name` of `"demo<U+2028>flow: injected"` becomes a `flow` block that is
  not in the file, and a merge then hands a foreign host bytes that came out of
  somebody's assembly name. Iterate on LF by hand. The oracle carries this case.
  The SPLITTER's answer for those bytes is settled here; whether a READER may
  open such a document at all is a different question, settled under
  [Line breaks](#line-breaks-normative), and the answer there is no.

What follows from the expression, each of these a case in the oracle: an indented
`  flow:` is not a key line and stays inside the block it sits in; `flow: value`
and `flow: # note` are key lines (a value or a trailing comment on the key line
changes nothing); `flow:value` is NOT, because the expression requires whitespace
between the colon and anything after it; and `---`, `...`, a `#` comment, an empty
line, a bare `:` and a key containing a space are not key lines.

### Line breaks (normative)

A document's line breaks are **LF** and **CRLF**, and nothing else.

The criterion that generates the rest of this section, stated once: **a character
that a YAML parser treats as a line break and THIS GRAMMAR does not makes a
document ill-formed.** The grammar is not one consumer among many. It is what the
repeated-top-level-key scan, flow rule 4, `top_level_blocks()` and every
splitting host read, so a character that moves a line for the parser and not for
the grammar hides a top-level key from all of them at once, and the hidden key is
the one that wins: the parser builds the document a consumer then acts on.

Stating the criterion this way makes it EXECUTABLE, which is the point.
`conformance/test_top_level_blocks.py` derives the members by running a parser
over a code-point range and asserts the reference reader refuses exactly what the
derivation finds. Measured over `U+0000..U+21FF` on PyYAML 6.0.3, the set has
four members:

| Code point | Ill-formed |
|------------|------------|
| `U+000D` CARRIAGE RETURN | unless the next byte is LF |
| `U+0085` NEXT LINE | anywhere |
| `U+2028` LINE SEPARATOR | anywhere |
| `U+2029` PARAGRAPH SEPARATOR | anywhere |

"Anywhere" means anywhere: in a scalar, in a comment, in a key. Both reference
readers refuse such a document at load, on the text, before any YAML parse, and
the refusal names the code point and the line, because all four are invisible in
an editor.

**A CR at end of file, with no LF after it, is refused too.** That case is
DECIDED here rather than inherited, because nothing else forces it: both
reference parsers accept such a document and agree on it (PyYAML 6.0.3 and
yaml-cpp 0.8.0 both drop the CR and read `demo`). It is refused for two reasons.
The rule stays ONE property of the bytes, "every CR is immediately followed by
LF", which is what a splitter carrying no YAML lexer can enforce in a single pass
over the text. And both reference line splitters pop a trailing CR whether or not
an LF follows it, so accepting the document would mean every implementation reads
one byte less than the file holds and none of them says so.

The SECOND reason, sharper but narrower, covers `U+0085`, `U+2028` and `U+2029`
only: a YAML 1.1 parser (PyYAML) treats those three as line breaks and a YAML 1.2
parser (yaml-cpp) does not, so the same bytes are two different documents and no
reading of them is conforming, exactly as for a repeated top-level key. The
disagreement is not academic and it runs in both directions, measured on PyYAML
6.0.3 and yaml-cpp 0.8.0:

- `name: demo<U+2028>format_version: "9.0"` inside `assembly` gives PyYAML a
  SECOND top-level `format_version` whose value wins, with the top-level key list
  unchanged, so the spoof moves a value and not the shape and no
  "unexpected top-level key" guard sees it; yaml-cpp throws `illegal map value`
  on the same bytes.
- The same separator followed by ordinary text flips it: yaml-cpp loads the
  document with the three bytes inside the scalar, and PyYAML throws.

So neither reader can be made to imitate the other, and the refusal belongs to
the format rather than to either parser.

That second reason does NOT generate `U+000D`, and `U+000D` is the member with
the widest blast radius, being one lost byte away from any CRLF file. Both
parsers break a line on a lone CR. Measured on the same two versions:
`name: demo<CR>format_version: "9.0"` gives PyYAML a second top-level
`format_version` whose value wins and gives yaml-cpp an `illegal map value` throw
at line 3, column 28; `name: demo<CR>trailing` loads in yaml-cpp with the CR
FOLDED TO A SPACE, so `assembly.name` comes back as `demo trailing`, and throws a
`ScannerError` in PyYAML. One reader silently changes the value, the other
refuses the file, and the grammar sees neither. A set built from the
parser-versus-parser reading alone has three members instead of four, which is
how this rule first shipped, and is why the set is derived rather than listed.

A WRITER that holds one of the four in a value MUST escape it inside a
double-quoted scalar (`\r`, `\N`, `\L`, `\P`, or the equivalent `\xNN`/`\uNNNN`
spelling its emitter produces) rather than write the raw character, so that what
it wrote is a document these readers still open. This is not hypothetical for a
writer built on PyYAML: `yaml.safe_dump(allow_unicode=True)` writes NEL, LS and
PS raw into a single-quoted scalar, and PyYAML then folds them back on the next
read, so the value does not survive the round trip. Both reference emitters
already escape CR without being asked; that is a fact about two versions, so the
conformance tests assert it for CR on the same terms as for the other three.

**The refusal is on the RAW BYTES; the ESCAPED spellings stay legal.** That is
the discriminating rule of this whole section, and it is what makes the set
liveable: a value that genuinely needs one of these characters is written
`name: "demo\Lx"`, which is two ordinary characters to the grammar and reads back
as `demo<U+2028>x`. The oracle carries that document as a case that must LOAD, so
the control for this rule is a document that opens rather than a second document
that is refused for a different reason.

What is NOT true, and was believed here for a while, is that putting the raw
character inside a double-quoted scalar makes the two readers agree. Measured on
PyYAML 6.0.3 and yaml-cpp 0.8.0:

- A raw CR or NEL inside a double-quoted scalar FOLDS TO A SPACE in PyYAML:
  `"demo<CR>x"` and `"demo<NEL>x"` both read back as `demo x`. yaml-cpp folds the
  CR the same way but KEEPS the NEL bytes, so on the NEL the two readers return
  different strings and neither says anything.
- A raw LS or PS is folded by neither reader, but the whitespace AROUND it is
  dropped by PyYAML and kept by yaml-cpp: `"demo<LS>   x"` reads back as
  `demo<LS>x` in PyYAML and as `demo<LS>   x` in yaml-cpp, and the same goes for a
  tab after the character or spaces before it. The apparent agreement holds for
  exactly one spelling, the one with no adjacent whitespace.

So a quoted scalar is not the safe place for the raw character. It is the place
where the disagreement stops being about the document's SHAPE, where a key list
would show it, and becomes a difference in a VALUE, where nothing downstream can
see it at all.

One caveat on the escaped spellings, measured rather than assumed: yaml-cpp 0.8.0
reads `\N` as the single byte `0x85` instead of the UTF-8 encoding of `U+0085`,
while PyYAML reads it as `U+0085`. Both accept the document; they disagree about
the bytes in the value. `\x85` is read correctly by both and is what the C++
reference writer emits, so prefer it for NEL. `\r`, `\L` and `\P` agree in both
readers.

**Why refusing these four is safe.** Not because "the population is zero". A
sweep bounds the CORPUS it walked, and no corpus anyone here can walk covers
build trees, what a user pastes into a GUI (LS and PS are exactly what a paste
from a web page carries), the documents held by the KiCad fork's users, or git
history. The load-bearing argument is the writer rule plus the escaped form: no
producer of ours can emit one of these characters raw once the rule above is in,
and any value that genuinely needs one is carried escaped, so a document that
meets this refusal was not written by a conforming producer. That was measured
rather than asserted: all 19 `.chiplet` in this repository, fixtures and
examples, dumped through the writer before and after the rule, give zero
differing files.

The sweeps corroborate it, and are worth naming for what they actually covered:
every `.chiplet` in the ecosystem outside build trees plus the YAML and stackups
shipped alongside them, zero occurrences of NEL, LS and PS; the KiCad plugin's
187 `.chiplet`, zero; 183 `.chiplet` across the umbrella tree with build trees
included, zero lone CR and zero of the other three. All three walked committed,
machine-written output, which is the population most likely to be clean, so they
raise confidence and do not establish the claim on their own.

### Unattributable lines at column zero (normative)

A key line opens a block. Every other line at column zero has to belong to
somebody, and for most of them it does: this section says which, and refuses the
rest rather than guessing.

Column zero means the line content does not start with a SPACE. A tab therefore
counts as column zero, deliberately: this grammar has no notion of tab
indentation and YAML has none either in block context, so a nonblank tab-led
line cannot quietly be attributed to the block above it.

At column zero, five shapes are ATTRIBUTABLE and a splitter carries them in the
block they sit in:

- a blank line: its content consists only of SPACE (U+0020) and TAB (U+0009),
  possibly empty; other Unicode whitespace and C0 separators are not blank;
- a comment, `#`;
- a directive, `%`;
- a document marker, `---` or `...`, which belongs to the current block (the
  preamble only before the first key line);
- a BLOCK SEQUENCE ENTRY: `-` followed by a space, a tab, or end of content.

The last one is the load-bearing exemption, not a courtesy. PyYAML writes a
block sequence under a mapping key at the PARENT's indentation, so a
`components:` block is followed by `- id: ...` lines sitting at column zero, and
that is what almost every generated `.chiplet` in this ecosystem looks like: of
the 70 column-zero non-key lines across the 77 tracked `.chiplet` when this rule
was written, 68 were exactly that. A rule that called every non-key line at
column zero unattributable would refuse to split nearly every real document.
The entry is attributable in the strong sense: the splitter and a YAML parser
agree that it belongs to the key whose block it sits in.

Anything else at column zero makes the document NOT SPLITTABLE. A splitter MUST
refuse to split it and MUST NOT attribute those bytes to the preceding key. The
document can still be valid YAML: a reader MUST not reject it merely because it
cannot be split, but other syntax and load-time checks still apply. This is the
same verdict, on the same terms, as the quoted key in the next section, which is one
spelling of this rule and keeps its own diagnostic because its cause is
different.

The motivating defect is a top-level key to a YAML parser and no key at all to
the grammar: the SPLIT and the PARSE report different documents from one file.
The broader rule also refuses continuations whose ownership the line grammar
cannot establish; it does not assert that every refused line is a parsed key.
Three problematic key spellings came out of a reference WRITER rather than
hand editing:

- an EXPLICIT key, `? "a\Lb"` on one line with its value on the next, `: x: 1`,
  which is what PyYAML 6.0.3 emits for a top-level key carrying NEL, LS, PS or a
  CR;
- a BARE key outside the grammar, `a b:`, which is what the same emitter writes
  for a top-level key carrying a space;
- a QUOTED key, `"flow":` or `'yes':`, which is what it writes for a key that
  would otherwise re-read as a boolean, a null or a number, and what a
  document-wide key-quoting emitter switch produces for every key at once.

### Top-level keys are written bare

`"flow":` and `'flow':` at column zero are valid YAML and are NOT key lines under
this grammar. A splitter therefore attaches such a block to the PRECEDING one,
where it is owned by whoever owns that block and is dropped by the next re-export
that regenerates it.

So a WRITER MUST emit top-level keys bare. This is the same rule as validation
rule 7, seen from the other side: rule 7 forbids fixing scalar quoting with a
document-wide emitter switch precisely because such a switch quotes keys too, and
this is what a quoted key costs. Keys stay bare, values are quoted.

A READER is bound differently. A quoted key at column zero makes a document NON
SPLITTABLE, not invalid: it is well-formed YAML, the schema has nothing to say
about how a key was spelled, and a reader that rejected it would be stricter than
this specification. A host that splits MUST refuse to SPLIT such a document
rather than mis-attribute the block, and MUST NOT write it back (see below).

The writer rule is therefore a countermeasure, not a prohibition: it keeps a
document from reaching the shape in which the ownership guard cannot answer, and
nothing in the schema forbids that shape.

### A writer refuses a top-level key it cannot emit as a key line

The rule above, stated as the obligation it puts on a producer. A top-level key
is a bare identifier `[A-Za-z0-9_][A-Za-z0-9_.-]*` that the emitter writes as a
KEY LINE at column zero. Nested keys are unrestricted: the grammar is a statement
about column zero and has nothing to say about anything indented under one.

A WRITER MUST refuse to write a mapping whose top-level keys it cannot emit that
way, and the refusal MUST name the key. It MUST NOT emit the document and leave
the disagreement in the file, which is what a writer that only checks its
quoting does: the quoted key is one of three spellings, and the other two, the
explicit key and the bare key with a space, are not quoted at all.

The check that covers all three is a POST-check and not a rule about keys: emit
the document, split the text just emitted with the same splitter every host runs,
and refuse unless the split returns exactly the keys the writer was given, in
order. That mechanism cannot drift from the reader, because it is the reader, and
it catches the keys an emitter quotes on its own initiative (`yes`, `null`,
`1.0`), which a regular expression over the key would pass.

The Python reference does this in `dumps()`, and its refusal names the key, the
line the emitter actually wrote, and the way out (rename the key, or nest it).
The C++ reference checks its complete output too, after appending any retained
`flow_yaml` source slice. Its generated mapping uses literal top-level names,
but the retained slice is caller-supplied text and can introduce additional or
unattributable lines. The expected key sequence comes from the generated mapping
plus the one emitted `flow` block, not from parsing the combined output. A failed
post-check raises `ChipletFormatError`, including with validation disabled.

This is a line-based ownership grammar, not a YAML lexer. It cannot distinguish
a key-shaped continuation such as `netlist: x` inside a multi-line quoted scalar
from a real key line; a harmless non-key continuation can instead be refused.
The readers do not claim that splitting arbitrary hand-authored YAML recovers its
parsed keys. Neither reference emitter generates that quoted spelling from a
mapping. Writer post-checks close the producer boundary by requiring the emitted
key sequence to match the known intended sequence, including when C++ receives
a caller-supplied source slice. This does not turn the splitter into a parser or
establish semantic validity of arbitrary source text.

### Block extent

A block's text runs from its key line up to but EXCLUDING the next key line, or to
end of file. Three consequences, all of them normative:

- Blank lines and full-line comments between two blocks are not key lines, so they
  attach to the PRECEDING block. A comment written directly above `flow:` belongs
  to the block before it, not to `flow`. This is deliberate: it is what the merge
  tooling already does, and a splitter that guessed otherwise would move bytes
  between two blocks with different owners.
- Lines before the first key line (a leading `---`, a file header comment) are a
  PREAMBLE that belongs to no key. An implementation that exposes the preamble
  uses the empty string as its key.
- A repeated top-level key is ILL-FORMED, and a reader MUST refuse the document at
  load: PyYAML resolves such a key to the last value and yaml-cpp to the first, so
  two conforming readers read different documents from one file, and neither the
  schema nor a parsed node tree can see that it happened. The refusal is on the
  text, because that is the only place the repeat still exists. A splitter could
  concatenate the two runs under the first occurrence without dropping a byte,
  which is what this specification used to say; that decides who owns the text and
  says nothing about which value wins, so it is no longer enough.

### Raw block text

A raw-block accessor returns the bytes exactly as they stand in the source: the
key line included, original line endings, no trailing-newline normalisation, no
re-indentation, nothing stripped. Both reference readers provide one, and that is
what makes flow rule 4 implementable:

- Python: `chiplet_format_io.top_level_blocks(text)` returns every block in
  document order, and `top_level_block(text, "flow")` returns one or `None`.
- C++: `ChipletDocument::flow_yaml` is the source slice of the `flow` block, key
  line included.
- Both, the key SEQUENCE alone (reader release 1.5.0): Python
  `chiplet_format_io.split_top_level_keys(text)` and C++
  `chiplet_format_io::split_top_level_keys(text)` return `TopLevelKeys { keys,
  not_splittable }`, a thin wrapper over the same splitter, for a host that writes
  a document itself and must check that the bytes it is about to write split into
  exactly the top-level keys it intended, in order. `keys` excludes the preamble and
  is unreadable when `not_splittable` is set; a repeated top-level key raises as a
  load does. It is not a loader: a forbidden line break splits here and is refused at
  load, and a key-shaped continuation inside a quoted scalar is a key to this grammar
  and a scalar to the parser, so the host compares against its own intended list and
  never reads a non-empty result as success.

### A block the grammar cannot delimit

A `flow` block can be spelled so that YAML sees it and this grammar does not: a
flow-style document (`{format_version: "1.0", ..., flow: {...}}`), a key line
written `flow :`, or any file that is not splittable at all because some line at
column zero is one no top-level key owns. The block then has no slice, and rule
4's byte for byte is not a thing that can be done to it. The first two of those
are themselves lines at column zero that no key owns, so such a document is not
splittable either; "the flow block has no slice" is the consequence a
source-slice writer acts on, and "the document cannot be split" is the wider fact
it follows from.

Three obligations follow, and they are deliberately not the same obligation:

- A host MUST still LOAD the document. Flow rule 1 already says a reader that
  cannot parse the block must not reject the file; not being able to *delimit* it
  is a weaker failure than not being able to parse it, so it cannot justify a
  stronger response. What is lost is a write guarantee, not the document.
- A host MUST NOT write the document back without re-authoring that block. The
  bytes were never captured, so writing is a choice between dropping the block
  and inventing it; a host that cannot make that choice honestly refuses to save
  and says why. Re-authoring the flow through the host is the way forward: then
  the host owns the block and rule 4 no longer applies to it.
- A host MUST NOT emit a re-serialisation of the parsed node in the place the
  source text belongs. A node dump re-quotes scalars and drops comments and is
  indistinguishable, in the file, from the source it replaced. This is the
  failure mode the raw-block accessors exist to prevent, and it is worse than
  refusing because nothing downstream can detect it.

The C++ reference records which of the three states a document is in
(`ChipletDocument::flow_source`: `Absent`, `Slice`, `NotDelimitable`), keeps
`flow_yaml` empty in the third, and throws from `dumps()`. A writer with no
source slice at all, such as the Python reference's canonical `dumps()`, never
promised rule 4 in the first place and is not bound by this.

### One oracle

Every implementation is measured against
[`conformance/fixtures/top_level_blocks_cases.json`](../conformance/fixtures/top_level_blocks_cases.json),
never against another implementation: accept and reject key lines, documents with
the exact expected slice per top-level key, the documents a splitter must refuse
to split (`quoted_key_at_column_zero` and the wider
`unattributable_line_at_column_zero`), the documents a reader must refuse to load
(`repeated_top_level_key`, `forbidden_line_break`), and the documents whose
`flow` block has no slice. The verdicts are recorded separately, because a
document can be loadable and not splittable, splittable and not writable, and
splittable and not loadable (a forbidden line break, where the grammar has an
answer and the readers do not). Add a case to that file, never to a consumer.

Each case in the `refuse` group carries an explicit **`refused_by`** list, one or
both of `"splitter"` and `"reader"`. A consumer MUST filter on that field and
MUST NOT read the group name as the verdict. The group is called `refuse` and
says nothing about WHICH implementation refuses; it once held only splitter
cases, and a test parametrized over the whole group therefore asserted "the
splitter must raise" for every row in it. When reader-only rows arrived, that
test failed on documents a splitter is right to accept. Filtering on
`refused_by` survives both kinds of addition, and a vendored copy predating the
field fails on a missing key, which is loud, rather than on an inverted verdict,
which is not. The file also carries a `version`, so a stale copy can be named as
one.

## Data Types and Constraints

### Numeric Values

| Type | Unit | Description |
|------|------|-------------|
| Coordinates (x, y, z) | micrometers | Floating-point position values |
| Dimensions (width, height, thickness) | micrometers | Floating-point, must be >= 0 |
| `attachment_surface_z` | micrometers | Floating-point (interposer only); typically > 0 |
| Rotation (z) | degrees | 0-360 |
| Array count (x, y) | - | Integer >= 1 |
| Array pitch (x, y) | micrometers | Floating-point spacing |
| DBU | micrometers | Typical: 0.001 (1nm resolution) |

### String Values

| Type | Format | Example |
|------|--------|---------|
| Identifiers | Alphanumeric + underscore | `logic_die_1` |
| Paths | Relative or absolute | `./layouts/die.gds` |
| Dates | ISO 8601 | `2024-01-15` |
| Cell names | Valid GDS cell name | `LOGIC_TOP` |

### Path Resolution

- **Relative paths** are resolved from the directory containing the `.chiplet` file
- **Absolute paths** are used as-is
- Supported layout formats: GDS, GDS2, OASIS

---

## YAML Formatting

Both compact (flow) and expanded (block) styles are supported:

```yaml
# Compact style
position: {x: 1000, y: 2000, z: 0}
dimensions: {width: 5000, height: 5000, thickness: 100}

# Expanded style
position:
  x: 1000
  y: 2000
  z: 0
```

---

## Validation Rules

The reference libraries (`chiplet-format-io`, Python and C++) enforce a small set
of structural invariants; file-system and cross-reference checks are left to the
consuming tool. Structural validation of the document shape itself is available
separately and mechanically, through
[`schemas/chiplet.schema.json`](../schemas/chiplet.schema.json) (see
[Machine-readable schema](#machine-readable-schema)); the rules below are the
ones that need a reader, not a schema.

**Enforced by the reference validator (hard errors):**

1. `format_version` is checked by a tolerant policy, not by exact-string equality.
   The on-disk baseline stays `"1.0"` (the format grows additively under it); a
   writer stamps the lowest minor its content needs, so an assembly is still
   written `"1.0"` and a source `"1.1"`. A reader accepts the same major with a
   minor at or below the highest it implements;
   accepts a same-major *higher* minor with a warning (reading it as the
   supported version and ignoring unknown additions); and rejects a different
   major (higher or lower) or a malformed value as a hard error. The field MUST
   be a quoted `"MAJOR.MINOR"` string; an unquoted numeric is coerced through
   `str()` for back-compat, which is exactly where PyYAML (`1.10` -> `"1.1"`) and
   yaml-cpp (`1.10` -> `"1.10"`) diverge, so quote it. A *lossless* passthrough
   writer preserves a same-major higher minor it was handed (the stamped version
   must describe the bytes written); a *lossy* writer that reconstructs from a
   struct model stamps the supported version, because it may have dropped
   fields it did not understand. A member of a closed vocabulary added after 1.0
   carries the minor that added it (the schema's `x-since`, see [Machine-readable
   schema](#machine-readable-schema)). A document stamped below that minor is out
   of contract and refused, whether or not the reader knows the member, and no
   writer, lossy or not, stamps below the highest such minor among the members
   it writes.
2. `assembly.name` is required and must not be empty.
3. Every component has a non-empty `id` and a non-empty `type`.
4. Every `interfaces[]` entry has an `id` and a non-empty `type`. Both reference validators enforce that much and no more: WHICH type is a closed vocabulary the schema enforces on producers (`micro_bump`, `copper_pillar`, `tsv`, `wire_bond`, `solder_bump`), and the readers carry an unrecognised member as the string it was written as, report it on the warn channel and refuse nothing over it. Refusing the document would make every future addition to the list a MAJOR; refusing the ELEMENT is the consumer's call and is what keeps it a MINOR. This is a VALIDATOR rule, not a parser rule, in both readers: the C++ reference used to enforce it inside `parse_interface`, where `LoadOptions::validate = false` did not reach it, so the two readers disagreed on the same document with validation off.
5. Every `netlist.nets[]` entry has a `name`. *C++ reference only; the Python reference validator does not check this and accepts a nameless net.*
6. A file whose top-level `_metadata.finalize_required` is `true` is refused unless intermediate files are explicitly allowed (it is not yet in the canonical frame; run the named `finalizer`). A file with a top-level `system` block is a source and is refused by every assembly read path, before this rule and whatever it allows (`SourceDocumentError`, see [System](#system-format-11)). A source reader in turn refuses a source stamped below `"1.1"`, one that is not marked `finalize_required: true`, and one carrying a derived block.
7. Quoting is document semantics, not an emitter default. A writer MUST quote EVERY
   scalar the format defines as a string (`format_version`, component and pad `id`s,
   `assembly.name`, `created`, `modified` and other names and dates, `technologies`
   keys, `top_cell`, layout and file paths, adapter ids), unconditionally, without
   looking at the value. The rule is deliberately not "quote when the bare form would
   be re-typed": that condition would make the writer model the resolution tables of
   every YAML version and loader, make correctness depend on the value, and be
   untestable. The unconditional rule has a one-line self-test that discriminates
   exactly the property at stake: a writer parses its own output and every field the
   schema declares as a string comes back as a string. The class is wide: under a
   YAML 1.1 loader `0755` and `012` are integers, `0x1F` is an integer, `1.10` and
   `.inf` are floats, `no`, `on`, `off`, `yes` are booleans, `null` and `~` are null,
   and `2026-03-22` is a date, while yaml-cpp hands the same bytes back as strings,
   so two conforming readers name different components from one file. The guaranteed
   instance is not an odd id: `created` and `modified` are declared strings described
   as ISO 8601 dates, every real document quotes them, and a writer that emits them
   bare turns them into dates for every PyYAML consumer on the next save. A reader
   MUST NOT rely on coercion to recover the string; the reference reader's coercion
   of an unquoted `format_version` is back-compat for files that predate this rule,
   not a permitted form, and the conformance suite classifies that spelling as a
   schema negative. Emitters that quote only when YAML forces it (yaml-cpp's default,
   PyYAML's default for most scalars) do not satisfy this rule without an explicit
   quoting style for those fields. That style is applied per VALUE, never as a
   document-wide emitter switch: a switch that also quotes mapping keys (yaml-cpp's
   `Emitter::SetStringFormat(DoubleQuoted)` does) emits `"format_version":` at
   column zero, a spelling that is valid YAML but that the line-level ownership
   grammar of the merge tooling does not recognise as a key (see [Top-level block
   grammar](#top-level-block-grammar-normative): the document still loads, but it
   can no longer be split or written back), so a writer that fixed its scalars
   that way would defeat the block-ownership guard on the next merge.
   Keys stay bare; values are quoted. The examples in this specification and in
   `examples/` quote every such field, because writers are copied from examples.

   A source (a document with a top-level `system` block) is the one exception, for
   its string values only: a person writes and revises it line by line, and in a
   fully quoted line the names drown. Its strings go bare by a closed allowlist,
   never by "when needed": a value matching `[A-Za-z_][A-Za-z0-9_./-]*` whose
   lowercased form is none of `y`, `n`, `yes`, `no`, `true`, `false`, `on`, `off`,
   `null` is written bare, and everything else is quoted (`"d2d_f[0..2]"`,
   `"1.1"`, `"0755"`, `"2026-03-22"`, `"no"`). The concern above still holds,
   because both reader families agree on every allowlisted word: a word that starts
   with a letter or `_` is never a number, a date, `.inf` or a merge key to either,
   and the bool and null words are out in every case form, so a YAML 1.1 loader and
   yaml-cpp read each one as the same string. The allowlist is a pattern and a word
   list, not a model of any loader's tables, and the self-test is the one above. A
   source reader accepts a string bare or quoted, refuses a bare value outside the
   allowlist, and gives the line to write with it quoted
   (`conformance/fixtures/source_bare_strings_cases.json`). An assembly, and with it
   every `design.chiplet` an elaborator writes, keeps this rule unchanged.

8. A pad's `io_class` must allow the `type` of every interface it takes part in,
   per the normative table under [Usage class and interface
   type](#usage-class-and-interface-type-normative). For each interface and each of
   its endpoints, the endpoint's PAD SET is the inline `io_pads` of the endpoint's
   `component` whose `layer` equals the endpoint's `port_layer`; every pad in that
   set MUST carry an `io_class` whose row allows the interface's `type`. A violation
   is refused, naming the interface id, the pad id, the `io_class` and the `type`.
   An empty pad set is vacuously satisfied, and a pad whose `io_class` is outside
   the table is not judged here (the schema closes that vocabulary). Rule 8 covers
   the endpoint whose component carries inline io_pads (today the interposer); an
   endpoint without inline pads (a die) is not checked by this rule, by decision,
   until an explicit pad binding exists (SPEC-24). A wire_bond pad bound through a
   copper_pillar interface is therefore refused on the interposer side and not seen
   on the die side.

**Assembly stage (stated here, run by the hosts that hold the interconnect
manifest; the reference validators state this rule and do NOT run it):**

9. A component's interconnect method must agree with the `type` of every interface
   that component takes part in. For each `interfaces[]` entry and each endpoint
   whose `component` selects a stack through `connection:`, the method that id
   resolves to in the interconnect method registry (`interconnect_methods.json`,
   the single source of truth for the interconnect PDK) MUST declare an
   `interface_type` equal to the interface's `type`. An inconsistent binding is
   refused, naming the interface id, its `type`, the component id, the method id
   and the method's `interface_type`.
   The rule is CROSS-ARTIFACT: the answer is not in the `.chiplet`, so its owners
   are the hosts that hold both halves. The KiCad plugin refuses to WRITE an
   inconsistent binding at export, because the writer that creates a binding is
   the cheapest place to refuse one; adk-tools' assembly DRC, the Mosaic loader
   and chiplet-system-description validate at LOAD, so a document that arrived by another
   route is still caught. The two reference validators (`chiplet-format-io`,
   Python and C++) hold no manifest, so they state this rule without running it:
   a green from a reference reader says nothing about rule 9, and
   `conformance/fixtures/v1_0_interface_inconsistent_binding.chiplet` is the one
   oracle every assembly-stage host parity-tests its refusal against.
   Scope, next to the rule so a reader of the rule alone gets it: rule 9 checks
   the BINDING (component -> method -> interface type) and nothing behind it,
   neither the geometry of the stack nor the pads it lands on, and only for a
   component that carries `connection:`; a component without one mounts on the
   interposer's plane (rule 11) and there is no method to disagree with. It is
   independent of [rule 8](#validation-rules): rule 8 relates a pad's usage class
   to the interface type inside one document, rule 9 relates the method to the
   interface type across two artifacts, and a document can pass either one and
   fail the other.

**Reader behavior (warnings / fallbacks, not hard errors):**

10. New files SHOULD declare `anchor:` on every component; a reader defaults an absent `anchor` to `bbox_center` and warns.
11. If `connection` is specified it SHOULD name a known interconnect method; an absent or unresolved `connection` mounts on the interposer's `attachment_surface_z` with connection height 0 -- see the [coordinate-frame contract](./coord_frame_contract.md), section 3.
12. A reader MUST warn (or reject) when any `position.x` or `position.y` exceeds `1e5` um in magnitude (heuristic for un-converted absolute coordinates).

**Consumer / tool-level (NOT enforced by the reference library, which never touches the filesystem):**

13. Component `id` values are expected to be unique.
14. Component `type` is expected to be one of the canonical values `die`, `die_array`, `interposer`, `substrate`; the reference reader accepts any non-empty string, so other values are allowed but not recommended.
15. If `technology` is specified it should reference a defined technology id.
16. If `layout` or `layer_properties` is specified, the referenced file should exist at the resolved path.
17. An interposer that seats dies and declares no `attachment_surface_z` has no mount plane. A tool that places dies in z for another tool refuses the file; a viewer may show the dies on `dimensions.thickness`, with a warning naming the file. See the [coordinate-frame contract](./coord_frame_contract.md), section 3.4.

---

## Examples

### Minimal Valid File

```yaml
format_version: "1.0"

assembly:
  name: "Minimal Assembly"
```

### Complete Assembly

```yaml
format_version: "1.0"

assembly:
  name: "2.5D SoC Package"
  description: "HPC SoC with HBM3 memory"
  author: "Chiplet Team"
  created: "2024-06-01"
  units: "um"

technologies:
  logic_5nm:
    description: "5nm Logic Process"
    layer_properties: "./tech/logic_5nm.lyp"
    dbu: 0.001

  hbm_process:
    description: "HBM DRAM Process"
    layer_properties: "./tech/hbm.lyp"
    dbu: 0.001

  interposer:
    description: "65nm Interposer"
    layer_properties: "./tech/interposer.lyp"
    dbu: 0.001

components:
  - id: pkg_substrate
    type: substrate
    dimensions: {width: 55000, height: 55000, thickness: 1200}
    position: {x: 0, y: 0, z: 0}

  - id: si_interposer
    type: interposer
    technology: interposer
    layout: "./gds/interposer.gds"
    top_cell: "INTERPOSER"
    dimensions: {width: 45000, height: 45000, thickness: 100}
    position: {x: 5000, y: 5000, z: 1200}

  - id: soc_die
    type: die
    technology: logic_5nm
    layout: "./gds/soc.gds"
    top_cell: "SOC_TOP"
    dimensions: {width: 15000, height: 15000, thickness: 50}
    position: {x: 20000, y: 15000, z: 1300}
    metadata:
      power_tdp: "250W"
      io_count: "2048"

  - id: hbm_stack_0
    type: die_array
    technology: hbm_process
    layout: "./gds/hbm_die.gds"
    top_cell: "HBM_DIE"
    array:
      pattern: grid
      count: {x: 1, y: 4}
      pitch: {x: 0, y: 2500}
      start_position: {x: 8000, y: 20000, z: 1300}
    metadata:
      capacity: "16GB"
      bandwidth: "1TB/s"
```

---

## Software Template

Use this template to generate `.chiplet` files from your software:

```yaml
format_version: "1.0"

assembly:
  name: "<ASSEMBLY_NAME>"
  description: "<DESCRIPTION>"
  author: "<AUTHOR>"
  created: "<YYYY-MM-DD>"
  units: "um"

technologies:
  <TECH_ID>:
    description: "<TECH_DESCRIPTION>"
    layer_properties: "<PATH_TO_LYP>"
    dbu: <DBU_VALUE>

components:
  - id: "<COMPONENT_ID>"
    type: "<die|die_array|interposer|substrate>"
    technology: "<TECH_ID>"
    anchor: "<gds_origin|bbox_center>"
    orientation: "<face_up|flip_chip|face_down>"   # dies only; default face_up
    connection: "<INTERCONNECT_METHOD_ID|>"   # dies only; drives z-mounting
    layout: "<PATH_TO_GDS>"
    top_cell: "<CELL_NAME>"
    dimensions:
      width: <WIDTH_UM>
      height: <HEIGHT_UM>
      thickness: <THICKNESS_UM>
    position:                          # geometric center, canonical frame
      x: <X_UM>
      y: <Y_UM>
      z: <Z_UM>                        # 0 to defer to per-die auto z-mounting
    rotation:
      z: <ROTATION_DEG>
    metadata:
      <KEY>: "<VALUE>"
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2024-01 | Initial specification |
| 1.0 | 2026-06-19 | Documentation reconciled with the reference libraries and [`coord_frame_contract.md`](./coord_frame_contract.md). Documented previously-undocumented parts of the existing `format_version` 1.0 schema: `assembly.assembly_gds`/`io_technology`, component `anchor`/`orientation`/`connection`/`cells`/`io_pads`, geometric-center `position` semantics, the per-die z-mounting rule, the `_metadata` intermediate-file guard, and the top-level `connection_stacks`, `interconnect`, `interfaces`, `netlist`, and `flow` blocks. Reorganized Validation Rules into reference-enforced vs consumer-level. Added a "Scope and relationship to other standards" section (CDXML / OCP-ODSA / JEDEC JEP30) and a proposed, non-normative `cdxml_ref` extension. No on-disk format change; `format_version` stays `"1.0"`. |
| 1.0 | 2026-06-19 | Editorial pass: aligned the File Structure skeleton with the Root Level Keys table (one required `assembly` block plus optional blocks; ten recognized keys), documented the full `orientation` vocabulary (`face_up`/`flip_chip`/`face_down`, `face_up` default and writer suppression) to match the reference, and removed non-ASCII dashes. No normative change. |
| 1.0 | 2026-07-09 | Positioned `.chiplet` relative to 3Dblox / IEEE P3537 in the scope section (same physical-assembly layer; P&R vs mask abstraction; interop, not rivalry), added a proposed, non-normative `3dblox_ref` extension mirroring `cdxml_ref` (component-level only; no assembly-level `.3dbx` reference by design), and added the non-normative mapping appendix [`3dblox_interop.md`](./3dblox_interop.md). No on-disk format change; `format_version` stays `"1.0"`. |
| 1.0 | 2026-08-05 | Documented the optional technology `stackup` field: a path to a layer-stackup YAML the technology ships itself, resolved through the same `${VAR}`/relative chain as `layer_properties` and taking priority over a consumer's own stackup lookup for that technology id. The field was already read, written and relied on by Chiplet Studio; it had never been written down here, so the reference C++ reader dropped it on a round-trip while a consumer's vendored copy carried it. Updated the C++ reference (struct/parse/emit); the Python reader already passes it through. Backward compatible and optional; `format_version` stays `"1.0"`. |
| 1.0 | 2026-07-21 | Added the optional interposer `attachment_surface_z` field: the die-attachment (BEOL-top) mount plane, decoupled from `dimensions.thickness`, which now carries the physical substrate body (extending downward from the attachment surface). Backward compatible: consumers fall back to `dimensions.thickness` as the mount reference when the field is absent, so legacy files seat dies unchanged. Updated the reference readers (C++ struct/parse/emit; the Python reader already passes it through) and the canonical example. No on-disk format change; `format_version` stays `"1.0"`. |
| 1.0 | 2026-09-01 | Added the optional top-level `interposer` block (a single required `adapter`, the interposer-axis registry id), taking the root key count from ten to eleven. The block was already emitted by the KiCad exporter and read by the ADK DRC runner and the cockpit; it had never been written down here, so it was an undocumented root key travelling between three tools. Fixed the `adapter` value as a registry id, never a filesystem path (pattern, no `.drc` suffix), and stated the consumer rule: refuse when an adapter is needed and absent, never default silently. Added [`schemas/chiplet.schema.json`](../schemas/chiplet.schema.json), normative for structure, with the reference reader still normative for semantics; wired it into the conformance gate over the whole committed corpus, with the schema-vs-reader divergences pinned. Backward compatible and optional; `format_version` stays `"1.0"`. |
| 1.0 | 2026-09-04 | Defined the format's line-break set as LF and CRLF, and made ill-formed every character a YAML parser breaks a line on that this grammar does not: NEL (`U+0085`), `U+2028` and `U+2029` anywhere, and a CR (`U+000D`) not immediately followed by LF, end of file included. Both reference readers refuse such a document on the text before any YAML parse, with the refusal naming the code point and the line, and the set is derived by executing a parser over a code-point range rather than listed by hand. This is an INTENTIONAL behaviour change, not a regression: `name: demo<U+2028>trailing` loads today in yaml-cpp 0.8.0 and stops loading after this release, and a consumer meets that through its next vendoring bump. It is a clarification rather than a MAJOR because the format had never defined its line-break set, so those bytes were never legal; yaml-cpp accepting them was implementation behaviour that PyYAML already refused on the same bytes; and no conforming producer can write one, since the writer rule escapes them and the escaped form carries any value that needs one (measured: all 19 `.chiplet` here dumped through the writer before and after the rule give zero differing files). The sweeps corroborate rather than establish that, and they bound a corpus of committed machine-written output: zero occurrences across every `.chiplet` outside build trees plus the YAML and stackups shipped with them, zero in the KiCad plugin's 187 `.chiplet`, and zero lone CR across 183 `.chiplet` with build trees included. Added the matching writer rule (escape them inside a double-quoted scalar) and fixed the Python reference writer, which emitted them raw into a single-quoted scalar and then folded them on the next read, so the value did not survive its own round trip. No document shape changed and `format_version` stays `"1.0"`. |
| 1.0 | 2026-09-04 | Stated who enforces a closed vocabulary, and corrected three sentences that said the wrong thing. A closed vocabulary (component `anchor`, component `orientation`, `interfaces[].type`, `io_pads[].io_class`) binds WRITERS and is enforced by [`schemas/chiplet.schema.json`](../schemas/chiplet.schema.json); the reference readers carry every one of them as the string the document wrote, report an unrecognised member on their warn channel, and refuse nothing over it, so a consumer that cannot act on a member refuses the ELEMENT that carries it. That is what keeps an addition to one of these lists a MINOR: a reader refusing the DOCUMENT would make every future addition a MAJOR for everyone downstream. Rule 4 accordingly drops the clause about refusing an unlisted type and moves TIER in the C++ reference, from the parser to the validator, which is what its own text under "Enforced by the reference validator" always said; it had lived in `parse_interface`, where `LoadOptions::validate = false` did not reach it, so the two readers loaded different documents from one file. The prose list of closed vocabularies gains `io_pads[].io_class` (it closed four, the prose named three), and the gloss saying the reference readers accepted `solder_bump` before the 1.1 stamp is gone: it stated a prohibition in terms of what a reader knows rather than what a document may carry. The `solder_bump` format MINOR is untouched and still owed at 1.1. Reader release 1.1.0 to 1.2.0; no document shape changed and `format_version` stays `"1.0"`. |
| 1.0 | 2026-09-04 | Defined the anchor of a `die_array`, which the format had never stated (SPEC-30). `anchor` applies PER ELEMENT: `array.start_position` places the FIRST element's anchor point and every element is placed by that same anchor, so a `die_array` has one definition of `anchor` and its effect does not depend on the component's type. The gap was live rather than theoretical: the frame contract, which this document names as the source of truth for the anchor convention, defined the anchor for a component's own mesh and never mentioned `die_array` or `start_position`, so both readings of `start_position` were admissible and consumers had silently picked one, with three interposer-pnr tests declaring `gds_origin` on an array and then asserting the centre reading. A MINOR clarification that reinterprets zero existing documents: exactly two `die_array` components exist across the ecosystem, and the other declares no anchor, so `conformance/fixtures/v1_0_all_blocks.chiplet` moves from `gds_origin` to `bbox_center` in the same commit rather than have its meaning restated after the fact. No document shape changed and `format_version` stays `"1.0"`. |
| 1.0 | 2026-11-09 | Withdrew the stand-ins for a missing interposer `attachment_surface_z` that the 2026-07-21 row introduced: neither `dimensions.thickness` nor a technology stackup's plane is a mount plane any more. An interposer that seats dies without the field has none; an exporter refuses the file and a viewer may show the dies on `thickness` with a warning (rule 17; coordinate-frame contract, section 3.4). The field stays optional and the reference readers do not change; `format_version` stays `"1.0"`. |
| 1.1 | 2026-10-18 | Added the optional top-level `system` block, taking the root key count from eleven to twelve, and with it a second kind of document: a SOURCE, which names dies, placements and wiring and from which a tool derives the assembly (see [System](#system-format-11)). A source is stamped `"1.1"`, carries `_metadata.finalize_required: true` and none of the derived blocks; an assembly is still stamped `"1.0"`, because a writer stamps the lowest minor its content needs. Every assembly read path in both reference readers refuses a source with `SourceDocumentError`, whatever `allow_intermediate` or `validate` say; the Python reference reads one only under `allow_source=True`. Readers now implement 1.1, so a `"1.1"` document reads without a warning. In the same minor: each closed-vocabulary member added after 1.0 carries the minor that added it (`x-since`), `solder_bump` is format 1.1, and `assembly.units` is closed to `um`, its default when absent, with any other value refused. Reader release 1.14.0. |
| 1.2 | 2026-10-26 | Added the optional `volts` key to a `power` rail of the `system` block: the rail's nominal supply, a positive number in volts, refused on a `ground` rail, which is 0 by its class. It is the first source key added after 1.1, so each such key now carries the minor that added it as schema data (`x-since` on the property, `SOURCE_KEY_SINCE`): a source stamped below it is refused, and a writer stamps a source that writes `volts` `"1.2"` and one that writes none `"1.1"`. A source reader reading a source above its own minor now skips an unknown item key and reports it, as this policy always promised; at or below its minor the key is still refused. Readers implement 1.2, so a `"1.2"` document reads without a warning and `"1.3"` is the first that warns. No assembly key changed; an assembly is still stamped `"1.0"`. Reader release 1.15.0. |
| 1.3 | 2026-10-26 | Added the unplaced source, so a source drawn on a schematic sheet is a `.chiplet` before anything is placed: a die may omit `at` and `rotation` (together; `rotation` alone is refused at every minor), the carrier may omit `size`, `external` may omit `edges`, `system.dies` may be absent or empty, and the new root key `unplaced: {pins: [...]}`, written after `system`, holds the external entries no edge carries yet. Each is refused in a source stamped below 1.3. A writer stamps `"1.3"` only when the content needs it, and drops back once placement has filled everything in. The source reader reads it; tier 1 refuses it unless the caller opts in, `check` opts in, and the elaborator always refuses it. `unplaced` is a root key so that a reader before 1.3 refuses the file rather than reading on without those externals. The schema relaxes the four requirements from 1.3 on and keeps them below. No assembly key changed. Both reference readers implement 1.3, so a `"1.3"` document reads without a warning and `"1.4"` is the first that warns; the Python reference holds what a source may leave open by its stamp (`SOURCE_KEY_SINCE` gains `unplaced`). Reader release 1.16.0. |
| 1.4 | 2026-10-29 | Added the optional `admission` key to a `dies` item of the `system` block: a relative `.json` naming the die's admission, for a die the run does not build. The file holds `reason`, `scope` and `not_verified` (see [Format 1.4](#format-14-a-dies-admission)); the format has no key for who admits the die. The key carries its minor as schema data (`x-since` on the property, `SOURCE_KEY_SINCE`), so a source stamped below 1.4 that writes it is refused, and a writer stamps a source that writes it `"1.4"`; the schema states the same as an `allOf` branch on the stamp. Tier 1 of the source reader reads the file and refuses an absent or malformed one on the die's line. A source reader before 1.4 skips the key with a note, which fails closed: the die is not admitted. Readers implement 1.4, so a `"1.4"` document reads without a warning and `"1.5"` is the first that warns. No assembly key changed; an assembly is still stamped `"1.0"`. Reader release 1.18.0. |
| 1.5 | 2026-10-30 | Added the optional routing requirement of a rail of the `system` block, on either class: `width_um`, or `amps` with `current_density_ma_per_um` (see [Format 1.5](#format-15-a-rails-routing-requirement)), written after `volts` and before `ports`. `amps` without the density, the density without `amps`, `width_um` beside either, and a value that is not a positive finite number are refused at every minor that has the keys. Each key carries its minor as schema data (`x-since` on the property, `SOURCE_KEY_SINCE`), so a source stamped below 1.5 that writes one is refused, and a writer stamps a source that writes one `"1.5"`; the schema states the same as an `allOf` branch on the stamp. The width a rail is routed at, its rounding and the cap of each layer are the router's; the format holds none of them. A source reader before 1.5 skips the keys with a note, which would route the rail narrow, so the consumer that routes from the source refuses such a reading. Readers implement 1.5, so a `"1.5"` document reads without a warning and `"1.6"` is the first that warns. No assembly key changed; an assembly is still stamped `"1.0"`. Reader release 1.20.0. |
