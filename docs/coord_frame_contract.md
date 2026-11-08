<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 IHP GmbH
-->

# Coordinate Frame Contract, Chiplet Pipeline

**Status:** canonical, part of the chiplet-system-spec format definition.
**Companion:** [`CHIPLET_FORMAT_SPEC.md`](./CHIPLET_FORMAT_SPEC.md)
(general schema; this doc adds frame and anchor semantics).
**Worked example:** [`examples/interposer_demo_design.chiplet`](../examples/interposer_demo_design.chiplet),
a self-contained instance of the frame, anchor, and z conventions
described here (mirrors sections 2.3 and 3.2).
**Reference libraries:** the repo-local, dependency-clean reference
reader/writer is `chiplet-format-io` (`reference/python/chiplet_format_io`,
`reference/cpp/`, Apache-2.0). These libraries are deliberately
**structural-only**: they parse, validate, and round-trip the schema (the
`format_version` gate, the intermediate-file guard, structural shape) and keep
`anchor:` as a raw string, but they do **not** implement the anchor defaulting,
the 1e5 range check, mesh centering, or z-mounting. Those frame semantics are a
consumer responsibility; this contract defines them, and the chiplet-studio
reference implementation, over a vendored copy of the C++ library, demonstrates
them. The writers are KiCad's Export Chiplet action, the chiplet_kicad_plugin
finalizer (`hyp_to_gds`), and `gds_to_kicad`. Tool names below illustrate how
the contract is met; they are not part of the format itself.

The problem this contract solves: a `.chiplet` file carries XY positions
that originate in several different coordinate frames (KiCad PCB, Hyperlynx,
GDS), and a die placed in the wrong frame lands tens or hundreds of microns
off its pads. Six alignment incidents traced back to exactly this. This
document pins one canonical frame, makes every writer convert into it, and
makes every reader fail loudly when something leaks through un-converted.

Any tool may implement this contract under any license; it is the
format-level companion to the `.chiplet` schema, not specific to one
implementation. In the reference toolchain chiplet-studio's contract tests
are the regression net that proves the behavior described here.

---

## 0. TL;DR

1. All `position:` values in a `.chiplet` file are expressed in
   **GDS-bbox-corner of the interposer top_cell**, y-up cartesian,
   units micrometers.
2. `position:` places the component's **anchor reference point**
   (item 3, section 2.1): the center of its GDS bbox for
   `anchor: bbox_center`, its own GDS (0,0) for `anchor: gds_origin`.
   It is the geometric center only in the `bbox_center` case.
3. Each component declares an explicit `anchor:` field:
   - `anchor: gds_origin`, the component mesh is built around its own
     GDS (0,0). Used by dies produced by `gds_to_kicad`.
   - `anchor: bbox_center`, the component mesh is centered on its own
     GDS bounding box. Used by interposers.
4. Z-mounting for dies on connection stacks is fixed by the formula
   in section 3. It is **per-die**: each die's `connection:` selects its
   own bump/pillar bodies, so one assembly can mix methods (section 3.2).
5. `hyp_to_gds --update-chiplet-file` is **mandatory** in the
   canonical path. KiCad's `pcbnew` GUI export produces an
   intermediate `.chiplet` whose positions live in the wrong frame
   (PCB-bbox-corner) and is not directly consumable by chiplet-studio.
6. Interposer `dimensions:` are the **board outline** (prBoundary
   235/0, drawn from KiCad Edge.Cuts) when present in the GDS;
   `position:` stays the full-GDS-bbox center (section 1.5).

Any tool that writes a `.chiplet` file MUST conform to this contract.
Any tool that reads one MUST validate that the contract is followed
(or fail loudly).

---

## 1. Canonical Coordinate Frame

### 1.1 Definition

The canonical frame for `.chiplet` `position:` values is:

| Property | Value |
|---|---|
| Reference object | Interposer's GDS top_cell |
| Origin (0, 0) | Lower-left corner of the interposer's GDS bbox |
| X axis | Increases rightward |
| Y axis | Increases upward (y-up cartesian) |
| Units | Micrometers (um) |
| Float precision | At least 6 decimal places (1 pm theoretical) |

