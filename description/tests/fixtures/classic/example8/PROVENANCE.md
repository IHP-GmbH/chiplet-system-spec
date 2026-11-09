# Provenance

`system.chiplet` and the two die folders under `inputs/dies/` (their
`die-contract.json` and `pins.json`, the files a die folder is read from) are
the packaging flow's `two_die_echo_shifted_gf180` example, adk-examples commit
6881572. The layouts and build records are left out: nothing here reads them.

`project.chiplet` is that example's file with what a classic sheet draws taken
out (dies, buses, rails, external edges, the carrier's size): the project file
`tests/test_classic_import.py` reads its classic sheets into. The test draws the
example's system on a sheet and expects the file it writes to connect what
`system.chiplet` connects.
