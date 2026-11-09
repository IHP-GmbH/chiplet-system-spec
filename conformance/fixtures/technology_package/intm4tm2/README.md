# IntM4TM2 carrier manifest

A copy of the technology manifest of the IHP 130-nm IntM4TM2 aluminium BEOL
interposer, with the members the validator reads copied byte for byte: the
manifest, both routing descriptors, the physical stack and the layer sheet.
Every other member is a 0-byte placeholder, because the validator only checks
that it exists and is a file or a folder: the KiCad template, the DRC entry
(`intm4tm2.drc`), its runner (`run_drc.py`) and the density deck, the fill
halo tool and the `.lyt`. They are not the PDK's files; look for those in the
interposer repository. `PROVENANCE.md` names the source release.

The copy proves that the reference carrier, as it was at that release, passes
sets A and B (the capability sets, `x-capability-set` in
`schemas/technology_package.schema.json`), and gives the refusal cases a real
package to change one thing in. It does not follow the interposer repository
by itself: the manifest that ships is validated where it ships.

To refresh it, copy the members listed in `provenance.json` from a new
interposer release and record that release and the new digests there. The set C
expectation in `conformance/test_technology_package_intm4tm2.py` lists what
the carrier does not describe yet (EM limits, layer and via resistances), so a
release that adds them turns that test red on purpose: update the expectation
with the copy.