### 1.2 Why GDS-bbox-corner

- The GDS file is the physical fabrication artifact. The interposer
  GDS is the ground truth of the layout.
- KLayout, the 3D scene in chiplet-studio, and any downstream
  packaging tool all consume positions as offsets within the GDS
  bbox.
- The PCB Edge.Cuts bounding box (which KiCad uses natively) **does
  not always match** the GDS bbox. Historically the wire-bond demo
  carried a hidden shift of (-200 um, -780 um) between the two frames
  even though widths and heights agreed to the um. The GDS frame is
  the only one with no such hidden shift relative to what gets
  fabricated.
- Since the converter draws the board outline (Edge.Cuts to prBoundary
  235/0) into the interposer GDS, the GDS bbox *contains* the outline.
  When all drawn geometry sits inside the outline, the normal case,
  the canonical origin coincides with the board outline's lower-left
  corner, and the historical shift above is zero by construction.

### 1.3 Diagram (top-down view)

```
                                  +--------------------------+
                                  |  GDS bbox of interposer  |
                                  |     (the reference)      |
                                  |                          |
                                  |    (die center)          |
                                  |        +                 |
                                  |       /                  |
                                  |      / position.y        |
                                  |     /                    |
                                  | (0,0)----- position.x    |
                                  +--------------------------+
                                  ^
                                  |
                          GDS-bbox-corner = canonical origin
```

`(0,0)` is the lower-left of the interposer's GDS bbox. Every
`position:` x and y in the .chiplet is measured from this corner,
y-up, in um.

### 1.4 Position semantics

`position:` places the component's **anchor reference point** in X and Y
(section 2.1). For `anchor: bbox_center` that is the component's geometric
center; for `anchor: gds_origin` it is the component's own GDS (0,0),
wherever that origin sits in the artwork.

In Z, `position.z` is the component's **seating plane**: the bottom
face of the placed body, the one that meets the mounting surface (for
dies on a connection stack, the tip of that stack; see section 3).
`dimensions.thickness` extends the body upward from `position.z`.
Files that predate real thickness data carry the placeholder
`thickness: 0.0`, where the center and seating-plane readings
coincide; with a real thickness they differ by `thickness / 2`, and
the seating-plane reading is the normative one: it is what the
z-mounting rule computes and what consumers implement.

For a `bbox_center` die of width 1000 um and height 2000 um placed with
its lower-left corner at (250, 250) inside the interposer:
```yaml
position:
  x: 750.0      # 250 + 1000/2
  y: 1250.0     # 250 + 2000/2
  z: 54.8103    # seating plane; see section 3 for Z mounting
dimensions:
  width: 1000.0
  height: 2000.0
  thickness: 50.0   # body spans z 54.8103 to 104.8103
```

### 1.5 Interposer dimensions vs. position

The two fields of the interposer component answer different
questions and have different sources:

| Field | Source | Meaning |
|---|---|---|
| `dimensions: width/height` | bbox of prBoundary 235/0 (the board outline, drawn from KiCad Edge.Cuts) when the layer is present; bbox of all drawn geometry otherwise (legacy GDS) | The fab extent of the interposer, what viewers render as the substrate body |
| `dimensions: thickness` | KiCad stackup (`GetBoardThickness`) | The interposer's **physical body** z-extent (Si substrate + BEOL). It extends **downward** from `attachment_surface_z`: the die-mount plane is the top surface and the substrate body sits below it (local z goes negative). Legacy files without `attachment_surface_z` overload this field as the mount reference. |
| `attachment_surface_z` | the interposer PDK's key `beol.attachment_surface_z_um` (10.8103 for IntM4TM2: the TopMetal2 top of the interposer's own BEOL, Metal4 on a 0.64 um base oxide) | The die-attachment (BEOL-top) surface z in the interposer-local frame: the plane dies mount on, `z_die = attachment_surface_z + connection height` (section 3). Optional; when absent, consumers fall back to `dimensions.thickness` as the mount reference. |
| `position: x/y` | half of the **full** GDS bbox (all layers, outline included) | Where the mesh bbox center sits in the canonical frame (`anchor: bbox_center`, section 2) |

