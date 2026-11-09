# Provenance

`echo_shifted.chiplet` and `mixed_gf180.chiplet` are the packaging flow's
`two_die_echo_shifted_gf180` and `mixed_gf180` example sources, with comments,
a trailing comment on an item and blank lines added, so the writer golden has
bytes to keep in every block and in the `flow` block.

The `*.golden.chiplet` files are what `tests/test_source_patch.py` expects the
patcher to write from them. Each was checked by hand against its diff when it was
frozen; a change to one is a change of the writer's output.
