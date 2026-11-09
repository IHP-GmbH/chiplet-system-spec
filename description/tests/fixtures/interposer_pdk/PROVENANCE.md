# Interposer PDK stack descriptor

`libs.tech/intm4tm2_stack.json` is a byte copy of the same path in the IntM4TM2 interposer PDK
(OpenIntM4TM2, Apache-2.0), identified by its sha256
46a5879540ece79cec3267975a29af51d617e81fb77e358dfd5af67452d24117.

The examples take their mount plane, `beol.attachment_surface_z_um`, from
`$INTERPOSER_PDK_ROOT/libs.tech/intm4tm2_stack.json`. `tests/conftest.py` points
`INTERPOSER_PDK_ROOT` here while it builds them, so the suite needs no PDK checkout. The golden
`ihp_two_die_route.chiplet` carries the plane this copy gives.

Refresh it only as a byte copy of a PDK release, and update the sha256 above with it.