When the outline contains all drawn geometry, the full bbox equals
the outline bbox and both fields describe the same rectangle. When
copper leaks outside the outline (a design error; the converter
warns loudly at export), `dimensions` keeps the true board size
while `position` follows the mesh center, preserving die/pillar
registry in the render at the cost of a shifted substrate body.

A z derived from the plane (a die's `position.z`, a wire's start or end z)
is written as the exact decimal sum of the plane and the height as
written, emitted as the shortest text that reads back as that sum:
10.8103 + 18 is written `28.8103`, never `28.810299999999998`. A writer
sums in decimal, not in binary floating point, so a height with more
decimals than the plane keeps all of them.

The reader uses `position` and `dimensions` together to compute the
3D world placement (see section 5).

---

## 2. Anchor Convention

### 2.1 The `anchor:` field

Each component declares how its local mesh is built:

| Value | Meaning | Use cases |
|---|---|---|
| `gds_origin` | The component mesh is built around its own GDS (0, 0). The `position:` value is added to the GDS-origin of the cell, no extra centering. | Dies produced by `gds_to_kicad` (footprint anchor at GDS (0,0)). Any die where the layout author put the cell origin where the placement reference should be. |
| `bbox_center` | The component mesh is centered on its own GDS bounding box. `position:` places the bbox center. | Interposers. Components whose GDS layout uses absolute pcbnew-derived coordinates and whose meaningful "placement reference" is the geometric center. |

The anchor is **declared by the writer** based on knowledge of how
the component's GDS was produced. The reader does **not** infer it
from `ComponentType`.

The anchor applies **per element**, and that sentence is the whole rule. For an
ordinary component there is one element and `position:` places its anchor point.
For a `die_array` there are `count.x * count.y` elements of the same cell:
`array.start_position` places the FIRST element's anchor point, and every element
is placed by the SAME anchor, `pitch.x` / `pitch.y` apart. So a `die_array` with
`anchor: gds_origin` has its first element's GDS (0, 0) at `start_position` and
the element at index (i, j) has its GDS (0, 0) at
`start_position + (i * pitch.x, j * pitch.y)`; with `anchor: bbox_center` the
same is true of each element's bbox center.

`start_position` is the same `position3d` as `position` (the schema declares it
by `$ref` to that definition), and the rule above attaches to that definition,
which is why it needs no separate table row. It is also why the anchor's meaning
must NOT depend on the component's type: a field whose effect changes with the
type it sits on is the defect this contract exists to prevent, and `anchor` is
the field the readers agree on least when it does.

### 2.2 Default behavior

If `anchor:` is absent:
- The reader must default to `bbox_center` and emit a warning.
- This is for legacy `.chiplet` files only; there is no
  auto-migration.
- New files MUST declare `anchor:` explicitly.

### 2.3 Example (from the wire-bond demo)

The canonical demo interposer uses `intm4tm2` (the IHP IntM4TM2
interposer stackup; `configs/stackups/intm4tm2.yaml`). Numbers below are
the live values from
`kicad_designs/interposer_wire_bonding_demo/interposer_wire_bonding_demo.chiplet`.

```yaml
components:
  - id: interposer
    type: interposer
    technology: intm4tm2
    anchor: bbox_center
    layout: interposer_wire_bonding_demo_interposer.gds
    top_cell: INTERPOSER
    position:
      x: 3246.156      # half of full GDS bbox width (= outline width
      y: 2801.000      #   when all geometry is on-board, see section 1.5)
      z: 0.0
    dimensions:
      width: 6492.312  # board outline (prBoundary 235/0), see section 1.5
      height: 5602.001
      thickness: 300.0    # physical interposer body (KiCad stackup); see section 1.5
    attachment_surface_z: 10.8103 # BEOL-top die-mount plane; see sections 1.5, 3

  - id: U1
    type: die
    technology: sg13g2
    anchor: gds_origin
    connection: cupillar_opt1   # per-die method; see section 3.2
    orientation: flip_chip
    layout: ${GDS_TO_KICAD_ROOT}/.../Metal_Test.gds
    top_cell: Metal_Test
    position:
      x: 1954.121     # where the die's GDS (0,0) lands (anchor: gds_origin)
      y: 2332.483
      z: 54.8103      # 10.8103 + 44 (cupillar_opt1), see section 3
    dimensions:
      width: 730.0
      height: 2566.339
      thickness: 750.0   # physical die body (Si bulk), see section 1.4
```

