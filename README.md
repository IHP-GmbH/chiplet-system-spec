<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 IHP GmbH
-->

# chiplet-system-spec

Open specifications for the heterogeneous-integration / chiplet-assembly interchange
formats developed at IHP; chiefly the **`.chiplet`** assembly format and the sidecar
manifests and vocabularies that travel with it.

This repository is the **canonical, neutral home** for the formats, deliberately kept
**separate from any implementing tool**. The reference tools that read and write these
formats (the KiCad fork, the Chiplet Studio viewer, the KiCad plugin) are GPL-licensed
because they link/derive from GPL software (KiCad, KLayout). **The formats themselves
are not**: they are published here under the permissive **Apache License 2.0** so that
anyone, open-source or commercial, can build independent implementations.

## What's here

| Path | Contents |
|------|----------|
| `docs/CHIPLET_FORMAT_SPEC.md` | The `.chiplet` YAML assembly format (v1.0). |
| `docs/coord_frame_contract.md` | Canonical coordinate-frame, anchor, and z-mounting contract for `.chiplet` positions; every writer and reader must obey it. |
| `docs/3dblox_interop.md` | Non-normative field-by-field mapping between `.chiplet` and 3Dblox / IEEE P3537. |
| `schemas/*.schema.json` | Machine-readable JSON Schemas (boundary manifest, interconnect methods, layers, rule params, interconnect rules). |
| `schemas/chiplet_pads.json` | Pad-layer vocabulary for black-box chiplets (GDS 205/0, 205/25, 206/0). |
| `examples/` | Sample `.chiplet`, `*.boundaries.json`, and `interconnect_methods.json`. |
| `reference/python/`, `reference/cpp/` | Dependency-clean reference reader/writer libraries (`chiplet-format-io`): Python (PyYAML only) and C++ (yaml-cpp only). Apache-2.0, with no GPL/Qt/KLayout dependency, so they embed in tools under any license. |

Only **format-level** artifacts live here. Tool-specific *implementation* contracts, for
example how Chiplet Studio merges stackup fragments and renders 3D bodies, or what inputs
an ADK DRC adapter must declare, stay with their respective tools (chiplet-studio, adk),
since they reference internal class names and CLI surfaces rather than the format itself.

## Scope and relationship to other standards

`.chiplet` is a **physical-assembly layout** format: it places dies, interposers,
and substrates in one shared coordinate frame, z-mounts each die on its chosen
interconnect, and feeds an assembly DRC flow. Two other format families touch
this space: one sits on a different layer of the stack, one on the same layer
at a different abstraction level.

**Part description (different layer) - CDXML / JEDEC JEP30.** `.chiplet` does
**not** describe a chiplet as IP; no electrical, functional, power, thermal,
PHY/D2D-protocol, or test characterization. That is the part-description
layer, where standards already exist:

- **CDXML** (Chiplet Data Exchange in XML), the per-chiplet datasheet: pinout,
  mechanical envelope, electrical/ESD ratings, D2D interface type. It was
  developed in **OCP / ODSA** (published under CC0-1.0); its capabilities were
  folded into **JEDEC's JEP30** PartModel, a separate JEDEC standard that is now
  the active vehicle for this part-model data. By its own scope CDXML carries no
  inter-die placement (its only coordinates are pad positions *within* a single
  part).

These are **complementary layers, not competitors**: CDXML / JEP30 describes
*what a part is*; `.chiplet` describes *where it is placed, how it is z-mounted,
and how the assembly is verified*. The intended pipeline is **select (CDXML / JEP30)
-> place (`.chiplet`) -> assembly DRC**. A `.chiplet` `die` may carry an optional,
*proposed* `cdxml_ref` (documented as an extension; not part of the v1.0 schema)
citing the part it instantiates, so an assembly *references* part data rather than
duplicating it (see
[`docs/CHIPLET_FORMAT_SPEC.md`](docs/CHIPLET_FORMAT_SPEC.md)).

**Assembly description (same layer, different abstraction level) - 3Dblox /
IEEE P3537.** **3Dblox**, originated by TSMC and being standardized as
**IEEE P3537**, also describes multi-die physical assemblies (per-die
technology, 3D placement and orientation, bond regions, per-bump maps) and
has an open-source (BSD-3) implementation in OpenROAD (ingestion, an
automatic assembly linter, a 3D viewer). The two formats overlap on the
assembly core (multi-die placement, z, thickness, flip, per-die technology)
and diverge in what they bind that assembly to:

- **3Dblox** binds it to the **P&R abstraction** (LEF/DEF, Verilog netlists,
  Liberty views) for multi-die EDA flows and design-space exploration. It
  references no artwork files.
- **`.chiplet`** binds it to the **mask level**: GDS/OASIS bodies with `.lyp`
  layer properties, per-layer interconnect metallurgy with method identity and
  provenance, polygonal boundary manifests, and the fab DRC parameters an
  assembly-signoff flow consumes.

An exploration-level and a signoff-level model of the same assembly are both
useful, so the intended relationship is **interop, not rivalry**: the spec
documents a *proposed* component-level `3dblox_ref` extension (analogous to
`cdxml_ref`) and a field-by-field mapping in
[`docs/3dblox_interop.md`](docs/3dblox_interop.md), which also defines the
conventions a mechanical (lossy) `.chiplet` -> 3Dblox export would follow. On
openness the models differ: 3Dblox has historically been distributed under
click-through terms, and P3537, as an IEEE-SA project, follows the IEEE
patent policy (RAND commitments), while everything in this repository is
Apache-2.0 with an explicit patent non-assertion (below).

`.chiplet` aims to serve as an open interchange for the **mask-level
physical-assembly + DRC** layer: downstream of and interoperable with the
part-description standards (CDXML / JEP30), complementary at the assembly
layer to the P&R-level descriptions (3Dblox / P3537), and not a competing
"chiplet exchange format" writ large.

## License and implementer rights

All artifacts in this repository are licensed under the **Apache License 2.0**
(`LICENSE`; per-file declarations follow the [REUSE](https://reuse.software) convention
in `.reuse/dep5`).

In addition, IHP makes the following explicit grant regarding the **formats** described
here:

> Anyone may implement readers, writers, converters, or any other tools that consume or
> produce files in these formats, under **any license** of their choosing (open-source
> or proprietary, royalty-free). The format definitions are intended as open,
> royalty-free interchange specifications. **IHP asserts no patents over the formats
> themselves** and will not use patents it may hold to prevent conformant
> implementations. Apache-2.0's patent grant (Section 3) applies to the material in this
> repository.

This means a GPL constraint only ever comes from a *tool* you choose to build on (e.g.
embedding KiCad or KLayout), never from these formats.

## Status

Single source of truth for the formats. Chiplet Studio already consumes the C++ reference
library from here (vendored under its `src/formats/chiplet_format_io/`) and points its spec
doc back here; the remaining tool repositories will likewise migrate to consume their
schemas and spec docs from here. Until that migration completes, some copies may still exist
inside those repositories and this repository is authoritative.

Versioning is per-artifact (e.g. the `.chiplet` format carries `format_version`, the
manifests carry `version`/`schema_version`). Changes follow semantic-versioning intent:
additive fields are minor; removals/renames are major.