### 2.4 Orientation vocabulary

A component's `orientation:` field takes exactly two canonical values:

| Value | Meaning |
|---|---|
| `face_up` (default) | The die artwork is used as drawn; no mirror. Applied before `rotation.z`. |
| `flip_chip` | The die is mounted face-down onto the interposer; realized as an **x-mirror** of the die artwork (`mx = -1`), applied before `rotation.z`. |

`face_up` is the default when the field is absent. **`face_down` is not a
canonical token**: flip-chip mounting is expressed as `flip_chip` (the mirror
is the observable contract; "face down" is the physical picture, not the field
value). Writers MUST emit only `face_up`/`flip_chip`.

Reader obligations on a non-canonical or unknown `orientation:` token:

- **Validators and exporters** (e.g. the ADK `pads_vs_pillars` check and the
  `chiplet2dbx` interop exporter) MUST reject it — an ambiguous token must
  never silently yield wrong geometry.
- **Interactive viewers** MAY be lenient (keep rendering) but MUST warn; they
  MAY treat `face_down` as `flip_chip`. A viewer must never silently render an
  unknown token as `face_up` (un-mirrored).

---

## 3. Z-Mounting Rule

### 3.1 Formula

For dies that mount on a connection stack (cu-pillar, solder bump,
microbump):

```
z_die = mounting_surface + connection.total_height()
```

where:
- `mounting_surface` = `z_bottom` of the interposer stackup layer
  whose name matches the connection stack's first layer, after the
  interconnect-PDK fragment for this die's `connection:` is merged in
  (see section 3.2).
- `connection.total_height()` = sum of `height` for every layer in
  the connection stack.

The mounting plane of a file is its interposer's `attachment_surface_z`
when the file declares one. The interconnect fragment is rebased on that
value, not on the technology stackup's. The technology stackup's
`attachment_surface_z` applies only when the file declares none, and
`dimensions.thickness` only when neither exists (section 3.4). For
`technology: intm4tm2` the declared value must equal the interposer PDK's
`beol.attachment_surface_z_um`; a file whose value differs is flagged
(section 3.5).

### 3.2 Worked example, two-die mixed-method demo

The canonical demo is a **two-die, mixed-method** assembly: U1 seats on an
IHP cu-pillar stack and U2 on a non-IHP vendor microbump, both finalized in
one export. The method is **per-die**: each die's `connection:` id selects
its own interconnect-PDK fragment, so the dies seat at different heights from
the same `calculate_component_z`.

Interposer technology: `intm4tm2`. The file declares
`attachment_surface_z: 10.8103`, the interposer PDK's
`beol.attachment_surface_z_um` (the TopMetal2 top through the passivation
opening), and the technology stackup carries the same value; by section
3.1 the file's value is the one used.

How the mounting surface resolves (chiplet-studio's z calculation):

```
1. Take this die's connection id, e.g. cupillar_opt1.
2. Load the interposer stackup (intm4tm2) and MERGE the interconnect
   fragment for that connection id
   (interconnect_pdk/libs.tech/chiplet_studio/stackup_fragments/
    cupillar_opt1.stackup.yaml). The fragment declares
    z_reference: attachment_surface, so its CuPillar layer (local z 0.00)
    is offset by the file's attachment_surface_z (10.8103). After merge,
    CuPillar.z_bottom = 10.8103.
3. firstLayerName = connection.layers[0].name = "CuPillar". Look it up
   in the merged stackup. FOUND. mounting_surface = 10.8103.
4. z_die = 10.8103 + total_height(cupillar_opt1).
```

Per-die totals in the demo:

| Die | connection | stack layers | total_height | z_die |
|---|---|---|---|---|
| U1 | `cupillar_opt1` (IHP cu-pillar) | CuPillar 28 + SnAgCap 16 | 44 | 54.8103 |
| U2 | `vendorx_microbump` (non-IHP) | VendorXBumpCu 18 + VendorXBumpCap 6 | 24 | 34.8103 |

U1 selects Option 1 (not Option 2) because its pad ring has a 79.93 um-pitch
pair, legal at Option 1's 75 um minimum but below Option 2's 80 um. That
selection lives in the die's footprint `CONNECTION` field and rides through
the export; the contract tests `U1Position` / `U2Position` are the regression
witness.

### 3.3 Reference implementation

chiplet-studio's z calculation implements this rule. It merges each die's own
interconnect fragment before it looks up the first layer, so a die using any
method seats on that method's body heights even when sibling dies use other
methods. The mounting surface is the z_bottom of the chosen connection stack's
first layer (the layer that attaches to the interposer pad, e.g. CuPillar for
cu-pillar stacks); after the merge it resolves to `attachment_surface_z`, and
adding the stack's total height lands the die on the tip of the connection.

### 3.4 Edge cases (must be handled by reader)

| Case | Behavior |
|---|---|
| Die has no `connection:` field | **Fallback**: mount on the file's plane, else the stackup's, else thickness (section 3.1), `connection_height = 0`. |
| `connection` id not a defined connection stack | **Fallback**: mount on the plane of section 3.1, `connection_height = 0`. |
| Connection's first layer not in interposer stackup (even after merge) | **Fallback**: mount on the plane of section 3.1. |
| `interposer.attachment_surface_z` absent (legacy file) | **Fallback**: use `interposer.dimensions.thickness` as the mount reference (the pre-split behavior, when thickness doubled as the attachment surface). |
| Die has `position.z` explicitly set non-zero | Use the explicit value, do not auto-calculate. |

The mount reference is `attachment_surface_z` when the interposer declares it,
falling back to `dimensions.thickness` for files written before the field
existed -- so both eras seat dies on the BEOL top, not on world z=0 (the
earlier `0.0` return was a bug that put dies at the origin) and not on the
substrate bottom (which is what reading the physical `thickness` would now
give). The fallback keeps the formula valid in all cases.

### 3.5 Plane check (intm4tm2)

A check reads the interposer PDK's key (`beol.attachment_surface_z_um` in
`libs.tech/intm4tm2_stack.json`) and every committed `.chiplet` whose
interposer declares `technology: intm4tm2`. It fails, naming the file and
line, when the file's `attachment_surface_z` differs from the key, or when a
die with a `connection:` has a non-zero `position.z` other than the plane
plus that connection's total height, written as section 1.5 states. It runs
in adk-tools CI and in the image's verify stage, the only place where the
PDK sits beside every consumer. The reference readers in this repository do
not run it, since they hold no PDK; a viewer may report the same mismatch
when it opens a file, but that report is not the check. Conformance
fixtures are format data, not designs: they may hold other values, and
the check does not read them.

---

## 4. Writer Contract

Every tool that writes a `.chiplet` file MUST:
1. Express all `position:` x, y in the canonical frame (section 1).
2. Place each component's anchor reference point at `position:`
   (section 1.4).
3. Declare `anchor:` explicitly per component (section 2).
4. Set `z` to either the explicit user value, 0 to defer to
   auto-calc, or the auto-calculated value per section 3.

---

## 5. Reader Contract

Every tool that reads a `.chiplet` file MUST:
1. Parse `anchor:` and store it on the component.
2. Use `anchor:` (not `ComponentType`) to drive mesh centering.
3. Validate position values are in plausible range; warn loudly if
   any `position.x` or `position.y` exceeds 1e5 um (heuristic
   indicator of HYP-absolute leakage).
4. Apply the tolerant `format_version` policy, not exact-string equality
   (`chiplet_format_io.check_format_version` is the single reference): accept a
   same-major minor at or below the supported one; accept a same-major higher
   minor with a warning; reject a different major or a malformed value. The
   baseline stays `"1.0"`. A lossless passthrough writer re-emits the version it
   was handed (preserving a higher minor); a struct-model (lossy) writer stamps
   the supported version.

The repo-local reference libraries (`reference/cpp`, `reference/python`) do
**not** satisfy these reader requirements on their own: they are structural-only
(see the header), so they parse and round-trip `anchor:` as a raw string but
perform no anchor defaulting, no 1e5 range check, and no z-mounting. A reader
that needs the full contract must add the frame semantics on top, as the
`chiplet-studio` reference implementation does.

---

## 6. io_pads Convention

### 6.1 Frame

io_pads `position:` lives in the same canonical frame as components
(GDS-bbox-corner of the interposer top_cell, geometric center of the
pad, um). io_pads are nested under their interposer in the schema and
inherit the interposer's frame. They do **not** declare an `anchor:`
field; they are points (size is 2D extent, not a centering rule).

### 6.2 Validation

io_pads must be re-anchored to the canonical frame by the finalizer
(the writer that owns the interposer GDS bbox). A reader MUST warn
(or reject) when `|position.x|` or `|position.y|` exceeds 1e5 um: the interposer GDS bbox is on the
order of a few thousand um, so values that large indicate
HYP-absolute coordinates leaking through un-converted. io_pads are
stored as metadata and not currently rendered in 3D, but their
positions must still be canonical for any downstream consumer.

---

## Appendix A, Glossary of Coordinate Frames

The frames present in the toolchain:

| # | Frame | Origin | Used by |
|---|---|---|---|
| 1 | KiCad PCB internal nm | KiCad's signed int IU (nm) | KiCad core; not exposed to .chiplet |
| 2 | PCB-bbox-corner | Lower-left of `BoardEdgesBoundingBox` (or fallback) | KiCad Export Chiplet for dies and io_pads |
| 3 | PCB-bbox-center | (PCB_w/2, PCB_h/2) | KiCad Export Chiplet for the interposer |
| 4 | HYP absolute | Hyperlynx file native (meters, KiCad y-down convention pre-conversion) | `hyp_to_gds` input parsing |
| 5 | GDS absolute | GDS file native (um, y-up cartesian) | KLayout, `hyp_to_gds` for cell instance placement |
| 6 | **GDS-bbox-corner** (canonical) | Lower-left of interposer GDS bbox | **THIS CONTRACT.** Also the finalizer for dies. |
| 7 | GDS-bbox-center | (GDS_w/2, GDS_h/2) | The finalizer for the interposer |
| 8 | Interposer-local-corner | The frame components mean to be in (per the schema's io_pad position) | The schema's intent. Now formalized as = #6. |
| 9 | Chiplet-studio 3D world | OpenGL world (mm, y-up, z-out-of-screen) | chiplet-studio final placement |

From the schema's perspective only #6 (canonical) and #9 (3D world)
are visible. The rest are internal to specific tools and never appear
in `.chiplet` files.

---

## References

- [`CHIPLET_FORMAT_SPEC.md`](./CHIPLET_FORMAT_SPEC.md), general
  schema (companion document; this doc adds frame and anchor
  semantics).
- [`examples/interposer_demo_design.chiplet`](../examples/interposer_demo_design.chiplet),
  the canonical worked example: a runnable two-die mixed-method
  assembly whose positions, anchors, and z values mirror sections 2.3
  and 3.2 of this contract.
- Reference reader/writer library: `reference/python/` and
  `reference/cpp/` (chiplet-format-io, Apache-2.0). chiplet-studio
  consumes a vendored copy of the C++ library. These libraries are
  structural-only; see the header and section 5 for the consumer/library
  split on frame semantics.

---

## History

This document's changes are recorded in the repository history and, where they touch the format,
in [`VERSION_POLICY.md`](./VERSION_POLICY.md).
